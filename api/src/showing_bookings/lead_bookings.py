"""Showings agreed in a Zillow lead's email thread, booked, moved and cancelled by code.

The lead agent has no tools: it returns an outcome, and each outcome says
where tour scheduling stands (``TourState``: status, start, virtual) and what
the thread says about the lead (``LeadFacts``: name, phone, unit). This module
turns those decisions into bookings, only while the setting
``lead_agent_uses_open_times`` is on. With it off, or unreadable, every
function here does nothing and answers None.

A person sends a reply that changes a booking, unless the lead agent's
settings let that kind go automatically (``zillow_leads.settings``: a new
booking, a move, a cancellation, each its own setting, all off until saved).
The automatic send holds any other reply for which ``plan_reply`` answers a
change (``Plan.person_sends``), and one that books or moves without stating
the booked time in the policy's form (``Plan.time_not_stated``). When a person
approves a reply (``handle.apply_approval``), or the automatic send is about
to send one (``auto_reply.maybe_send_reply``), ``prepare_reply`` runs just
before the send:

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
- A moved showing keeps the lead's details. When a place being moved has them
  (name, mobile, email, the consent to texts and the requirement ticks) and its
  number is not known to be wrong, the new place takes them over as they are
  (``_moving``), the consent record included: consent was given then. Its
  confirmation text, worded as a move, and the invite behind it are queued too,
  and nothing reaches the lead before the reply's send is settled
  (``delivery_checks.reply_unsettled``). The email told the lead, so the place
  stands if the number is refused, and is marked for the team. Otherwise the
  new place is provisional, as above.
- A refused time (gone, full, the unit off the market, the thread already
  booked there) answers ``Prepared.refused``: the caller does not send, and
  puts the reason on the reply's card for a person.
- A reply whose tour is ``cancelled`` cancels the thread's places at that
  unit, after the send.

Then ``after_send`` settles it with what the send did:

- ``sent``: the held notices go now; the old time of a move, or the places a
  cancellation names, are cancelled (reason ``prospect``). The lead is texted
  about a cancellation, never about the old time of a move.
- ``failed`` (Gmail refused it, so nothing went out): the place this reply
  booked is cancelled at once (``reply_not_sent``), and its notices, still
  held, are stopped before they go. Nothing else changes.
- ``uncertain``: the place stands, marked ``reply_uncertain`` on the
  Showings section (and ``lead_reply_uncertain_at``), and its held notices go
  when their hold ends; the old time of a move, or a cancellation, goes ahead
  as for ``sent``.

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
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import bookings, cancelling, delivery_checks, residents
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import RequirementConfirmation, normalize_us_mobile
from api.src.showing_bookings.settings import read_settings
from api.src.zillow_leads.agent import LeadFacts, TourState

# How long a booking's host event and residents' text (and the lead's own text,
# for a move that kept their details) wait for the reply's send. Gmail answers in
# seconds; the hold only has to outlast it.
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
# The lead changed the place being moved while the new one was being booked.
PLACE_CHANGED = "The lead's showing changed while this was being booked. Approve it again."


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
    # Whether that place has the lead's details, kept from the one it replaces.
    details_kept: bool = False
    # Places to cancel once the reply is sent: a move's old time, or a cancellation.
    to_cancel: list[int] = field(default_factory=list)
    refusal_code: str | None = None
    refusal_message: str | None = None
    # The conversation whose reply this is.
    conversation_id: str | None = None
    # The place was booked by an earlier attempt at this reply that stopped
    # before its send was known, and found again now.
    recovered: bool = False
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

    def unsent_note(self) -> str | None:
        """One line for the team when this reply's send failed or is not known,
        once ``after_send`` has settled it. None when nothing needs saying."""
        booked_here = self.action in ("booked", "moved")
        if self.sent == "failed":
            if booked_here and self.cancelled:
                return "The reply was not sent, so the showing booked for it was cancelled."
            if self.action == "cancel":
                return "The reply was not sent, so the showing was not cancelled."
        if self.sent == "uncertain":
            if booked_here:
                return (
                    "The reply may not have gone out. The showing stays booked, "
                    "marked on the Showings section."
                )
            if self.action == "cancel":
                return "The reply may not have gone out. The showing was cancelled."
        return None

    def record(self) -> dict[str, Any]:
        """What the conversation keeps, under ``showing_booking``."""
        return {
            "action": self.action,
            "unit": self.unit,
            "start": self.start.isoformat() if self.start else None,
            "showing_id": self.showing_id,
            "attendee_id": self.attendee_id,
            "details_kept": self.details_kept,
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
    uncertain (``lead_reply_uncertain_at``: kept and marked). It is this
    conversation's unsent booking when this conversation booked it
    (``lead_conversation_id``), its reply was never sent nor may have been, and
    the lead has not completed it through their link
    (``delivery_checks.reply_unsettled``). A place the lead booked through their
    link, or one an earlier reply booked and sent, never is.
    """
    async with AsyncSessionFactory() as session:
        place = await session.get(ShowingAttendee, attendee_id)
    return (
        place is not None
        and place.lead_conversation_id == conversation_id
        and delivery_checks.reply_unsettled(place)
    )


async def _recovered(attendee_id: int) -> dict[str, Any]:
    """A recovered place's showing, the residents' text of that showing, if any, and
    whether the place kept the lead's details, as ``Prepared`` holds them.

    The residents' text is released with the place's own notices once the
    reply is sent (``release_notices`` moves only one still held past now).
    """
    async with AsyncSessionFactory() as session:
        place = await session.get_one(ShowingAttendee, attendee_id)
        residents_notice_id = await session.scalar(
            select(ShowingNotice.id).where(
                ShowingNotice.showing_id == place.showing_id,
                ShowingNotice.kind == residents.NEW_SHOWING_KIND,
            )
        )
    return {
        "showing_id": place.showing_id,
        "attendee_id": attendee_id,
        "residents_notice_id": residents_notice_id,
        "details_kept": place.texts_consent_at is not None,
    }


@dataclass(frozen=True)
class Kept:
    """What a lead gave on the place being moved, which the new place takes over as it is."""

    name: str
    mobile: str
    email: str
    consent_at: datetime
    consent_version: str | None
    consent_page: str | None
    confirmations: tuple[RequirementConfirmation, ...]


@dataclass(frozen=True)
class Moving:
    """The places a move replaces, as they stand: their details, and their starts."""

    kept: Kept | None
    # The starts of the ones the lead knows of, for the new place's text.
    replaces: tuple[datetime, ...]


async def _moving(session: AsyncSession, attendee_ids: list[int]) -> Moving:
    """What the new place of a move takes over from the old ones, as things are now.

    The details are kept only from a place that has them (a mobile, an email
    and recorded consent to texts) whose number is not known to be wrong. Of
    several, the place with the latest consent. A place whose own reply is not
    settled, which the lead never heard of, gives neither details nor a start.
    """
    if not attendee_ids:
        return Moving(None, ())
    rows = (
        await session.execute(
            select(ShowingAttendee, Showing.starts_at)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(ShowingAttendee.id.in_(attendee_ids), ShowingAttendee.status == "booked")
            .order_by(Showing.starts_at, ShowingAttendee.id)
        )
    ).all()
    known = [(place, start) for place, start in rows if not delivery_checks.reply_unsettled(place)]
    with_details = [
        place
        for place, _ in known
        if place.email and place.texts_consent_at is not None and delivery_checks.can_text(place)
    ]
    latest = max(with_details, key=lambda place: (place.texts_consent_at, place.id), default=None)
    return Moving(
        kept=None
        if latest is None
        else Kept(
            name=latest.name,
            mobile=latest.mobile,
            email=latest.email,
            consent_at=latest.texts_consent_at,
            consent_version=latest.texts_consent_version,
            consent_page=latest.texts_consent_page,
            confirmations=tuple(
                RequirementConfirmation.model_validate(item)
                for item in latest.requirement_confirmations
            ),
        ),
        replaces=tuple(start for _, start in known),
    )


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
        return f"This reply {self._verb()} a showing, so a person sends it."

    def time_not_stated(self, timezone: str) -> str:
        """Why the automatic send holds a reply that books or moves without
        giving the time code would book, in the policy's form."""
        return (
            f"This reply {self._verb()} a showing but does not give the time as "
            f"{_when(self.start, timezone)}, so a person sends it."
        )

    def if_unsent(self, timezone: str) -> str:
        """For the card left while an automatic send is under way: what the
        reply does to the showing, and what to check if it never went out."""
        if self.action == "book":
            return (
                f"This reply books unit {self.unit} for {_short(self.start, timezone)}. "
                "If it did not go out, cancel that showing on the Booking settings tab."
            )
        if self.action == "move":
            return (
                f"This reply moves their showing of {self.unit} to "
                f"{_short(self.start, timezone)}. If it did not go out, check their "
                "showings on the Booking settings tab."
            )
        return (
            f"This reply cancels their showing of {self.unit}. If it went out and the "
            "showing is still booked, cancel it on the Booking settings tab."
        )

    def _verb(self) -> str:
        return {"book": "books", "move": "moves", "cancel": "cancels"}[self.action]

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
        return Prepared(
            "moved" if others else "booked",
            want.unit,
            want.start,
            to_cancel=others,
            conversation_id=conversation_id,
            recovered=True,
            **await _recovered(same[0]),
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
            listing, rules = await bookings._listing(session, want.unit, now, only_open=True)
            moving = await _moving(session, others)

        async def unchanged(session: AsyncSession) -> None:
            # Under the booking locks: what the new place takes over is what the
            # old one holds now, should the lead have changed it meanwhile.
            if await _moving(session, others) != moving:
                raise bookings.Refused(409, "place_changed", PLACE_CHANGED)

        placed = await bookings.place_booking(
            listing,
            rules,
            want.start,
            _person(lead, thread_id, conversation_id, moving.kept),
            now=now,
            checks=unchanged,
            hold_notices_until=now + HOLD,
            replaces=moving.replaces if moving.kept is not None else (),
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
        details_kept=moving.kept is not None,
    )
    return Prepared(
        "moved" if others else "booked",
        listing.unit,
        want.start,
        showing_id=placed.showing.id,
        attendee_id=placed.attendee.id,
        residents_notice_id=placed.residents_notice_id,
        details_kept=moving.kept is not None,
        to_cancel=others,
        conversation_id=conversation_id,
    )


def _person(
    lead: LeadFacts, thread_id: str, conversation_id: str, kept: Kept | None
) -> bookings.Person:
    """Who the place is for: with the details kept from the place being moved, or
    provisional, with what the thread says."""
    if kept is None:
        return bookings.Person(
            name=_name(lead.name),
            source="lead_agent",
            mobile=normalize_us_mobile(lead.phone or ""),
            zillow_thread_id=thread_id,
            lead_conversation_id=conversation_id,
        )
    return bookings.Person(
        name=kept.name,
        source="lead_agent",
        mobile=kept.mobile,
        email=kept.email,
        texts_accepted=True,
        consent_page=kept.consent_page,
        consent_at=kept.consent_at,
        consent_version=kept.consent_version,
        confirmations=kept.confirmations,
        zillow_thread_id=thread_id,
        lead_conversation_id=conversation_id,
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
                await _uncertain(prepared.attendee_id, now=now)
            if prepared.action == "already_booked" and result == "sent":
                await _confirmed_by(prepared.attendee_id, prepared.conversation_id)
            # The old place of a move is cancelled with no text to the lead saying so.
            moved = prepared.action != "cancel"
            for attendee_id in prepared.to_cancel:
                if await cancelling.cancel_attendee(
                    attendee_id, reason="prospect", now=now, texts=texts, moved=moved
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


async def _uncertain(attendee_id: int, *, now: datetime) -> None:
    """The reply that booked a place may or may not have gone out: the place stands,
    marked for the team, and is never cleaned up as unsent. Its own column keeps
    that when a confirmation text that did not reach the number replaces the mark."""
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id, ShowingAttendee.status == "booked")
            .values(attention_reason=REPLY_UNCERTAIN, lead_reply_uncertain_at=now)
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

    For a rejected reply. Leaves a place whose reply is uncertain (it may have
    gone out) and one the lead has since completed through their link. Nothing
    at all while the switch is off. Returns what it cancelled.
    """
    if not await enabled():
        return []
    async with AsyncSessionFactory() as session:
        places = await session.scalars(
            select(ShowingAttendee)
            .where(
                ShowingAttendee.lead_conversation_id == conversation_id,
                ShowingAttendee.status == "booked",
            )
            .order_by(ShowingAttendee.id)
        )
        ids = [place.id for place in places if delivery_checks.reply_unsettled(place)]
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
