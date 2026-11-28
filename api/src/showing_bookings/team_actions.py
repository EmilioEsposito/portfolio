"""The team's actions on the Booking settings tab, beside cancelling (``cancelling.py``):
a booking for someone who called, another host for a showing, and who came.

Each goes through the same code as the public page, so the same texts and
calendar changes follow, and each answers what it did and what it queued.

- **A booking for a caller** (``book_for_caller``) is a booking through
  ``bookings.place_booking`` with source ``team``: the shared lock, the
  re-check of the time with fresh calendars, the same rows and the same
  notices. It skips what only protects the public form: Turnstile, the limits
  per mobile, email, IP address and site, and the form's ticks (a person on
  the team spoke to the caller). With texts accepted and a mobile, the
  confirmation text is delivered at once and the host's event and the invite
  wait behind it, as for a page booking; a number Quo refuses withdraws the
  booking as on the page. Without texts, or with no mobile, nothing is
  texted or held: the host's event goes with the next run, and the invite
  too when there is an email. ``place_booking`` takes a ``Person`` with no
  mobile or email as well, which the lead agent's bookings reuse.
- **Another host** (``change_host``): the agent must be free for that time by
  the rules open times use (``open_times.host_problem``). The old host's
  event is cancelled by a ``host_event_moved`` notice naming that event, the
  old host's notices that have not gone out are stopped and queued again for
  the new host, held behind the same confirmation texts, and when the old
  event went out the new host gets a new one at once. The showing's
  ``host_event_id`` is cleared, so the new host's event is created afresh;
  host notices from before the move no longer count
  (``host_event.since_last_move``). Prospects are told nothing: they were
  never shown a host. Refused while a host notice is being sent, so the old
  event's id is always known, and once the showing has started.
- **Who came** (``mark_attendance``): after the start, each attendee is marked
  ``attended`` or ``no_show``, and can be changed (``unknown`` clears it).
  Nothing is sent.

Each change is one transaction in the module's lock order: the shared
booking lock first, then attendees, then notices, then the showing.
"""

import secrets
from datetime import datetime
from typing import Literal

import logfire
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.showing_bookings import (
    bookings,
    host_event,
    host_texts,
    open_times,
    outbox,
    wording,
)
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import CalendarPayload, normalize_email, normalize_us_mobile

TEAM_NUMBER_REFUSED = (
    "Quo refused this number, so the booking was withdrawn and nothing else was sent. "
    "Check the number, or book without texts."
)
STARTED = "This showing has started."


class QueuedNotice(BaseModel):
    """A notice an action queued, as it stands when the action answers."""

    id: int
    kind: str
    channel: str
    status: str
    # Set when the notice waits for a confirmation text to be delivered.
    held: bool


def _queued(notice: ShowingNotice) -> QueuedNotice:
    return QueuedNotice(
        id=notice.id,
        kind=notice.kind,
        channel=notice.channel,
        status=notice.status,
        held=notice.status == "held",
    )


async def _notices(ids: list[int]) -> list[QueuedNotice]:
    if not ids:
        return []
    async with AsyncSessionFactory() as session:
        rows = await session.scalars(
            select(ShowingNotice).where(ShowingNotice.id.in_(ids)).order_by(ShowingNotice.id)
        )
        return [_queued(row) for row in rows]


# ---------------------------------------------------------------------------
# A booking for someone who called
# ---------------------------------------------------------------------------


class TeamBookingRequest(BaseModel):
    """The tab's Add a booking form. A name is the only detail required."""

    model_config = ConfigDict(extra="ignore")

    unit: str = ""
    start: str = ""
    name: str = ""
    mobile: str = ""
    email: str = ""
    # The caller agreed to texts about the showing. Without a mobile it is ignored.
    texts: StrictBool = False


class TeamBooking(BaseModel):
    showing_id: int
    attendee_id: int
    # "join" when the caller joined a showing already booked.
    kind: str
    texts_accepted: bool
    # The confirmation text's status right after sending, or None when none was queued.
    confirmation: str | None
    # Every notice queued for this booking, in order.
    notices: list[QueuedNotice]


def _check_fields(
    body: TeamBookingRequest,
) -> tuple[datetime, str, str | None, str | None]:
    problems: dict[str, str] = {}
    start = bookings._parse_start(body.start)
    if start is None:
        problems["start"] = "Pick a time."
    name = " ".join(body.name.split())
    if not name:
        problems["name"] = "Enter the caller's name."
    elif len(name) > 100:
        problems["name"] = "Enter a shorter name."
    mobile = None
    if body.mobile.strip():
        mobile = normalize_us_mobile(body.mobile)
        if mobile is None:
            problems["mobile"] = "Enter a ten-digit US mobile number, or leave it empty."
    email = None
    if body.email.strip():
        email = normalize_email(body.email) if len(body.email) <= 254 else None
        if email is None:
            problems["email"] = "Enter one email address, or leave it empty."
    if problems:
        raise Refused(422, "invalid", "Check the marked fields.", problems)
    return start, name, mobile, email


async def book_for_caller(
    body: TeamBookingRequest,
    *,
    taken_by: str,
    now: datetime,
    texts: TextSender | None = None,
    zillow_thread_id: str | None = None,
) -> TeamBooking:
    """Book a caller into one of the unit's open times. Raises ``Refused``.

    ``taken_by`` is who on the team took the booking; with texts accepted it is
    the consent record's page.

    ``zillow_thread_id`` ties the booking to a Zillow lead's thread, as a page
    booking through the lead's own link is tied (``bookings.book``), so the lead
    agent's next case for that thread lists it as the lead's own showing. Only
    the general agent's tool passes it (``sernia_ai/tools/showing_tools.py``);
    the tab's form cannot. The row keeps source ``team`` and no lead
    conversation: nothing that applies only to a place the lead agent's reply
    booked (``delivery_checks.reply_unsettled``, the reminder by email, the
    link's "add your details") applies to it.
    """
    async with AsyncSessionFactory() as session:
        listing, rules = await bookings._listing(session, body.unit, now, only_open=False)
    start, name, mobile, email = _check_fields(body)
    placed = await bookings.place_booking(
        listing,
        rules,
        start,
        bookings.Person(
            name=name,
            source="team",
            mobile=mobile,
            email=email,
            texts_accepted=body.texts,
            consent_page=f"team:{taken_by}",
            zillow_thread_id=zillow_thread_id,
        ),
        now=now,
    )
    attendee = placed.attendee
    confirmation = None
    if placed.confirmation_id is not None:
        confirmation = await bookings._send_confirmation(
            placed.confirmation_id, now=now, texts=texts
        )
        if confirmation == "refused":
            await bookings.withdraw(attendee.id, placed.confirmation_id, now=now)
            raise Refused(
                422,
                "number_refused",
                TEAM_NUMBER_REFUSED,
                {"mobile": "Quo refused this number."},
            )
    # A showing starting within 2 hours: the host is texted at once.
    await bookings.deliver_host_text(placed.host_text_id, now=now, texts=texts)
    async with AsyncSessionFactory() as session:
        ids = list(
            await session.scalars(
                select(ShowingNotice.id).where(ShowingNotice.attendee_id == attendee.id)
            )
        )
    if placed.residents_notice_id is not None:
        ids.append(placed.residents_notice_id)
    return TeamBooking(
        showing_id=placed.showing.id,
        attendee_id=attendee.id,
        kind=placed.kind,
        texts_accepted=attendee.texts_accepted,
        confirmation=confirmation,
        notices=await _notices(ids),
    )


# ---------------------------------------------------------------------------
# Another host
# ---------------------------------------------------------------------------


class HostChangeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: int


class HostChange(BaseModel):
    showing_id: int
    host_id: int
    host_name: str
    previous_host_name: str
    # The old host's event cancelled, and the new host's event or its held notices.
    notices: list[QueuedNotice]


async def change_host(
    showing_id: int, agent_id: int, *, now: datetime, texts: TextSender | None = None
) -> HostChange:
    """Give a scheduled showing that has not started to another agent. Raises ``Refused``.

    Within 2 hours of the start, both hosts are also texted at once
    (``host_texts.queue_moved``): the new one that it is theirs, the old one
    that it is not.
    """
    queued: list[int] = []
    late: list[int] = []
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
    try:
        async with AsyncSessionFactory() as session, session.begin():
            await bookings.lock(session, bookings.BOOKING_LOCK)
            showing = await session.get(Showing, showing_id)
            if showing is None:
                raise Refused(404, "unknown_showing", "No such showing.")
            agent = await session.get(ShowingAgentRecord, agent_id)
            if agent is None:
                raise Refused(404, "unknown_agent", "No such agent.")
            if showing.status != "scheduled":
                raise Refused(409, "not_scheduled", "This showing is cancelled.")
            if showing.starts_at <= now:
                raise Refused(409, "started", f"{STARTED} Its host can no longer change.")
            if agent.id == showing.agent_id:
                raise Refused(409, "same_host", f"{agent.name} already hosts this showing.")
            # Notices before the showing, as everywhere in the module.
            host_notices = list(
                await session.scalars(
                    select(ShowingNotice)
                    .where(
                        ShowingNotice.showing_id == showing_id,
                        ShowingNotice.kind.in_(host_event.KINDS),
                        host_event.since_last_move(showing_id),
                    )
                    .order_by(ShowingNotice.id)
                    .with_for_update()
                )
            )
            if any(n.status == "pending" and n.sending_started_at for n in host_notices):
                raise Refused(
                    409,
                    "event_being_sent",
                    "The host's calendar event is being sent right now. Try again in a minute.",
                )
            showing = await session.scalar(
                select(Showing)
                .where(Showing.id == showing_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            try:
                problem = await open_times.host_problem(showing, agent, now=now, session=session)
            except RentalDataInvalid:
                logfire.exception("showing bookings: rental data fails its checks")
                raise Refused(
                    503, "unavailable", "Rental listing data fails its checks. Fix it first."
                ) from None
            if problem is not None:
                raise Refused(409, problem[0], problem[1])
            old_host = await session.get_one(ShowingAgentRecord, showing.agent_id)
            went_out = [n for n in host_notices if n.status in ("sent", "captured", "unknown")]
            waiting = [n for n in host_notices if n.status in ("held", "pending")]
            # Stopped, but a corrected mobile would bring them back (``correct_mobile``
            # revives cancelled notices that wait for an earlier confirmation, except a
            # withdrawn booking's). They move to the new host the same way, cancelled.
            revivable = [
                n
                for n in host_notices
                if n.status == "cancelled"
                and n.waits_for_notice_id is not None
                and n.last_error != bookings.WITHDRAWN_NOTE
            ]
            # The old host's create was refused: the new host still needs the event.
            failed = any(n.status == "failed" for n in host_notices)
            move = secrets.token_hex(4)

            if went_out or showing.host_event_id:
                latest = went_out[-1] if went_out else None
                calendar_id = (
                    latest.payload.get("calendar_id") if latest is not None else None
                ) or bookings.NO_SHOWINGS_CALENDAR
                visit = wording.Visit(
                    unit=showing.unit,
                    address=showing.address,
                    starts_at=showing.starts_at,
                    ends_at=showing.ends_at,
                    timezone="UTC",
                )
                moved = await outbox.enqueue(
                    session,
                    kind=host_event.MOVED_KIND,
                    channel="calendar_event",
                    showing_id=showing.id,
                    recipient=old_host.email,
                    recipient_key=f"agent:{old_host.id}:moved:{move}",
                    payload=CalendarPayload(
                        operation="cancel",
                        calendar_id=calendar_id,
                        event_id=showing.host_event_id,
                        summary=wording.host_event_moved_summary(visit),
                        description=wording.host_event_moved_description(),
                        location=showing.address,
                        starts_at=showing.starts_at,
                        ends_at=showing.ends_at,
                        attendees=[old_host.email],
                    ),
                    now=now,
                )
                queued.append(moved.id)

            # The old host's notices that have not gone out never will. Their
            # hold is dropped too, so a corrected mobile does not bring them back.
            # Held ones wait for the same confirmation again; released ones stay due.
            holds_by_notice = {
                n.id: n.waits_for_notice_id if n.status == "held" else None for n in waiting
            }
            for notice in waiting:
                notice.status = "cancelled"
                notice.last_error = host_event.HOST_CHANGED
                notice.waits_for_notice_id = None
            stopped_by_notice = {n.id: (n.waits_for_notice_id, n.last_error) for n in revivable}
            for notice in revivable:
                notice.waits_for_notice_id = None
            showing.agent_id = agent.id
            showing.host_event_id = None
            await session.flush()
            late = await host_texts.queue_moved(
                session, showing, old_host, agent, move=move, rules=rules, now=now
            )
            queued += late

            calendar_id = await host_event.showings_calendar(session, agent)
            payload = await host_event.render(session, showing, calendar_id=calendar_id)
            if payload is not None:
                for notice in waiting:
                    again = await outbox.enqueue(
                        session,
                        kind=notice.kind,
                        channel="calendar_event",
                        showing_id=showing.id,
                        attendee_id=notice.attendee_id,
                        recipient=agent.email,
                        recipient_key=f"agent:{agent.id}:replaces:{notice.id}",
                        payload=payload,
                        now=now,
                        waits_for_notice_id=holds_by_notice[notice.id],
                    )
                    queued.append(again.id)
                for notice in revivable:
                    # Saved stopped, as the old one was, so only a correction revives it,
                    # and then for the new host on the new host's calendar.
                    holder, note = stopped_by_notice[notice.id]
                    again = await outbox.enqueue(
                        session,
                        kind=notice.kind,
                        channel="calendar_event",
                        showing_id=showing.id,
                        attendee_id=notice.attendee_id,
                        recipient=agent.email,
                        recipient_key=f"agent:{agent.id}:replaces:{notice.id}",
                        payload=payload,
                        now=now,
                        waits_for_notice_id=holder,
                    )
                    again.status = "cancelled"
                    again.last_error = note
                # The old host had the event, or was meant to and the calendar refused
                # it: the new host gets theirs now, not only when a later
                # confirmation is delivered.
                if (went_out or failed) and all(holds_by_notice[n.id] is not None for n in waiting):
                    created = await outbox.enqueue(
                        session,
                        kind=host_event.CREATE_KIND,
                        channel="calendar_event",
                        showing_id=showing.id,
                        recipient=agent.email,
                        recipient_key=f"agent:{agent.id}:moved:{move}",
                        payload=payload,
                        now=now,
                    )
                    queued.append(created.id)
            previous, new_name = old_host.name, agent.name
    except IntegrityError:
        # The database's no-overlap rule: another showing took the agent meanwhile.
        raise Refused(
            409, "in_another_showing", "That agent has another showing at that time."
        ) from None
    logfire.info(
        "showing host changed by the team",
        showing_id=showing_id,
        agent_id=agent_id,
        notices_queued=len(queued),
    )
    for notice_id in late:
        await bookings.deliver_host_text(notice_id, now=now, texts=texts)
    return HostChange(
        showing_id=showing_id,
        host_id=agent_id,
        host_name=new_name,
        previous_host_name=previous,
        notices=await _notices(queued),
    )


# ---------------------------------------------------------------------------
# Who came
# ---------------------------------------------------------------------------

Attendance = Literal["attended", "no_show", "unknown"]


class AttendanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attendance: Attendance


class AttendanceMark(BaseModel):
    showing_id: int
    attendee_id: int
    attendance: Attendance
    # Marking attendance sends nothing.
    notices: list[QueuedNotice] = []


async def mark_attendance(
    showing_id: int, attendee_id: int, attendance: Attendance, *, now: datetime
) -> AttendanceMark:
    """Mark whether a booked person came, once the showing has started. Raises ``Refused``."""
    async with AsyncSessionFactory() as session, session.begin():
        attendee = await session.scalar(
            select(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id, ShowingAttendee.showing_id == showing_id)
            .with_for_update()
        )
        if attendee is None:
            raise Refused(404, "unknown_attendee", "No such showing or person.")
        showing = await session.get_one(Showing, showing_id)
        if attendee.status != "booked" or showing.status != "scheduled":
            raise Refused(409, "not_booked", "This booking is cancelled.")
        if showing.starts_at > now:
            raise Refused(409, "not_started", "Mark who came once the showing has started.")
        attendee.attendance = attendance
    logfire.info(
        "showing attendance marked",
        showing_id=showing_id,
        attendee_id=attendee_id,
        attendance=attendance,
    )
    return AttendanceMark(showing_id=showing_id, attendee_id=attendee_id, attendance=attendance)
