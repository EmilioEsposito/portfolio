"""What a message follows must really have gone out before it goes to anyone.

A captured message counts as told everywhere a follow-up is queued or prepared,
so capture mode keeps a booking's cancellations and changes too. That is not
enough once something is really sent: the residents of a home whose new-showing
text was only captured must not get its cancellation.

So the outbox asks ``missing`` about a notice only once the guard has answered
send, and cancels it with the note when what it follows was captured and never
sent. A notice the guard captures is never asked about.

| A message | Follows | Goes when |
| --- | --- | --- |
| the residents' cancellation | their new-showing text, or a reminder that listed the showing | one of them went |
| a host's event or a prospect's invite cancelled, an old host's event cancelled | the event | there is an event to cancel |
| a host's event update because someone left, with no event to change | the host's event | an earlier host notice was not just captured |
| a host's text that someone left, that the showing is cancelled or that it moved away | anything that told that host of the showing | something did, or nothing was captured for them |

"Went" is sent, may have gone out (``unknown``) or being sent now. Everything
else says something by itself: a confirmation, a reminder, a prospect's
cancellation (they booked it themselves), "someone booked", "now yours to host",
and a host's event update for someone still booked, which creates the event when
there is none, with everyone booked.

Nothing here sends or changes anything.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import delivery_checks, host_event, residents
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice

HOST_NEVER_TOLD = (
    "Not sent: the host's messages about this showing were captured and never sent, "
    "so there is nothing to change."
)
# The host's texts that only make sense to a host who knows of the showing.
HOST_CHANGE_TEXTS = frozenset(
    {
        delivery_checks.HOST_LEFT_KIND,
        delivery_checks.HOST_SHOWING_CANCELLED_KIND,
        delivery_checks.HOST_MOVED_FROM_KIND,
    }
)
# What tells a host of one showing, beside the morning text that lists it.
TELLS_THE_HOST = host_event.KINDS | {
    delivery_checks.HOST_JOIN_KIND,
    delivery_checks.HOST_MOVED_TO_KIND,
}


def _went(status: str, started: object) -> bool:
    return status in ("sent", "unknown") or (status == "pending" and started is not None)


async def _residents_told(session: AsyncSession, showing_id: int) -> bool:
    rows = await session.execute(
        select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
            or_(
                (ShowingNotice.showing_id == showing_id)
                & (ShowingNotice.kind == residents.NEW_SHOWING_KIND),
                (ShowingNotice.kind == delivery_checks.RESIDENT_REMINDER_KIND)
                & ShowingNotice.payload["showing_ids"].contains([showing_id]),
            )
        )
    )
    return any(_went(status, started) for status, started in rows)


async def _host_only_captured(session: AsyncSession, notice: ShowingNotice, agent_id: int) -> bool:
    """Something else for this host about the showing was captured, and nothing went."""
    showing_id = notice.showing_id
    rows = (
        await session.execute(
            select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
                ShowingNotice.id != notice.id,
                or_(
                    (ShowingNotice.showing_id == showing_id)
                    & ShowingNotice.kind.in_(TELLS_THE_HOST)
                    & (
                        (ShowingNotice.recipient_key == f"agent:{agent_id}")
                        | ShowingNotice.recipient_key.startswith(
                            f"agent:{agent_id}:", autoescape=True
                        )
                    ),
                    (ShowingNotice.kind == delivery_checks.HOST_MORNING_KIND)
                    & ShowingNotice.recipient_key.startswith(
                        f"agent:{agent_id}:morning:", autoescape=True
                    )
                    & ShowingNotice.payload["showing_ids"].contains([showing_id]),
                ),
            )
        )
    ).all()
    return any(status == "captured" for status, _ in rows) and not any(
        _went(status, started) for status, started in rows
    )


async def missing(session: AsyncSession, notice: ShowingNotice, event_id: str | None) -> str | None:
    """The note to cancel ``notice`` with, when it is about to be really sent and what
    it follows never was; None when it may go. ``event_id``: the calendar event it
    applies to, as the outbox found it, or None when there is none."""
    kind = notice.kind
    if kind == residents.CANCELLED_KIND:
        told = await _residents_told(session, notice.showing_id)
        return None if told else residents.NEVER_TOLD_NOTE
    if kind in host_event.CANCEL_KINDS | {host_event.MOVED_KIND}:
        return None if event_id else host_event.NEVER_CREATED
    if kind == host_event.UPDATE_KIND and event_id is None:
        status = await session.scalar(
            select(ShowingAttendee.status).where(ShowingAttendee.id == notice.attendee_id)
        )
        if status == "booked":
            return None  # brings someone, so it creates the event with everyone booked
    elif kind not in HOST_CHANGE_TEXTS:
        return None
    if kind == delivery_checks.HOST_MOVED_FROM_KIND:
        agent_id = delivery_checks.named_agent(notice.recipient_key)
    else:
        agent_id = await session.scalar(
            select(Showing.agent_id).where(Showing.id == notice.showing_id)
        )
    if await _host_only_captured(session, notice, agent_id):
        return HOST_NEVER_TOLD
    return None
