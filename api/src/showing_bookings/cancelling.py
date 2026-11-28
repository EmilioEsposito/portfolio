"""Cancelling: a prospect from the link in their text, the team from the Booking
settings tab, and a unit going off the market.

| Case | Attendee | Showing | Prospect | Host's event |
| --- | --- | --- | --- | --- |
| A prospect cancels, others still booked | cancelled (``prospect``) | stays | text confirming it, invite withdrawn | updated |
| The last prospect cancels | cancelled (``prospect``) | cancelled (``prospect``) | the same | cancelled |
| A lead moved their showing by email (the old place) | cancelled (``prospect``) | stays, or cancelled (``prospect``) when they were the last | no text; invite withdrawn | updated, or cancelled when they were the last |
| The team removes one attendee | cancelled (``team``) | cancelled when they were the last | the team's cancellation text, invite withdrawn | updated, or cancelled when they were the last |
| The team cancels a showing | every one cancelled (``team``) | cancelled (``team``) | the team's cancellation text, invite withdrawn | cancelled |
| The unit goes off the market | every one cancelled (``off_market``) | cancelled (``off_market``) | the same | cancelled |

Every text and calendar change is a notice queued here and delivered by the
outbox. Notices of a cancelled attendee or showing that have not gone out are
stopped. An invite or a host's event that went out (sent, captured, or may
have: unknown, or being sent now) gets a cancellation notice; one that never
went out gets nothing, so a cancellation never creates an event. Whether there
is an event to cancel is decided again when the notice is delivered
(``host_event.cancel_check``).

Each case is one transaction, in the module's lock order: the shared booking
lock first (so no booking, withdrawal or other cancellation runs in between),
then the attendees in id order, then their notices, then the showings in id
order. Each is safe to call twice: a second call finds the booking cancelled,
changes nothing and queues nothing.

A showing that is cancelled texts its residents (``queue_resident_cancellation``)
when they were told of it; see ``resident_notices.py``.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import (
    bookings,
    delivery_checks,
    host_event,
    host_texts,
    outbox,
    resident_notices,
    residents,
    wording,
)
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import CalendarPayload, TextPayload

# "reply_not_sent": a place the Zillow lead agent booked for a reply that was
# then not sent (``lead_bookings.py``); the lead never heard of it.
CancelReason = Literal["prospect", "team", "off_market", "reply_not_sent"]
# Reasons a person did not choose: the page says the team cancelled.
BY_TEAM = frozenset({"team", "off_market"})

PROSPECT_TEXT_KIND = "prospect_cancelled"
TEAM_TEXT_KIND = "prospect_cancelled_by_team"
INVITE_CANCEL_KIND = host_event.INVITE_CANCEL_KIND
HOST_CANCEL_KIND = host_event.CANCEL_KIND
# Notices a cancellation queues. Stopping the notices of a cancelled booking
# never stops these, so a later cancellation in the same showing keeps the
# texts an earlier one queued. A change of host's cancellation of the old
# host's event is one too: it always goes, so the old calendar is cleaned up.
CANCELLATION_KINDS = frozenset(
    {
        PROSPECT_TEXT_KIND,
        TEAM_TEXT_KIND,
        INVITE_CANCEL_KIND,
        HOST_CANCEL_KIND,
        host_event.MOVED_KIND,
        residents.CANCELLED_KIND,
        delivery_checks.HOST_LEFT_KIND,
        delivery_checks.HOST_SHOWING_CANCELLED_KIND,
    }
)
# Never stopped with a cancelled showing either: the residents' reminder and the
# host's morning text list every showing of the day (at the unit, or of the
# host), and drop the cancelled ones themselves when they are delivered.
NOT_STOPPED_KINDS = CANCELLATION_KINDS | {
    delivery_checks.RESIDENT_REMINDER_KIND,
    delivery_checks.HOST_MORNING_KIND,
}
STOPPED_NOTE = host_event.BOOKING_CANCELLED
# A booking whose confirmation text could not reach the number: a cancellation
# texts it nothing more (the invite is still withdrawn).
WRONG_NUMBER = delivery_checks.WRONG_NUMBER
STARTED_MESSAGE = "This showing has already started."


def clock() -> datetime:
    """The time an off-market cancellation runs at. Tests replace it."""
    return datetime.now(UTC)


def _went_out():
    """A notice that went out, may have, or is being sent now."""
    return ShowingNotice.status.in_(["sent", "captured", "unknown"]) | (
        (ShowingNotice.status == "pending") & ShowingNotice.sending_started_at.is_not(None)
    )


async def _stop(session: AsyncSession, condition) -> int:
    """Cancel the matching notices that have not gone out and are not being sent."""
    result = await session.execute(
        update(ShowingNotice)
        .where(
            condition,
            ShowingNotice.status.in_(["held", "pending"]),
            ShowingNotice.sending_started_at.is_(None),
            ShowingNotice.kind.not_in(NOT_STOPPED_KINDS),
        )
        .values(status="cancelled", last_error=STOPPED_NOTE),
        execution_options={"synchronize_session": False},
    )
    return result.rowcount or 0


def will_text(attendee: ShowingAttendee) -> bool:
    """Whether a cancellation texts this person: a mobile, texts accepted, no sign
    the number is wrong (its confirmation was undelivered or refused), and not a
    place whose lead agent's reply is not settled, which they never heard of."""
    return delivery_checks.can_text(attendee)


def _mark(row: Showing | ShowingAttendee, reason: str, now: datetime) -> None:
    row.status = "cancelled"
    row.cancel_reason = reason
    row.cancelled_at = now


async def _lock_showings(session: AsyncSession, showing_ids: list[int]) -> list[Showing]:
    return list(
        await session.scalars(
            select(Showing)
            .where(Showing.id.in_(showing_ids))
            .order_by(Showing.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


async def _tell_attendee(
    session: AsyncSession,
    attendee: ShowingAttendee,
    showing: Showing,
    *,
    by_team: bool,
    rules: ShowingRules,
    now: datetime,
    moved: bool = False,
) -> list[int]:
    """The attendee's cancellation text and their invite withdrawn. Returns the text's id.

    ``moved``: the place is the old one of a showing that moved, so no text says
    it is cancelled; what tells them of the new time says it replaces this one.
    """
    visit = bookings.visit_of(showing, rules)
    queued: list[int] = []
    if will_text(attendee) and not moved:
        body = (
            wording.team_cancelled_text(visit, bookings.units_url())
            if by_team
            else wording.prospect_cancelled_text(visit, bookings.unit_url(showing.unit))
        )
        text = await outbox.enqueue(
            session,
            kind=TEAM_TEXT_KIND if by_team else PROSPECT_TEXT_KIND,
            channel="sms",
            showing_id=showing.id,
            attendee_id=attendee.id,
            recipient=attendee.mobile,
            recipient_key=f"attendee:{attendee.id}",
            payload=TextPayload(body=body),
            now=now,
        )
        queued.append(text.id)
    invite = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.attendee_id == attendee.id,
            ShowingNotice.kind == host_event.INVITE_KIND,
            _went_out(),
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    if attendee.email and (invite is not None or attendee.calendar_event_id):
        calendar_id = (
            invite.payload.get("calendar_id") if invite is not None else None
        ) or bookings.NO_SHOWINGS_CALENDAR
        await outbox.enqueue(
            session,
            kind=INVITE_CANCEL_KIND,
            channel="calendar_invite",
            showing_id=showing.id,
            attendee_id=attendee.id,
            recipient=attendee.email,
            recipient_key=f"attendee:{attendee.id}",
            payload=CalendarPayload(
                operation="cancel",
                calendar_id=calendar_id,
                summary=wording.invite_cancelled_summary(visit),
                description=wording.invite_cancelled_description(),
                location=showing.address,
                starts_at=showing.starts_at,
                ends_at=showing.ends_at,
                attendees=[attendee.email],
            ),
            now=now,
        )
    return queued


async def queue_host_cancel(session: AsyncSession, showing: Showing, now: datetime) -> bool:
    """Cancel the host's event when it went out, may have, or is being created now."""
    out = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing.id,
            ShowingNotice.kind.in_(host_event.KINDS),
            host_event.since_last_move(showing.id),
            _went_out(),
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    if out is None and not showing.host_event_id:
        return False
    host_email = await session.scalar(
        select(ShowingAgentRecord.email).where(ShowingAgentRecord.id == showing.agent_id)
    )
    calendar_id = (
        out.payload.get("calendar_id") if out is not None else None
    ) or bookings.NO_SHOWINGS_CALENDAR
    visit = wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone="UTC",
    )
    await outbox.enqueue(
        session,
        kind=HOST_CANCEL_KIND,
        channel="calendar_event",
        showing_id=showing.id,
        recipient=host_email,
        recipient_key=f"agent:{showing.agent_id}",
        payload=CalendarPayload(
            operation="cancel",
            calendar_id=calendar_id,
            summary=wording.host_event_cancelled_summary(visit),
            description=wording.host_event_cancelled_description(showing.cancel_reason),
            location=showing.address,
            starts_at=showing.starts_at,
            ends_at=showing.ends_at,
            attendees=[host_email],
        ),
        now=now,
    )
    return True


async def queue_resident_cancellation(
    session: AsyncSession, showing: Showing, *, now: datetime
) -> None:
    """The residents' cancellation text, for a showing that is now cancelled.

    Every path that cancels a showing calls this after stopping its unsent
    notices: the last prospect cancelling, the team, the unit going off the
    market, and a withdrawn booking that leaves the showing empty. Queued only
    when the residents' first text went out, may have, or is being sent; due
    now, for the job, in the daytime.
    """
    await resident_notices.queue_cancellation(session, showing, now=now)


async def _cancel_showings(
    session: AsyncSession,
    showing_ids: list[int],
    *,
    reason: CancelReason,
    rules: ShowingRules,
    now: datetime,
) -> tuple[int, list[int], list[int]]:
    """Cancel the showings and everyone booked in them, in the caller's transaction.

    The caller holds the shared booking lock. Returns how many attendees were
    cancelled, the ids of the texts queued to them, and the ids of the host's
    texts (a showing starting within 2 hours).
    """
    ids = sorted(set(showing_ids))
    if not ids:
        return 0, [], []
    attendees = list(
        await session.scalars(
            select(ShowingAttendee)
            .where(ShowingAttendee.showing_id.in_(ids), ShowingAttendee.status == "booked")
            .order_by(ShowingAttendee.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    showings = [
        showing
        for showing in await _lock_showings_after_notices(session, ids)
        if showing.status == "scheduled"
    ]
    texts: list[int] = []
    host: list[int] = []
    cancelled = 0
    for showing in showings:
        _mark(showing, reason, now)
        for attendee in (a for a in attendees if a.showing_id == showing.id):
            _mark(attendee, reason, now)
            cancelled += 1
        await session.flush()
        for attendee in (a for a in attendees if a.showing_id == showing.id):
            texts += await _tell_attendee(
                session, attendee, showing, by_team=True, rules=rules, now=now
            )
        await queue_host_cancel(session, showing, now)
        await queue_resident_cancellation(session, showing, now=now)
        late = await host_texts.queue_showing_cancelled(session, showing, rules=rules, now=now)
        host += [late] if late is not None else []
    return cancelled, texts, host


async def _lock_showings_after_notices(session: AsyncSession, ids: list[int]) -> list[Showing]:
    """Stop the showings' notices, then lock the showings: notices before showings."""
    await _stop(session, ShowingNotice.showing_id.in_(ids))
    return await _lock_showings(session, ids)


async def _remove(
    session: AsyncSession,
    attendee: ShowingAttendee,
    showing_id: int,
    *,
    reason: CancelReason,
    rules: ShowingRules,
    now: datetime,
    moved: bool = False,
) -> tuple[Showing, list[int], list[int]]:
    """Cancel one booked attendee, and their showing when they were the last.

    The caller holds the shared booking lock and the attendee's row lock, so
    the number still booked cannot change underneath. Returns the showing, the
    ids of the texts queued to the attendee (none for the old place of a move),
    and the id of the host's text when the showing starts within 2 hours; all
    are delivered at once.
    """
    others = await session.scalar(
        select(func.count())
        .select_from(ShowingAttendee)
        .where(
            ShowingAttendee.showing_id == showing_id,
            ShowingAttendee.status == "booked",
            ShowingAttendee.id != attendee.id,
        )
    )
    last = others == 0
    _mark(attendee, reason, now)
    await session.flush()
    await _stop(
        session,
        ShowingNotice.showing_id == showing_id
        if last
        else ShowingNotice.attendee_id == attendee.id,
    )
    (showing,) = await _lock_showings(session, [showing_id])
    if last:
        _mark(showing, reason, now)
        await session.flush()
    texts = await _tell_attendee(
        session,
        attendee,
        showing,
        by_team=reason in BY_TEAM,
        rules=rules,
        now=now,
        moved=moved,
    )
    if last:
        await queue_host_cancel(session, showing, now)
        await queue_resident_cancellation(session, showing, now=now)
    else:
        await bookings.queue_host_update_after_leaving(
            session, showing=showing, attendee_id=attendee.id, now=now, why="cancelled"
        )
    late = await host_texts.queue_left(
        session, showing, attendee, by_team=reason in BY_TEAM, rules=rules, now=now
    )
    return showing, texts, [late] if late is not None else []


async def _deliver_texts(ids: list[int], *, now: datetime, texts: TextSender | None) -> None:
    """Deliver the cancellation texts at once; calendar changes go with the job."""
    for notice_id in ids:
        try:
            await outbox.deliver(notice_id, now=now, texts=texts)
        except Exception:
            # The row stays pending or is saved as unknown; the job takes it from there.
            logfire.exception("showing cancellation text could not be delivered now")


# ---------------------------------------------------------------------------
# A prospect, from the link in their text
# ---------------------------------------------------------------------------


async def cancel_by_token(
    token: str, *, now: datetime, texts: TextSender | None = None
) -> bookings.BookingView:
    """Cancel the booking the token names. Raises ``bookings.Refused``.

    A booking already cancelled is answered as it is, and nothing is sent.
    One whose showing has started is refused with 409 ``started``.
    """
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
    queued: list[int] = []
    host: list[int] = []
    async with AsyncSessionFactory() as session, session.begin():
        await bookings.lock(session, bookings.BOOKING_LOCK)
        attendee, showing = await bookings.by_token(session, token, lock=True)
        if attendee.status == "booked" and showing.status == "scheduled":
            if showing.starts_at <= now:
                raise bookings.Refused(409, "started", STARTED_MESSAGE)
            showing, queued, host = await _remove(
                session, attendee, showing.id, reason="prospect", rules=rules, now=now
            )
            logfire.info(
                "showing booking cancelled by the prospect",
                attendee_id=attendee.id,
                showing_id=showing.id,
                showing_cancelled=showing.status == "cancelled",
            )
        view = await bookings.view_of(session, showing, attendee, rules.timezone, now)
    await _deliver_texts(queued + host, now=now, texts=texts)
    return view


# ---------------------------------------------------------------------------
# The Zillow lead agent's decisions
# ---------------------------------------------------------------------------


async def cancel_attendee(
    attendee_id: int,
    *,
    reason: CancelReason,
    now: datetime,
    texts: TextSender | None = None,
    moved: bool = False,
) -> bool:
    """Cancel one booked place, as the cancel link does, with ``reason``.

    For the lead agent's decisions (``lead_bookings.py``): ``prospect`` when
    the lead cancelled or moved in the email thread, ``reply_not_sent`` when
    the reply that booked it was not sent. The same notices as any
    cancellation: the attendee's own text and invite withdrawn when they had
    them, the host's event updated or cancelled, and the residents' text when
    they were told. With ``moved`` (the showing moved and this is its old
    place) the attendee's own text is left out, and everything else is the
    same. Returns whether it cancelled anything; a place already cancelled, or
    whose showing has started, is left as it is.
    """
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
    queued: list[int] = []
    host: list[int] = []
    async with AsyncSessionFactory() as session, session.begin():
        await bookings.lock(session, bookings.BOOKING_LOCK)
        attendee = await session.scalar(
            select(ShowingAttendee).where(ShowingAttendee.id == attendee_id).with_for_update()
        )
        if attendee is None or attendee.status != "booked":
            return False
        showing = await session.get_one(Showing, attendee.showing_id)
        if showing.status != "scheduled" or showing.starts_at <= now:
            return False
        showing, queued, host = await _remove(
            session, attendee, showing.id, reason=reason, rules=rules, now=now, moved=moved
        )
    logfire.info(
        "showing place cancelled from a lead agent decision",
        attendee_id=attendee_id,
        reason=reason,
        moved=moved,
        showing_cancelled=showing.status == "cancelled",
    )
    await _deliver_texts(queued + host, now=now, texts=texts)
    return True


# ---------------------------------------------------------------------------
# The team, from the Booking settings tab
# ---------------------------------------------------------------------------


class TeamCancel(BaseModel):
    """What a team cancellation did."""

    showing_id: int
    showing_status: str
    # How many people this call cancelled; 0 when there was nothing left to cancel.
    attendees_cancelled: int
    texts_queued: int


class NotFound(LookupError):
    pass


class Started(Exception):
    pass


async def cancel_showing(
    showing_id: int,
    *,
    now: datetime,
    texts: TextSender | None = None,
    checks: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> TeamCancel:
    """Cancel a showing and everyone in it. Raises ``NotFound`` or ``Started``.

    ``checks(session)`` runs under the shared booking lock before anything changes,
    and may raise to cancel nothing: the general agent's tool checks there that the
    cancellation still reads as the person approved it (``effects.py``).
    """
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
    async with AsyncSessionFactory() as session, session.begin():
        await bookings.lock(session, bookings.BOOKING_LOCK)
        if checks is not None:
            await checks(session)
        showing = await session.get(Showing, showing_id)
        if showing is None:
            raise NotFound
        cancelled, queued, host = 0, [], []
        if showing.status == "scheduled":
            if showing.starts_at <= now:
                raise Started
            cancelled, queued, host = await _cancel_showings(
                session, [showing_id], reason="team", rules=rules, now=now
            )
        status = showing.status
    logfire.info(
        "showing cancelled by the team", showing_id=showing_id, attendees_cancelled=cancelled
    )
    await _deliver_texts(queued + host, now=now, texts=texts)
    return TeamCancel(
        showing_id=showing_id,
        showing_status=status,
        attendees_cancelled=cancelled,
        texts_queued=len(queued),
    )


async def remove_attendee(
    showing_id: int,
    attendee_id: int,
    *,
    now: datetime,
    texts: TextSender | None = None,
    checks: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> TeamCancel:
    """Remove one attendee, as a prospect cancelling but with the team's wording.

    Raises ``NotFound`` or ``Started``. ``checks`` as for ``cancel_showing``.
    """
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
    async with AsyncSessionFactory() as session, session.begin():
        await bookings.lock(session, bookings.BOOKING_LOCK)
        if checks is not None:
            await checks(session)
        attendee = await session.scalar(
            select(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id, ShowingAttendee.showing_id == showing_id)
            .with_for_update()
        )
        if attendee is None:
            raise NotFound
        showing = await session.get_one(Showing, showing_id)
        cancelled, queued, host = 0, [], []
        if attendee.status == "booked" and showing.status == "scheduled":
            if showing.starts_at <= now:
                raise Started
            showing, queued, host = await _remove(
                session, attendee, showing_id, reason="team", rules=rules, now=now
            )
            cancelled = 1
        status = showing.status
    logfire.info(
        "showing attendee removed by the team",
        showing_id=showing_id,
        attendee_id=attendee_id,
        changed=bool(cancelled),
        showing_cancelled=status == "cancelled",
    )
    await _deliver_texts(queued + host, now=now, texts=texts)
    return TeamCancel(
        showing_id=showing_id,
        showing_status=status,
        attendees_cancelled=cancelled,
        texts_queued=len(queued),
    )


# ---------------------------------------------------------------------------
# A unit going off the market
# ---------------------------------------------------------------------------


class OffMarketEffect(BaseModel):
    """What saving an off-market date would cancel for a unit right now."""

    showings: int
    # People booked in them.
    people: int
    # Of those, the people a cancellation would text (``will_text``).
    people_texted: int

    def sentence(self) -> str:
        """ "This cancels 2 showings and texts 3 people." """
        showings = "1 showing" if self.showings == 1 else f"{self.showings} showings"
        texted = "1 person" if self.people_texted == 1 else f"{self.people_texted} people"
        return f"This cancels {showings} and texts {texted}."


async def lock_bookings(session: AsyncSession) -> None:
    """Take the shared booking lock for the caller's transaction, until it ends.

    A listing save that carries an off-market date takes it first, before the
    listing's own lock and before counting ``off_market_effect``, and holds it
    through the save and the cancellation: no booking can then commit between
    what a person agreed to and what is cancelled. Taking it again in the same
    transaction is a no-op.
    """
    await bookings.lock(session, bookings.BOOKING_LOCK)


async def _off_market_showing_ids(
    session: AsyncSession, unit: str, off_market_on: date, now: datetime
) -> list[int]:
    rules = await listings_service.read_showing_rules(session)
    first_day = datetime.combine(off_market_on, time(0), ZoneInfo(rules.timezone))
    return list(
        await session.scalars(
            select(Showing.id)
            .where(
                func.lower(Showing.unit) == unit.strip().lower(),
                Showing.status == "scheduled",
                Showing.starts_at >= first_day,
                Showing.starts_at > now,
            )
            .order_by(Showing.id)
        )
    )


async def off_market_effect(
    session: AsyncSession, unit: str, off_market_on: date | None, *, now: datetime | None = None
) -> OffMarketEffect:
    """What saving ``off_market_on`` for ``unit`` would cancel now. Changes nothing.

    The gates in front of an off-market save (the general agent's approval, the
    team's confirmation on the listings screen) ask this first. A caller that
    saves on the answer holds ``lock_bookings`` from before this count.
    """
    if off_market_on is None:
        return OffMarketEffect(showings=0, people=0, people_texted=0)
    showing_ids = await _off_market_showing_ids(session, unit, off_market_on, now or clock())
    if not showing_ids:
        return OffMarketEffect(showings=0, people=0, people_texted=0)
    people = list(
        await session.scalars(
            select(ShowingAttendee).where(
                ShowingAttendee.showing_id.in_(showing_ids), ShowingAttendee.status == "booked"
            )
        )
    )
    return OffMarketEffect(
        showings=len(showing_ids),
        people=len(people),
        people_texted=sum(1 for person in people if will_text(person)),
    )


async def cancel_off_market(
    session: AsyncSession, unit: str, off_market_on: date, *, now: datetime | None = None
) -> int:
    """Cancel the unit's scheduled showings on or after ``off_market_on``, in the caller's
    transaction, the way a team cancellation does. Returns how many were cancelled.

    Called by ``rental_listings.service.save_listing`` whenever a listing is
    saved with an off-market date, before it commits, so the listing and its
    cancellations are saved together. Showings that have started are left as
    they are. The texts are queued for the job every minute. Callers that act
    for a person ask ``off_market_effect`` first and have the person agree.
    """
    now = now or clock()
    rules = await listings_service.read_showing_rules(session)
    await bookings.lock(session, bookings.BOOKING_LOCK)
    showing_ids = await _off_market_showing_ids(session, unit, off_market_on, now)
    if not showing_ids:
        return 0
    cancelled, queued, _host = await _cancel_showings(
        session, showing_ids, reason="off_market", rules=rules, now=now
    )
    logfire.info(
        "showings cancelled: unit off the market",
        unit=unit,
        showings=len(showing_ids),
        attendees_cancelled=cancelled,
        texts_queued=len(queued),
    )
    return len(showing_ids)
