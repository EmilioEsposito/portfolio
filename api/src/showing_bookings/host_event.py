"""The host's calendar event for a showing, built from the showing as it is now.

A host event notice (``host_event`` when the first person books,
``host_event_update`` for each later change) is rendered again when the
outbox delivers it: the summary and description come from the showing's
attendees still booked, with their current mobiles and emails, and whether
it creates the event or updates it is decided then, from the event id saved
on the showing. So a showing has one event whatever order its notices are
released in, and a corrected mobile or a withdrawn attendee shows in the
next delivery.

A cancelled showing's event is cancelled by its own notice (``CANCEL_KIND``),
and a prospect's withdrawn invite by ``INVITE_CANCEL_KIND``. ``cancel_check``
decides when such a notice is delivered whether there is an event to cancel,
so a cancellation never creates one.

Rendering only reads; ``outbox.py`` saves what was rendered on the notice and
is the only code that hands it to a calendar. Nothing here imports the outbox.
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import wording
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import CalendarPayload

CREATE_KIND = "host_event"
UPDATE_KIND = "host_event_update"
# The notices rendered from the showing at delivery.
KINDS = frozenset({CREATE_KIND, UPDATE_KIND})
CANCEL_KIND = "host_event_cancel"
# The host notices delivered under a lock on their showing.
SHOWING_LOCKED_KINDS = KINDS | {CANCEL_KIND}
INVITE_KIND = "prospect_invite"
INVITE_CANCEL_KIND = "prospect_invite_cancel"
CANCEL_KINDS = frozenset({CANCEL_KIND, INVITE_CANCEL_KIND})
NO_ONE_BOOKED = "Not sent: the showing has no one booked, or is cancelled."
EARLIER_UNKNOWN = (
    "Not sent: an earlier attempt to create the host's event may have gone out. "
    "Check the calendar before sending it again."
)
NEVER_CREATED = "Not sent: the event was never created, so there is nothing to cancel."
MAYBE_CREATED = (
    "Not sent: an earlier attempt to create the event may have gone out. "
    "Check the calendar and remove it by hand."
)


async def booked_guests(session: AsyncSession, showing_id: int) -> list[wording.Guest]:
    rows = await session.scalars(
        select(ShowingAttendee)
        .where(ShowingAttendee.showing_id == showing_id, ShowingAttendee.status == "booked")
        .order_by(ShowingAttendee.id)
    )
    return [
        wording.Guest(
            name=row.name,
            mobile=row.mobile,
            email=row.email,
            confirmations=[item.get("wording", "") for item in row.requirement_confirmations],
        )
        for row in rows
    ]


async def render(
    session: AsyncSession, showing: Showing, *, calendar_id: str
) -> CalendarPayload | None:
    """The event as it should be now, or None when the showing is cancelled or empty.

    Creates when the showing has no event yet, else updates the one it has.
    """
    if showing.status != "scheduled":
        return None
    guests = await booked_guests(session, showing.id)
    if not guests:
        return None
    host_email = await session.scalar(
        select(ShowingAgentRecord.email).where(ShowingAgentRecord.id == showing.agent_id)
    )
    visit = wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone="UTC",  # the host's summary and description name no time
    )
    return CalendarPayload(
        operation="update" if showing.host_event_id else "create",
        calendar_id=calendar_id,
        event_id=showing.host_event_id,
        summary=wording.host_event_summary(visit, guests),
        description=wording.host_event_description(guests),
        location=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        attendees=[host_email],
    )


async def cancel_check(
    session: AsyncSession, notice: ShowingNotice, *, event_id: str | None
) -> Literal["go", "wait"] | str:
    """Whether a cancellation notice (``CANCEL_KINDS``) can be delivered now.

    "go" when the event exists (``event_id``), or when the notice that would
    have created it was captured, so capture mode keeps the cancellation too.
    "wait" while that notice is being sent. Otherwise the note to cancel the
    notice with: never created, or an earlier create may have gone out.
    """
    if notice.kind == CANCEL_KIND:
        creates = select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
            ShowingNotice.showing_id == notice.showing_id, ShowingNotice.kind.in_(KINDS)
        )
    else:
        creates = select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
            ShowingNotice.attendee_id == notice.attendee_id,
            ShowingNotice.kind == INVITE_KIND,
        )
    rows = (await session.execute(creates)).all()
    if any(status == "pending" and started is not None for status, started in rows):
        return "wait"
    if event_id:
        return "go"
    if any(status == "unknown" for status, _ in rows):
        return MAYBE_CREATED
    if any(status == "captured" for status, _ in rows):
        return "go"
    return NEVER_CREATED
