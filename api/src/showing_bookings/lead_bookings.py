"""Showings agreed in a Zillow lead's email thread, booked, moved and cancelled by code.

The lead agent has no tools: it returns an outcome, and each outcome says
where tour scheduling stands (``TourState``: status, start, virtual) and what
the thread says about the lead (``LeadFacts``: name, phone, unit). This module
turns those decisions into bookings, only while the setting
``lead_agent_uses_open_times`` is on. With it off, or unreadable, every
function here does nothing and answers None.

Only a person sends a reply that changes a booking: the automatic send holds
any reply for which ``plan_reply`` answers a change (``Plan.person_sends``),
until a check that the reply's words name the booked time exists, with an
eval run. When a person approves a reply (``handle.apply_approval``),
``prepare_reply`` runs just before the send:

- A reply whose tour is ``confirmed`` or ``rescheduled``, in person, with a
  start and a unit the case lists, books the lead into that showing through
  ``bookings.place_booking`` (source ``lead_agent``), which re-checks the time
  under the shared booking lock. The place is provisional: the thread's name
  (or ``PLACEHOLDER_NAME``), the thread id, the conversation id, no email and
  no consent to texts. A phone number from the thread is saved as the mobile
  so the host can see it; it is never texted. The host's event and the
  residents' text are queued as for a team booking without texts, but held
  for ``HOLD`` so they cannot go out before the reply's send is known.
- If the thread already holds a booked place at that unit and start (the lead
  used their link, or an earlier reply booked it), nothing new is booked.
  Any other upcoming place the thread holds at that unit is the old time of a
  move, cancelled only after the reply is sent.
- A refused time (gone, full, the unit off the market, the thread already
  booked there) answers ``Prepared.refused``: the caller does not send, and
  puts the reason on the reply's card for a person.
- A reply whose tour is ``cancelled`` cancels the thread's places at that
  unit, after the send.

Then ``after_send`` settles it with what the send did:

- ``sent``: the held notices go now; the old time of a move, or the places a
  cancellation names, are cancelled (reason ``prospect``).
- ``failed`` (Gmail refused it, so nothing went out): the place this reply
  booked is cancelled at once (``reply_not_sent``), and its notices, still
  held, are stopped before they go. Nothing else changes.
- ``uncertain``: the place stands, marked ``reply_uncertain`` on the
  Showings section; the old
  time of a move, or a cancellation, goes ahead as for ``sent``.

Before anyone approves, the send card states what sending does
(``plan_reply``, ``Plan.note``): "Sending this books unit 320-03 for Wed,
Apr 14 at 4:00 PM.", or moves, or cancels. Approving the draft unchanged
applies exactly that; if the thread's showings changed so that sending would
now do something else, the card is held with the new statement. Approving
edited words applies no booking change at all (``Plan.not_applied`` says what
was not done). A place this conversation booked for its reply and never sent
(a process stopped in between) is still that reply's booking: approving again
recovers it, and a definite send failure cancels it.

A reply that is rejected cancels any place its conversation booked
(``cancel_unsent``), which covers a process that stopped between the booking
and the send. A ``no_reply`` decision that records the tour as cancelled
cancels the thread's places at the unit at once (``cancel_from_decision``);
an escalation cancels nothing, and its card says what is still booked.

Every step is safe to repeat: a booking finds the place already held, a
cancellation finds it already cancelled.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import logfire
from sqlalchemy import func, select, update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import bookings, cancelling, residents
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import normalize_us_mobile
from api.src.showing_bookings.settings import read_settings
from api.src.zillow_leads.agent import LeadFacts, TourState

# How long a booking's host event and residents' text wait for the reply's
# send. Gmail answers in seconds; the hold only has to outlast it.
HOLD = timedelta(minutes=5)
# The attendee's name when the thread gives none.
PLACEHOLDER_NAME = "Zillow lead"
# On an attendee whose booking reply may or may not have gone out.
REPLY_UNCERTAIN = "reply_uncertain"
SendResult = Literal["sent", "failed", "uncertain"]
# How every note on a send card about what sending does begins (``Plan.note``).
PLAN_PREFIX = "Sending this "
# Added above a fresh ``Plan.note`` when the thread's showings changed after
# the card was written, so approving it would now do something else.
PLAN_CHANGED = "The thread's showings changed since this card was written; approve again to send it with this change."


async def enabled() -> bool:
    """Whether the lead agent's switch is on. False whenever it cannot be read."""
    try:
        async with AsyncSessionFactory() as session:
            return (await read_settings(session)).lead_agent_uses_open_times
    except Exception:
        logfire.warn("lead bookings: booking settings unreadable, nothing booked")
        return False


@dataclass(frozen=True)
class Want:
    """What a decision asks of the booking service."""

    action: Literal["book", "cancel"]
    unit: str
    start: datetime | None = None


def wanted(tour: TourState, lead: LeadFacts, listed_units: list[str]) -> Want | None:
    """What a sent reply's tour state asks for, or None.

    Book: confirmed or rescheduled, in person, with a start and a unit the
    case lists (spelled as the listing spells it). Cancel: cancelled, with a
    unit named; the unit need not be listed any more. Anything else: nothing.
    """
    named = (lead.unit or "").strip()
    if not named:
        return None
    if tour.status == "cancelled":
        return Want("cancel", named)
    if tour.status not in ("confirmed", "rescheduled") or tour.virtual:
        return None
    if tour.start is None or tour.start.tzinfo is None:
        return None
    unit = next((item for item in listed_units if item.casefold() == named.casefold()), None)
    if unit is None:
        return None
    return Want("book", unit, tour.start)


@dataclass
class Prepared:
    """What a reply's booking did before the send, and what is left for after it."""

    action: Literal["booked", "already_booked", "moved", "cancel", "refused"]
    unit: str
    start: datetime | None = None
    # The place this reply booked, when it booked one.
    showing_id: int | None = None
    attendee_id: int | None = None
    residents_notice_id: int | None = None
    # Places to cancel once the reply is sent: a move's old time, or a cancellation.
    to_cancel: list[int] = field(default_factory=list)
    refusal_code: str | None = None
    refusal_message: str | None = None
    # The conversation whose reply this is.
    conversation_id: str | None = None
    # Filled in by ``after_send``.
    sent: SendResult | None = None
    cancelled: list[int] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.action == "refused"

    def problem(self) -> str:
        """The line for the reply's card when the time was refused."""
        return f"Not sent: the showing could not be booked. {self.refusal_message}".strip()

    def note(self, timezone: str) -> str:
        """One line for the team about this reply's booking."""
        when = _when(self.start, timezone) if self.start else ""
        if self.action == "booked":
            return f"Showing booked with this reply: {self.unit}, {when}."
        if self.action == "moved":
            return f"Showing moved with this reply to {self.unit}, {when}."
        if self.action == "already_booked":
            return f"Already booked: {self.unit}, {when}."
        if self.action == "cancel":
            return f"This reply cancels the lead's showing at {self.unit}."
        return self.problem()

    def record(self) -> dict[str, Any]:
        """What the conversation keeps, under ``showing_booking``."""
        return {
            "action": self.action,
            "unit": self.unit,
            "start": self.start.isoformat() if self.start else None,
            "showing_id": self.showing_id,
            "attendee_id": self.attendee_id,
            "to_cancel": self.to_cancel,
            "refusal": self.refusal_code,
            "sent": self.sent,
            "cancelled": self.cancelled,
            "at": datetime.now(UTC).isoformat(),
        }


def _when(start: datetime, timezone: str) -> str:
    local = start.astimezone(ZoneInfo(timezone))
    return f"{local:%A, %B} {local.day} at {local.strftime('%I:%M %p').lstrip('0')}"


async def _thread_places(thread_id: str, unit: str, now: datetime) -> list[tuple[int, datetime]]:
    """The thread's upcoming booked places at ``unit``: (attendee id, start)."""
    async with AsyncSessionFactory() as session:
        rows = await session.execute(
            select(ShowingAttendee.id, Showing.starts_at)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(
                ShowingAttendee.zillow_thread_id == thread_id,
                ShowingAttendee.status == "booked",
                Showing.status == "scheduled",
                Showing.starts_at > now,
                func.lower(Showing.unit) == unit.casefold(),
            )
            .order_by(Showing.starts_at, ShowingAttendee.id)
        )
        return [(attendee_id, start) for attendee_id, start in rows]


async def _unsent_booking_of(attendee_id: int, conversation_id: str) -> bool:
    """Whether a place is an unsent booking of this conversation's reply.

    A place is in one of three states: never sent (cleaned up), sent
    (``lead_reply_sent_at``: kept, whatever happens to the card afterwards) or
    uncertain (``reply_uncertain``: kept and marked). It is this
    conversation's unsent booking when this conversation booked it
    (``lead_conversation_id``), its reply was never sent nor may have been, and
    the lead has not completed it through their link. A place the lead booked
    through their link, or one an earlier reply booked and sent, never is.
    """
    async with AsyncSessionFactory() as session:
        row = (
            await session.execute(
                select(
                    ShowingAttendee.lead_conversation_id,
                    ShowingAttendee.texts_consent_at,
                    ShowingAttendee.attention_reason,
                    ShowingAttendee.lead_reply_sent_at,
                ).where(ShowingAttendee.id == attendee_id)
            )
        ).first()
    if row is None:
        return False
    owner, consent, attention, sent_at = row
    return (
        owner == conversation_id
        and consent is None
        and sent_at is None
        and attention != REPLY_UNCERTAIN
    )


async def _booking_rows(attendee_id: int) -> tuple[int, int | None]:
    """A recovered place's showing, and the residents' text of that showing, if any.

    The residents' text is released with the place's own notices once the
    reply is sent (``release_notices`` moves only one still held past now).
    """
    async with AsyncSessionFactory() as session:
        showing_id = await session.scalar(
            select(ShowingAttendee.showing_id).where(ShowingAttendee.id == attendee_id)
        )
        residents_notice_id = await session.scalar(
            select(ShowingNotice.id).where(
                ShowingNotice.showing_id == showing_id,
                ShowingNotice.kind == residents.NEW_SHOWING_KIND,
            )
        )
    return showing_id, residents_notice_id


@dataclass(frozen=True)
class Plan:
    """What sending a reply would do to the thread's showings, said before anyone approves."""

    action: Literal["book", "move", "cancel"]
    unit: str
    start: datetime | None
    # The thread's other upcoming times at the unit: a move's old time, or what a cancellation cancels.
    old: tuple[datetime, ...] = ()

    def note(self, timezone: str) -> str:
        """The line on the send card."""
        old = " and ".join(_short(start, timezone) for start in self.old)
        if self.action == "book":
            return f"Sending this books unit {self.unit} for {_short(self.start, timezone)}."
        if self.action == "move":
            return (
                f"Sending this moves their showing of {self.unit} from {old} "
                f"to {_short(self.start, timezone)}."
            )
        return f"Sending this cancels their showing of {self.unit} on {old}."

    def person_sends(self) -> str:
        """Why the automatic send holds this reply for a person."""
        verb = {"book": "books", "move": "moves", "cancel": "cancels"}[self.action]
        return f"This reply {verb} a showing, so a person sends it."

    def not_applied(self) -> str:
        """The result line when a person sent edited words, so nothing was applied."""
        if self.action == "book":
            return (
                "The reply was edited, so the showing was not booked automatically. "
                "Add it on the Booking settings tab if it still applies."
            )
        if self.action == "move":
            return (
                "The reply was edited, so the showing was not moved automatically. "
                "Change it on the Booking settings tab if it still applies."
            )
        return (
            "The reply was edited, so the showing was not cancelled automatically. "
            "Cancel it on the Booking settings tab if it still applies."
        )


def _short(start: datetime, timezone: str) -> str:
    """ "Wed, Apr 14 at 4:00 PM"."""
    local = start.astimezone(ZoneInfo(timezone))
    return f"{local:%a, %b} {local.day} at {local.strftime('%I:%M %p').lstrip('0')}"


async def plan_reply(
    *,
    conversation_id: str,
    thread_id: str,
    lead: LeadFacts,
    tour: TourState,
    listed_units: list[str],
    now: datetime,
) -> Plan | None:
    """What sending a reply would book, move or cancel, without changing anything.

    None with the switch off, when the decision asks for nothing, or when it
    would change nothing (the time is already booked for the thread). A place
    this conversation booked for the reply and never sent counts as not booked
    yet. Never raises: an unreadable thread plans nothing.
    """
    want = wanted(tour, lead, listed_units)
    if want is None or not await enabled():
        return None
    try:
        places = await _thread_places(thread_id, want.unit, now)
        mine = [
            attendee_id
            for attendee_id, _ in places
            if await _unsent_booking_of(attendee_id, conversation_id)
        ]
    except Exception:
        logfire.exception("lead bookings: the thread's showings unreadable, nothing planned")
        return None
    if want.action == "cancel":
        old = tuple(start for _, start in places)
        return Plan("cancel", want.unit, None, old) if old else None
    others = tuple(start for _, start in places if start != want.start)
    same = [attendee_id for attendee_id, start in places if start == want.start]
    if same and not set(same) <= set(mine) and not others:
        return None  # already booked, nothing changes
    return Plan("move" if others else "book", want.unit, want.start, others)


async def prepare_reply(
    *,
    conversation_id: str,
    thread_id: str,
    lead: LeadFacts,
    tour: TourState,
    listed_units: list[str],
    now: datetime,
) -> Prepared | None:
    """Book what a reply about to be sent confirms. None: nothing to do, or the switch is off.

    Call it only once the reply will be sent, right before the send. A
    refusal (``Prepared.refused``) means: do not send.
    """
    want = wanted(tour, lead, listed_units)
    if want is None or not await enabled():
        return None
    try:
        return await _prepare(want, conversation_id, thread_id, lead, now)
    except Exception:
        # Unknown is not booked: the reply waits for a person, as for a refusal.
        logfire.exception(
            "lead bookings: booking failed, reply held", conversation_id=conversation_id
        )
        return Prepared(
            "refused",
            want.unit,
            want.start,
            refusal_code="unavailable",
            refusal_message="The booking service could not be reached.",
            conversation_id=conversation_id,
        )


async def _prepare(
    want: Want, conversation_id: str, thread_id: str, lead: LeadFacts, now: datetime
) -> Prepared | None:
    places = await _thread_places(thread_id, want.unit, now)
    if want.action == "cancel":
        if not places:
            return None
        return Prepared(
            "cancel",
            want.unit,
            to_cancel=[attendee_id for attendee_id, _ in places],
            conversation_id=conversation_id,
        )

    same = [attendee_id for attendee_id, start in places if start == want.start]
    others = [attendee_id for attendee_id, start in places if start != want.start]
    if same and await _unsent_booking_of(same[0], conversation_id):
        # This conversation booked the place for this very reply, and a process
        # stopped before it was sent. It is still this reply's booking: kept
        # when the reply is sent, cancelled if Gmail refuses it.
        logfire.info(
            "lead bookings: an unsent booking of this reply recovered",
            conversation_id=conversation_id,
            attendee_id=same[0],
        )
        showing_id, residents_notice_id = await _booking_rows(same[0])
        return Prepared(
            "moved" if others else "booked",
            want.unit,
            want.start,
            showing_id=showing_id,
            attendee_id=same[0],
            residents_notice_id=residents_notice_id,
            to_cancel=others,
            conversation_id=conversation_id,
        )
    if same:
        return Prepared(
            "already_booked",
            want.unit,
            want.start,
            attendee_id=same[0],
            to_cancel=others,
            conversation_id=conversation_id,
        )
    try:
        async with AsyncSessionFactory() as session:
            listing, rules = await bookings._listing(session, want.unit, now)
        placed = await bookings.place_booking(
            listing,
            rules,
            want.start,
            bookings.Person(
                name=_name(lead.name),
                source="lead_agent",
                mobile=normalize_us_mobile(lead.phone or ""),
                zillow_thread_id=thread_id,
                lead_conversation_id=conversation_id,
            ),
            now=now,
            hold_notices_until=now + HOLD,
        )
    except bookings.Refused as refused:
        logfire.info(
            "lead bookings: time refused, reply held",
            conversation_id=conversation_id,
            code=refused.code,
        )
        return Prepared(
            "refused",
            want.unit,
            want.start,
            refusal_code=refused.code,
            refusal_message=refused.message,
            conversation_id=conversation_id,
        )
    logfire.info(
        "lead bookings: booked before the reply",
        conversation_id=conversation_id,
        showing_id=placed.showing.id,
        attendee_id=placed.attendee.id,
        moves=len(others),
    )
    return Prepared(
        "moved" if others else "booked",
        listing.unit,
        want.start,
        showing_id=placed.showing.id,
        attendee_id=placed.attendee.id,
        residents_notice_id=placed.residents_notice_id,
        to_cancel=others,
        conversation_id=conversation_id,
    )


def _name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())[:100]
    return cleaned or PLACEHOLDER_NAME


async def after_send(
    prepared: Prepared,
    result: SendResult,
    *,
    now: datetime,
    texts: TextSender | None = None,
) -> Prepared:
    """Settle a prepared booking once the send's result is known. Never raises."""
    cancelled: list[int] = []
    booked_here = prepared.action in ("booked", "moved") and prepared.attendee_id is not None
    try:
        if result == "failed":
            if booked_here:
                await cancelling.cancel_attendee(
                    prepared.attendee_id, reason="reply_not_sent", now=now, texts=texts
                )
                cancelled.append(prepared.attendee_id)
        else:
            if booked_here and result == "sent":
                await _sent(prepared.attendee_id, prepared.residents_notice_id, now=now)
            if booked_here and result == "uncertain":
                await _mark(prepared.attendee_id, REPLY_UNCERTAIN)
            if prepared.action == "already_booked" and result == "sent":
                await _confirmed_by(prepared.attendee_id, prepared.conversation_id)
            for attendee_id in prepared.to_cancel:
                if await cancelling.cancel_attendee(
                    attendee_id, reason="prospect", now=now, texts=texts
                ):
                    cancelled.append(attendee_id)
    except Exception:
        # What is left stands; the team sees it on the Showings section.
        logfire.exception("lead bookings: settling after the send failed")
    logfire.info(
        "lead bookings: settled after the send",
        result=result,
        action=prepared.action,
        cancelled=len(cancelled),
    )
    return replace(prepared, sent=result, cancelled=cancelled)


async def _sent(attendee_id: int, residents_notice_id: int | None, *, now: datetime) -> None:
    """The reply that booked a place was sent: say so on the place, and let its
    held notices go, in one transaction. From here the place is never cleaned
    up as unsent, whatever happens to the reply's card afterwards."""
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id)
            .values(lead_reply_sent_at=now)
        )
        await bookings.release_notices(session, attendee_id, residents_notice_id, now=now)


async def _mark(attendee_id: int, reason: str) -> None:
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id, ShowingAttendee.status == "booked")
            .values(attention_reason=reason)
        )


async def _confirmed_by(attendee_id: int | None, conversation_id: str | None) -> None:
    """A sent reply confirmed a place the lead agent booked earlier: it is now this
    conversation's, so rejecting an older, stale reply cannot cancel it, and a
    ``reply_uncertain`` mark is cleared."""
    if attendee_id is None or conversation_id is None:
        return
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee)
            .where(
                ShowingAttendee.id == attendee_id,
                ShowingAttendee.status == "booked",
                ShowingAttendee.lead_conversation_id.is_not(None),
            )
            .values(
                lead_conversation_id=conversation_id,
                lead_reply_sent_at=func.coalesce(
                    ShowingAttendee.lead_reply_sent_at, datetime.now(UTC)
                ),
            )
        )
        await session.execute(
            update(ShowingAttendee)
            .where(
                ShowingAttendee.id == attendee_id,
                ShowingAttendee.attention_reason == REPLY_UNCERTAIN,
            )
            .values(attention_reason=None)
        )


async def cancel_unsent(
    conversation_id: str, *, now: datetime, texts: TextSender | None = None
) -> list[int]:
    """Cancel the places a conversation booked for a reply that will not be sent.

    For a rejected reply. Leaves a place marked ``reply_uncertain`` (the reply
    may have gone out) and one the lead has since completed through their
    link. Nothing at all while the switch is off. Returns what it cancelled.
    """
    if not await enabled():
        return []
    async with AsyncSessionFactory() as session:
        ids = list(
            await session.scalars(
                select(ShowingAttendee.id).where(
                    ShowingAttendee.lead_conversation_id == conversation_id,
                    ShowingAttendee.status == "booked",
                    ShowingAttendee.texts_consent_at.is_(None),
                    ShowingAttendee.lead_reply_sent_at.is_(None),
                    ShowingAttendee.attention_reason.is_distinct_from(REPLY_UNCERTAIN),
                )
            )
        )
    cancelled = [
        attendee_id
        for attendee_id in ids
        if await cancelling.cancel_attendee(
            attendee_id, reason="reply_not_sent", now=now, texts=texts
        )
    ]
    if cancelled:
        logfire.info(
            "lead bookings: places of a rejected reply cancelled",
            conversation_id=conversation_id,
            cancelled=len(cancelled),
        )
    return cancelled


async def cancel_from_decision(
    *,
    thread_id: str,
    lead: LeadFacts,
    tour: TourState,
    now: datetime,
    texts: TextSender | None = None,
) -> Prepared | None:
    """A ``no_reply`` decision that records the tour as cancelled: cancel at once.

    Nothing waits for a send, so the thread's upcoming places at the unit the
    decision names are cancelled now, with reason ``prospect``.
    """
    if tour.status != "cancelled" or not (lead.unit or "").strip() or not await enabled():
        return None
    unit = lead.unit.strip()
    try:
        places = await _thread_places(thread_id, unit, now)
    except Exception:
        logfire.exception("lead bookings: the thread's showings unreadable, nothing cancelled")
        return None
    if not places:
        return None
    prepared = Prepared("cancel", unit, to_cancel=[attendee_id for attendee_id, _ in places])
    return await after_send(prepared, "sent", now=now, texts=texts)


async def still_booked(
    *, thread_id: str, lead: LeadFacts, tour: TourState, now: datetime
) -> list[datetime]:
    """For an escalation that records the tour as cancelled: the times still booked.

    The escalation cancels nothing; its card tells the person what is left.
    """
    if tour.status != "cancelled" or not (lead.unit or "").strip() or not await enabled():
        return []
    try:
        return [start for _, start in await _thread_places(thread_id, lead.unit.strip(), now)]
    except Exception:
        logfire.exception("lead bookings: the thread's showings unreadable")
        return []
