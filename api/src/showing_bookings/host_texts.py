"""A text to the host when their showing changes less than 2 hours before it starts.

The host's calendar event follows every change, but inside 2 hours they may
not look at it again, so a change then also texts them, from the team number,
at their own mobile (``showing_agents.mobile``):

| When, inside 2 hours | Kind | The text |
| --- | --- | --- |
| Someone books (a join, or a new showing) | ``host_late_join`` | who, which showing, how many are booked now |
| Someone cancels, or the team removes them | ``host_late_cancel`` | who, which showing, how many are still booked, or that it is cancelled |
| The team cancels the showing, or the unit goes off the market | ``host_showing_cancelled`` | which showing, and why |
| The team gives the showing to another host | ``host_moved_to`` and ``host_moved_from`` | to the new host that it is theirs and how many are booked; to the old host that it is not |

Each is queued in the caller's transaction and delivered at once after it
commits (an off-market save leaves it to the job every minute). An agent with
no mobile gets none. A text says no more than the host's event already shows:
a name and the showing, never a number. One per host and person (or per
showing for a cancelled showing), by the notice's unique key. At delivery
(``delivery_checks``) it goes to the showing's host then, only before the start,
and "someone booked" only while that person is still booked.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import outbox, wording
from api.src.showing_bookings.delivery_checks import (
    HOST_JOIN_KIND,
    HOST_LEFT_KIND,
    HOST_MOVED_FROM_KIND,
    HOST_MOVED_TO_KIND,
    HOST_SHOWING_CANCELLED_KIND,
)
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.schema import TextPayload

LATE = timedelta(hours=2)


def is_late(showing: Showing, now: datetime) -> bool:
    """The showing starts within 2 hours from ``now`` and has not started."""
    return now < showing.starts_at <= now + LATE


def _visit(showing: Showing, rules: ShowingRules) -> wording.Visit:
    return wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=rules.timezone,
    )


async def _host_mobile(session: AsyncSession, showing: Showing) -> str | None:
    return await session.scalar(
        select(ShowingAgentRecord.mobile).where(ShowingAgentRecord.id == showing.agent_id)
    )


async def _booked(session: AsyncSession, showing_id: int) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(ShowingAttendee)
        .where(ShowingAttendee.showing_id == showing_id, ShowingAttendee.status == "booked")
    )


async def _queue(
    session: AsyncSession,
    showing: Showing,
    *,
    kind: str,
    body: str,
    mobile: str,
    recipient_key: str,
    attendee_id: int | None,
    now: datetime,
) -> int:
    notice = await outbox.enqueue(
        session,
        kind=kind,
        channel="sms",
        showing_id=showing.id,
        attendee_id=attendee_id,
        recipient=mobile,
        recipient_key=recipient_key,
        payload=TextPayload(body=body),
        now=now,
    )
    return notice.id


async def queue_join(
    session: AsyncSession,
    showing: Showing,
    attendee: ShowingAttendee,
    *,
    rules: ShowingRules,
    now: datetime,
) -> int | None:
    """Someone booked inside 2 hours. Returns the text's id, or None when none is owed."""
    if not is_late(showing, now):
        return None
    mobile = await _host_mobile(session, showing)
    if not mobile:
        return None
    await session.flush()
    body = wording.host_joined_text(
        _visit(showing, rules), attendee.name, await _booked(session, showing.id)
    )
    return await _queue(
        session,
        showing,
        kind=HOST_JOIN_KIND,
        body=body,
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee.id}",
        attendee_id=attendee.id,
        now=now,
    )


async def queue_left(
    session: AsyncSession,
    showing: Showing,
    attendee: ShowingAttendee,
    *,
    by_team: bool,
    rules: ShowingRules,
    now: datetime,
) -> int | None:
    """Someone cancelled, or the team removed them, inside 2 hours; called after the
    attendee (and the showing, when they were the last) is marked cancelled."""
    if not is_late(showing, now):
        return None
    mobile = await _host_mobile(session, showing)
    if not mobile:
        return None
    await session.flush()
    body = wording.host_left_text(
        _visit(showing, rules),
        attendee.name,
        by_team=by_team,
        booked=await _booked(session, showing.id),
    )
    return await _queue(
        session,
        showing,
        kind=HOST_LEFT_KIND,
        body=body,
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee.id}",
        attendee_id=attendee.id,
        now=now,
    )


async def queue_showing_cancelled(
    session: AsyncSession, showing: Showing, *, rules: ShowingRules, now: datetime
) -> int | None:
    """The team cancelled the showing, or its unit went off the market, inside 2 hours."""
    if not is_late(showing, now):
        return None
    mobile = await _host_mobile(session, showing)
    if not mobile:
        return None
    body = wording.host_showing_cancelled_text(_visit(showing, rules), showing.cancel_reason)
    return await _queue(
        session,
        showing,
        kind=HOST_SHOWING_CANCELLED_KIND,
        body=body,
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}",
        attendee_id=None,
        now=now,
    )


async def queue_moved(
    session: AsyncSession,
    showing: Showing,
    old_host: ShowingAgentRecord,
    new_host: ShowingAgentRecord,
    *,
    move: str,
    rules: ShowingRules,
    now: datetime,
) -> list[int]:
    """The team gave the showing to ``new_host`` inside 2 hours; called after the
    showing names its new host. One text to each host with a mobile; ``move`` is
    the change's own key, so a showing moved twice texts each time."""
    if not is_late(showing, now):
        return []
    visit = _visit(showing, rules)
    queued = []
    if new_host.mobile:
        body = wording.host_moved_to_text(visit, await _booked(session, showing.id))
        queued.append(
            await _queue(
                session,
                showing,
                kind=HOST_MOVED_TO_KIND,
                body=body,
                mobile=new_host.mobile,
                recipient_key=f"agent:{new_host.id}:moved:{move}",
                attendee_id=None,
                now=now,
            )
        )
    if old_host.mobile:
        queued.append(
            await _queue(
                session,
                showing,
                kind=HOST_MOVED_FROM_KIND,
                body=wording.host_moved_from_text(visit),
                mobile=old_host.mobile,
                recipient_key=f"agent:{old_host.id}:moved:{move}",
                attendee_id=None,
                now=now,
            )
        )
    return queued
