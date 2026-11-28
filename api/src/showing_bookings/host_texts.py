"""A text to the host on every change to their showings, from the AI line.

The host's calendar event follows every change, and each change also texts the
host at their own mobile (``showing_agents.mobile``):

| When | Kind | The change, in the text |
| --- | --- | --- |
| Someone books (a join, or a new showing) | ``host_late_join`` | who booked which showing |
| Someone cancels, or the team removes them | ``host_late_cancel`` | who left which showing, and that it is cancelled when no one is left |
| The team cancels the showing, or the unit goes off the market | ``host_showing_cancelled`` | which showing, and why |
| The team gives the showing to another host | ``host_moved_to`` and ``host_moved_from`` | to the new host that it is theirs; to the old host that it is not |

A text that someone left, that the showing is cancelled or that it moved away
is queued only when something already told that host of the showing (their
event, or a text, sent or captured: ``told.host_heard_of``); a booking stopped
before anything went tells the host nothing.

(The kinds keep the names they had when only changes inside 2 hours texted.)

Each is queued in the caller's transaction with its own sentence
(``TextPayload.line``) and goes at once from 8 AM to 9 PM in the showing
rules' time zone; after 9 PM it waits for 8 AM. When it goes, every other text
due for the same host goes with it as one text (``digests.py``): what changed,
in order, then their upcoming showings; at 8 AM the changes open the morning
text instead, which lists the day's showings. An agent with no mobile gets
none. A text names the people who changed something and the showing, never a
number. One per host and person (or per showing for a cancelled showing), by
the notice's unique key. At delivery (``delivery_checks``) it goes to the
showing's host then, only before the start, and "someone booked" only while
that person is still booked.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import outbox, told, wording
from api.src.showing_bookings.delivery_checks import (
    HOST_JOIN_KIND,
    HOST_LEFT_KIND,
    HOST_MOVED_FROM_KIND,
    HOST_MOVED_TO_KIND,
    HOST_SHOWING_CANCELLED_KIND,
)
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.schema import TextPayload


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
    line: str,
    mobile: str,
    recipient_key: str,
    attendee_id: int | None,
    rules: ShowingRules,
    now: datetime,
) -> int | None:
    """Queue one change for the host; None once the showing has started."""
    if showing.starts_at <= now:
        return None
    notice = await outbox.enqueue(
        session,
        kind=kind,
        channel="sms",
        showing_id=showing.id,
        attendee_id=attendee_id,
        recipient=mobile,
        recipient_key=recipient_key,
        payload=TextPayload(body=line, line=line, daytime_timezone=rules.timezone),
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
    """Someone booked. Returns the text's id, or None when none is owed."""
    mobile = await _host_mobile(session, showing)
    if not mobile:
        return None
    return await _queue(
        session,
        showing,
        kind=HOST_JOIN_KIND,
        line=wording.host_joined_line(_visit(showing, rules), attendee.name),
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee.id}",
        attendee_id=attendee.id,
        rules=rules,
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
    """Someone cancelled, or the team removed them; called after the attendee (and
    the showing, when they were the last) is marked cancelled. Only when something
    told the host of the showing already."""
    mobile = await _host_mobile(session, showing)
    if not mobile or not await told.host_heard_of(session, showing.id, showing.agent_id):
        return None
    await session.flush()
    line = wording.host_left_line(
        _visit(showing, rules),
        attendee.name,
        by_team=by_team,
        booked=await _booked(session, showing.id),
    )
    return await _queue(
        session,
        showing,
        kind=HOST_LEFT_KIND,
        line=line,
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee.id}",
        attendee_id=attendee.id,
        rules=rules,
        now=now,
    )


async def queue_showing_cancelled(
    session: AsyncSession, showing: Showing, *, rules: ShowingRules, now: datetime
) -> int | None:
    """The team cancelled the showing, or its unit went off the market. Only when
    something told the host of the showing already."""
    mobile = await _host_mobile(session, showing)
    if not mobile or not await told.host_heard_of(session, showing.id, showing.agent_id):
        return None
    return await _queue(
        session,
        showing,
        kind=HOST_SHOWING_CANCELLED_KIND,
        line=wording.host_showing_cancelled_line(_visit(showing, rules), showing.cancel_reason),
        mobile=mobile,
        recipient_key=f"agent:{showing.agent_id}",
        attendee_id=None,
        rules=rules,
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
    """The team gave the showing to ``new_host``; called after the showing names its
    new host. One text to each host with a mobile; ``move`` is the change's own
    key, so a showing moved twice texts each time."""
    visit = _visit(showing, rules)
    queued = []
    old_heard = await told.host_heard_of(session, showing.id, old_host.id)
    for host, kind, line in (
        (new_host, HOST_MOVED_TO_KIND, wording.host_moved_to_line(visit)),
        (old_host, HOST_MOVED_FROM_KIND, wording.host_moved_from_line(visit)),
    ):
        if not host.mobile or (host is old_host and not old_heard):
            continue
        notice_id = await _queue(
            session,
            showing,
            kind=kind,
            line=line,
            mobile=host.mobile,
            recipient_key=f"agent:{host.id}:moved:{move}",
            attendee_id=None,
            rules=rules,
            now=now,
        )
        if notice_id is not None:
            queued.append(notice_id)
    return queued
