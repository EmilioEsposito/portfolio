"""The host's calendar event for a showing, built from the showing as it is now.

A host event notice (``host_event`` when the first person books,
``host_event_update`` for each later change) is rendered again when the
outbox delivers it: the summary and description come from the showing's
attendees still booked, with their current mobiles and emails, and whether
it creates the event or updates it is decided then, from the event id saved
on the showing. So a showing has one event whatever order its notices are
released in, and a corrected mobile or a withdrawn attendee shows in the
next delivery.

Rendering only reads; ``outbox.py`` saves what was rendered on the notice and
is the only code that hands it to a calendar. Nothing here imports the outbox.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import wording
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.schema import CalendarPayload

CREATE_KIND = "host_event"
UPDATE_KIND = "host_event_update"
KINDS = frozenset({CREATE_KIND, UPDATE_KIND})
NO_ONE_BOOKED = "Not sent: the showing has no one booked, or is cancelled."
EARLIER_UNKNOWN = (
    "Not sent: an earlier attempt to create the host's event may have gone out. "
    "Check the calendar before sending it again."
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
