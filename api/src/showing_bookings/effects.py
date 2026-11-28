"""What a team booking or cancellation would do, said before anyone approves it.

A person approving a booking or a cancellation from a chat, rather than from
the Booking settings tab, must see what the click does: who is booked or
cancelled, where and when, and who is told (the host's calendar event, the
residents' text, the prospect's confirmation text and email, or cancellation
text and email) and what the send mode does with those messages. The general agent's
showing tools (``sernia_ai/tools/showing_tools.py``) put this text on their
approval card; any other caller that asks a person to approve a team action
should take its text from here too, so there is one wording.

Nothing here changes anything. ``preview_booking`` checks what
``team_actions.book_for_caller`` checks before it saves (the fields, the unit,
and the time by ``open_times.recheck`` with calendars read afresh, so the host
named is the host ``place_booking`` would pick), and ``preview_cancellation``
what ``cancelling.cancel_showing`` and ``remove_attendee`` check. Each raises
``bookings.Refused`` with a plain message for anything that would be refused.

A preview is read before the action takes its locks, so a booking, a join or a
calendar change can land in between. So the actions take the approved paragraph
as ``expected_effect`` (``team_actions.book_for_caller``,
``cancelling.cancel_showing``, ``cancelling.remove_attendee``), work the preview
out again inside their own transaction, under their locks, and raise
``EffectChanged`` with the paragraph as it now reads unless it is the same:
nobody is booked with another host, and nobody who joined later is cancelled or
texted, without a person agreeing to that.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import bookings, delivery_checks, host_texts, open_times, wording
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
    current_environment,
    read_settings,
    unit_is_live,
)
from api.src.showing_bookings.team_actions import TeamBookingRequest, _check_fields
from api.src.zillow_leads.models import ZillowEmailJob, ZillowLeadLink

# A preview's refusal for a time the unit cannot be booked for now.
NOT_OPEN = "not_open"
# The action's own check under its locks: what it would do no longer reads as approved.
EFFECT_CHANGED = "effect_changed"
CHANGED = "Nothing was done: what it would do changed since it was approved."


class EffectChanged(Refused):
    """A booking or cancellation no longer reads as the person approved it, found under
    the action's own locks before anything was written or sent (409 ``effect_changed``).

    ``effect`` is the paragraph as it reads now, for the person to approve again, or
    None when the action can no longer be done at all; the message then says why.
    """

    def __init__(self, effect: str | None, reason: str | None = None) -> None:
        message = f"{CHANGED} It would now read: {effect}" if effect else f"{CHANGED} {reason}"
        super().__init__(409, EFFECT_CHANGED, message)
        self.effect = effect


_NO_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def when_text(start: datetime, timezone: str) -> str:
    """ "Wed, Apr 14 at 4:00 PM", in the showing rules' time zone, as the texts say it."""
    return wording.when(
        wording.Visit(unit="", address="", starts_at=start, ends_at=start, timezone=timezone)
    )


def _place(unit: str, address: str) -> str:
    """ "12 Example St #A (unit 12-A)", as the texts name a unit."""
    return wording.place(
        wording.Visit(
            unit=unit, address=address, starts_at=_NO_TIME, ends_at=_NO_TIME, timezone="UTC"
        )
    )


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def sending_sentence(settings: ShowingBookingSettings, unit: str | None = None) -> str:
    """What the send mode does with what a booking or cancellation queues, in a
    sentence, in the order ``guard.decide`` asks."""
    later = "until a person sends it from Captured messages on the Booking settings tab"
    if settings.send_mode == "capture":
        return f"Bookings are in capture mode: nothing is sent {later}."
    if current_environment() != "production":
        if settings.send_mode == "test":
            return (
                "This is not production and bookings are in test mode: only people on the "
                f"test list are sent anything; the rest is captured {later}."
            )
        return f"This is not production: nothing is sent {later}."
    if settings.send_mode == "test":
        return (
            "Bookings are in test mode: only people on the test list are sent anything; the "
            f"rest is captured {later}."
        )
    if unit is None:
        return "Bookings are live: texts and calendar changes go out for the units ticked live."
    if not unit_is_live(settings, unit):
        return f"{unit} is not live: nothing about it is sent {later}."
    return "Bookings are live for this unit: these go out."


async def known_lead_thread(thread_id: str, session: AsyncSession | None = None) -> bool:
    """Whether a Zillow lead thread has this id: the lead agent has handled it."""
    if session is None:
        async with AsyncSessionFactory() as own:
            return await known_lead_thread(thread_id, own)
    found = await session.scalar(
        select(ZillowEmailJob.thread_id).where(ZillowEmailJob.thread_id == thread_id).limit(1)
    ) or await session.scalar(
        select(ZillowLeadLink.thread_id).where(ZillowLeadLink.thread_id == thread_id)
    )
    return found is not None


UNKNOWN_THREAD = (
    "No Zillow lead thread with id {thread!r} has reached the lead agent. Use the Gmail "
    "thread id of the lead's Zillow email, or leave zillow_thread_id empty."
)


# ---------------------------------------------------------------------------
# A booking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingPreview:
    """What ``book_for_caller`` would do with a request, as things are now."""

    sentence: str
    unit: str
    start: datetime
    name: str
    timezone: str
    # "new" or "join", and who would host: what ``place_booking``'s own re-check picks.
    kind: str
    host: str


def booking_sentence(
    *,
    name: str,
    listing: RentalListing,
    start: datetime,
    kind: str,
    host: str,
    mobile: str | None,
    email: str | None,
    texts: bool,
    thread_id: str | None,
    settings: ShowingBookingSettings,
    rules: ShowingRules,
    now: datetime,
) -> str:
    """The booking, and who is told, in one paragraph."""
    where = _place(listing.unit, listing.address)
    when = when_text(start, rules.timezone)
    if kind == "join":
        lines = [
            f"Books {name} into the showing of {where} already booked on {when}, hosted by {host}."
        ]
        host_told = f"{host}'s calendar event is updated"
    else:
        lines = [f"Books {name} into a new showing of {where} on {when}, hosted by {host}."]
        host_told = f"{host} gets a calendar event"
    if now < start <= now + host_texts.LATE:
        host_told += " and a text now, as it starts within 2 hours"
    told = [host_told]
    if kind != "join":
        told.append("the unit's residents get a text about the showing, if anyone lives there")
    prospect = []
    if texts and mobile:
        prospect.append(f"a confirmation text at {wording.display_phone(mobile)}")
    if email:
        prospect.append(f"a confirmation email at {email}")
    told.append(f"{name} gets {' and '.join(prospect)}" if prospect else f"{name} is sent nothing")
    lines.append(f"{'; '.join(told)}.")
    if not (texts and mobile):
        lines.append(f"{name} takes no texts: no confirmation, reminder or cancellation by text.")
    if thread_id:
        lines.append("It is tied to the lead's Zillow thread, as the thread's own showing.")
    lines.append(sending_sentence(settings, listing.unit))
    return " ".join(lines)


async def _booking_preview(
    session: AsyncSession,
    body: TeamBookingRequest,
    *,
    now: datetime,
    zillow_thread_id: str | None,
) -> BookingPreview:
    start, name, mobile, email = _check_fields(body)
    listing, rules = await bookings._listing(session, body.unit, now, only_open=False)
    settings = await read_settings(session)
    if zillow_thread_id is not None and not await known_lead_thread(zillow_thread_id, session):
        raise Refused(404, "unknown_thread", UNKNOWN_THREAD.format(thread=zillow_thread_id))
    try:
        # The same re-check ``place_booking`` makes, with calendars read afresh, so
        # the host named here is the host the booking would get.
        check = await open_times.recheck(listing.unit, start, now=now, session=session)
    except RentalDataInvalid as error:
        raise Refused(
            503, "unavailable", f"The rental listing data fails its checks: {error}."
        ) from None
    if not check.ok:
        raise Refused(
            409,
            NOT_OPEN,
            f"{when_text(start, rules.timezone)} cannot be booked for {listing.unit}: "
            f"{check.message}",
        )
    kind, host = check.kind or "new", check.host_name or "a showing agent"
    sentence = booking_sentence(
        name=name,
        listing=listing,
        start=start,
        kind=kind,
        host=host,
        mobile=mobile,
        email=email,
        texts=body.texts,
        thread_id=zillow_thread_id,
        settings=settings,
        rules=rules,
        now=now,
    )
    return BookingPreview(
        sentence=sentence,
        unit=listing.unit,
        start=start,
        name=name,
        timezone=rules.timezone,
        kind=kind,
        host=host,
    )


async def preview_booking(
    body: TeamBookingRequest, *, now: datetime, zillow_thread_id: str | None = None
) -> BookingPreview:
    """What booking ``body`` as the team would do now. Changes nothing. Raises ``Refused``:
    the fields (422 ``invalid``), the unit (404 ``unknown_unit``), a thread the lead
    agent never handled (404 ``unknown_thread``), a time the unit cannot be booked for
    now (409 ``not_open``, with the reason), or listing data that fails its checks."""
    async with AsyncSessionFactory() as session:
        return await _booking_preview(session, body, now=now, zillow_thread_id=zillow_thread_id)


def booking_still_as_approved(
    body: TeamBookingRequest,
    approved: str,
    *,
    now: datetime,
    zillow_thread_id: str | None = None,
) -> Callable[[AsyncSession], Awaitable[None]]:
    """What ``book_for_caller(expected_effect=)`` runs as ``place_booking``'s ``checks``:
    under the booking's own locks, the preview again, ``EffectChanged`` unless it still
    reads as ``approved``. So no booking, join or calendar change between the
    approval and the booking can give it another host or another kind than the one
    the person agreed to."""

    async def check(session: AsyncSession) -> None:
        try:
            now_reads = (
                await _booking_preview(session, body, now=now, zillow_thread_id=zillow_thread_id)
            ).sentence
        except Refused as refused:
            raise EffectChanged(None, refused.message) from None
        if now_reads != approved:
            raise EffectChanged(now_reads)

    return check


# ---------------------------------------------------------------------------
# A cancellation
# ---------------------------------------------------------------------------


def cancellation_sentence(
    showing: Showing,
    host: str,
    booked: list[ShowingAttendee],
    target: ShowingAttendee | None,
    settings: ShowingBookingSettings,
    rules: ShowingRules,
    now: datetime,
) -> str:
    """The cancellation, and who is told, in one paragraph. ``target`` is the one person
    removed, or None for the whole showing."""
    where = _place(showing.unit, showing.address)
    when = when_text(showing.starts_at, rules.timezone)
    late = host_texts.is_late(showing, now)
    residents = "the unit's residents are texted that it is off, if they were told of it"
    if target is None or len(booked) == 1:
        names = [person.name for person in booked]
        if target is None:
            lines = [
                f"Cancels the showing of {where} on {when}, hosted by {host}, and everyone "
                f"booked in it: {_names(names) or 'no one'}."
            ]
        else:
            lines = [
                f"Removes {target.name} from the showing of {where} on {when}, hosted by "
                f"{host}. They are the last one booked, so the showing is cancelled too."
            ]
        texted = [person.name for person in booked if delivery_checks.can_text(person)]
        lines.append(
            f"Texted that the team cancelled: {_names(texted)}."
            if texted
            else "No one is texted: none of them takes texts, or their numbers are known to be wrong."
        )
        host_told = f"{host}'s calendar event is cancelled" + (
            " and they are texted now, as it starts within 2 hours" if late else ""
        )
        lines.append(
            f"Anyone sent a confirmation email gets a cancellation email; {host_told}; {residents}."
        )
    else:
        others = [person.name for person in booked if person.id != target.id]
        lines = [f"Removes {target.name} from the showing of {where} on {when}, hosted by {host}."]
        lines.append(
            f"{target.name} is texted that the team cancelled their place."
            if delivery_checks.can_text(target)
            else f"{target.name} is not texted: they take no texts, or their number is known to be wrong."
        )
        host_told = f"{host}'s calendar event is updated" + (
            " and they are texted now, as it starts within 2 hours" if late else ""
        )
        lines.append(
            f"If they were sent a confirmation email, they get a cancellation email; {host_told}; "
            f"{_names(others)} {'stays' if len(others) == 1 else 'stay'} booked."
        )
    lines.append(sending_sentence(settings, showing.unit))
    return " ".join(lines)


async def _cancellation_preview(
    session: AsyncSession, showing_id: int, attendee_id: int | None, now: datetime
) -> str:
    found = (
        await session.execute(
            select(Showing, ShowingAgentRecord.name)
            .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
            .where(Showing.id == showing_id)
        )
    ).first()
    if found is None:
        raise Refused(404, "unknown_showing", f"No showing with id {showing_id}.")
    showing, host = found
    booked = list(
        await session.scalars(
            select(ShowingAttendee)
            .where(ShowingAttendee.showing_id == showing_id, ShowingAttendee.status == "booked")
            .order_by(ShowingAttendee.id)
        )
    )
    target = None
    if attendee_id is not None:
        target = await session.scalar(
            select(ShowingAttendee).where(
                ShowingAttendee.id == attendee_id, ShowingAttendee.showing_id == showing_id
            )
        )
        if target is None:
            raise Refused(
                404,
                "unknown_attendee",
                f"No person with attendee_id {attendee_id} in showing {showing_id}.",
            )
    settings = await read_settings(session)
    rules = await listings_service.read_showing_rules(session)
    if showing.status != "scheduled" or not booked:
        raise Refused(409, "already_cancelled", f"Showing {showing_id} is already cancelled.")
    if target is not None and target.status != "booked":
        raise Refused(409, "already_cancelled", f"{target.name}'s place is already cancelled.")
    if showing.starts_at <= now:
        raise Refused(
            409, "started", f"Showing {showing_id} has started; it can no longer be cancelled."
        )
    return cancellation_sentence(showing, host, booked, target, settings, rules, now)


async def preview_cancellation(
    showing_id: int, attendee_id: int | None = None, *, now: datetime
) -> str:
    """What cancelling the showing, or removing ``attendee_id`` from it, would do now.
    Changes nothing. Raises ``Refused``: 404 ``unknown_showing`` or ``unknown_attendee``,
    409 ``already_cancelled`` or ``started``."""
    async with AsyncSessionFactory() as session:
        return await _cancellation_preview(session, showing_id, attendee_id, now)


def cancellation_still_as_approved(
    showing_id: int, attendee_id: int | None, approved: str, *, now: datetime
) -> Callable[[AsyncSession], Awaitable[None]]:
    """What ``cancelling.cancel_showing`` and ``remove_attendee`` run for
    ``expected_effect``: under their shared booking lock, the preview again,
    ``EffectChanged`` unless it still reads as ``approved``. So nobody who joined after the approval
    is cancelled or texted without having been on the card."""

    async def check(session: AsyncSession) -> None:
        try:
            now_reads = await _cancellation_preview(session, showing_id, attendee_id, now)
        except Refused as refused:
            raise EffectChanged(None, refused.message) from None
        if now_reads != approved:
            raise EffectChanged(now_reads)

    return check
