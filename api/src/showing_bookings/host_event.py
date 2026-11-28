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

When the team gives a showing to another host (``team_actions.change_host``),
the old host's event is cancelled by a ``MOVED_KIND`` notice that names the
event itself, and the new host's event starts afresh: the showing's
``host_event_id`` is cleared, and the host notices from before the move no
longer count (``since_last_move``). ``moved_check`` decides at delivery
whether there is an old event to cancel.

A host event notice sent by hand (``captured.py``) is rendered the same way, so
several captured for one showing end as one correct event: the first creates
it, and one that would change nothing is not sent (``already_says``). It goes to
the calendar the event is on, or, when there is none yet, to the one the
host's connection writes to now (``calendar_now``).

Rendering only reads; ``outbox.py`` saves what was rendered on the notice and
is the only code that hands it to a calendar. Nothing here imports the outbox.
"""

from collections.abc import Collection
from typing import Literal

import logfire
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.src.agent_conversations.models import AgentConversation
from api.src.showing_bookings import agents, wording
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import CalendarPayload
from api.src.utils.team_links import lead_thread_path, showing_path, team_url
from api.src.zillow_leads.identity import HANDLER as LEAD_HANDLER

# A showings calendar that names no connected account: the outbox cannot
# write to it, so a notice on it that would be sent fails visibly.
NO_SHOWINGS_CALENDAR = "no-showings-calendar"
CREATE_KIND = "host_event"
UPDATE_KIND = "host_event_update"
# The notices rendered from the showing at delivery.
KINDS = frozenset({CREATE_KIND, UPDATE_KIND})
CANCEL_KIND = "host_event_cancel"
# The old host's event cancelled when the showing moves to another host.
MOVED_KIND = "host_event_moved"
# The host notices delivered under a lock on their showing.
SHOWING_LOCKED_KINDS = KINDS | {CANCEL_KIND, MOVED_KIND}
HOST_CHANGED = "Not sent: the showing moved to another host."
ALREADY_SAYS = "Not sent: the host's event already says this."
INVITE_KIND = "prospect_invite"
CONFIRMATION_KIND = "prospect_confirmation"
# Notices that are for one booking only: checked when delivered, first try or
# retry, and never sent once the booking or its showing is cancelled.
FOR_A_BOOKING_KINDS = frozenset({CONFIRMATION_KIND, INVITE_KIND})
BOOKING_CANCELLED = "Not sent: the booking was cancelled."
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


def since_last_move(showing_id: int):
    """A condition on ``ShowingNotice``: queued since the showing last changed host.

    The host notices of the current host are these; the ones before the last
    ``MOVED_KIND`` notice were for an earlier host's event, which that notice
    cancels. With no move, every notice counts.
    """
    moved = aliased(ShowingNotice)
    last_move = (
        select(func.coalesce(func.max(moved.id), 0))
        .where(moved.showing_id == showing_id, moved.kind == MOVED_KIND)
        .scalar_subquery()
    )
    return ShowingNotice.id > last_move


async def before_last_move(session: AsyncSession, notice: ShowingNotice) -> bool:
    """Whether a host notice was for an earlier host than the showing's own now."""
    return not await session.scalar(
        select(ShowingNotice.id).where(
            ShowingNotice.id == notice.id, since_last_move(notice.showing_id)
        )
    )


async def showings_calendar(session: AsyncSession, agent: ShowingAgentRecord) -> str:
    """The showings calendar an agent's events are written to now."""
    rows = list(await session.scalars(select(CalendarConnection)))
    connection = agents.connection_for(agent, rows)
    if connection is not None and connection.showings_calendar_id:
        return connection.showings_calendar_id
    return NO_SHOWINGS_CALENDAR


async def calendar_now(
    session: AsyncSession, notice: ShowingNotice, agent_id: int, event_id: str | None
) -> str:
    """The calendar a notice sent by hand is for, which may not be the one it was
    captured with: the one ``event_id`` is on, else the one the host's connection
    writes to now, else its own."""
    own = notice.payload.get("calendar_id") or NO_SHOWINGS_CALENDAR
    if event_id:
        made_on = await session.scalar(
            select(ShowingNotice.payload["calendar_id"].astext)
            .where(ShowingNotice.status == "sent", ShowingNotice.provider_id == event_id)
            .order_by(ShowingNotice.id)
            .limit(1)
        )
        return made_on or own
    host = await session.get(ShowingAgentRecord, agent_id)
    current = await showings_calendar(session, host) if host is not None else own
    return own if current == NO_SHOWINGS_CALENDAR else current


async def already_says(session: AsyncSession, showing: Showing, rendered: CalendarPayload) -> bool:
    """Whether the host's event was last sent exactly as ``rendered``."""
    if not showing.host_event_id:
        return False
    last = await session.scalar(
        select(ShowingNotice.payload)
        .where(
            ShowingNotice.showing_id == showing.id,
            ShowingNotice.kind.in_(KINDS),
            ShowingNotice.status == "sent",
            since_last_move(showing.id),
        )
        .order_by(ShowingNotice.delivered_at.desc(), ShowingNotice.id.desc())
        .limit(1)
    )
    if last is None:
        return False
    # Creating the event or changing it, it says the same.
    how = {"operation", "event_id"}
    sent = CalendarPayload.model_validate(last)
    return sent.model_dump(exclude=how) == rendered.model_dump(exclude=how)


async def lead_names(session: AsyncSession, thread_ids: Collection[str]) -> dict[str, str]:
    """The lead's name the Zillow lead agent last recorded for each thread, where it
    recorded one (``outcome.lead.name`` in the lead conversation's metadata).

    Only reads, and never raises: a read that goes wrong answers no names, so
    the host's event is still written, naming no lead.
    """
    if not thread_ids:
        return {}
    thread = AgentConversation.metadata_["lead_thread_id"].as_string()
    name = AgentConversation.metadata_["outcome"]["lead"]["name"].as_string()
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    select(thread, name)
                    .where(
                        AgentConversation.metadata_["handler"].as_string() == LEAD_HANDLER,
                        thread.in_(sorted(thread_ids)),
                        name.is_not(None),
                    )
                    .order_by(AgentConversation.updated_at.desc(), AgentConversation.id)
                )
            ).all()
    except Exception:
        logfire.exception("showing host event: lead names not read")
        return {}
    found: dict[str, str] = {}
    for thread_id, value in rows:
        cleaned = " ".join(str(value or "").split())[:100]
        if cleaned and thread_id not in found:
            found[thread_id] = cleaned
    return found


async def booked_guests(session: AsyncSession, showing_id: int) -> list[wording.Guest]:
    rows = list(
        await session.scalars(
            select(ShowingAttendee)
            .where(ShowingAttendee.showing_id == showing_id, ShowingAttendee.status == "booked")
            .order_by(ShowingAttendee.id)
        )
    )
    names = await lead_names(
        session, {row.zillow_thread_id for row in rows if row.zillow_thread_id}
    )
    return [
        wording.Guest(
            name=row.name,
            mobile=row.mobile,
            email=row.email,
            confirmations=[item.get("wording", "") for item in row.requirement_confirmations],
            source=row.source,
            texts=row.texts_accepted,
            inquiry_url=(
                team_url(lead_thread_path(row.zillow_thread_id)) if row.zillow_thread_id else None
            ),
            lead_name=names.get(row.zillow_thread_id or ""),
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
        description=wording.host_event_description(
            guests, showing_url=team_url(showing_path(showing.id))
        ),
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
            ShowingNotice.showing_id == notice.showing_id,
            ShowingNotice.kind.in_(KINDS),
            since_last_move(notice.showing_id),
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


async def moved_check(session: AsyncSession, notice: ShowingNotice) -> Literal["go", "wait"] | str:
    """Whether the old host's event can be cancelled now (``MOVED_KIND``).

    "go" when the notice names the event, or when the old host's create was
    captured, so capture mode keeps the cancellation too. "wait" while one of
    the old host's notices is being sent. Otherwise the note to cancel the
    notice with: never created, or an earlier create may have gone out.
    """
    if notice.payload.get("event_id"):
        return "go"
    moved = aliased(ShowingNotice)
    previous_move = (
        select(func.coalesce(func.max(moved.id), 0))
        .where(
            moved.showing_id == notice.showing_id,
            moved.kind == MOVED_KIND,
            moved.id < notice.id,
        )
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
                ShowingNotice.showing_id == notice.showing_id,
                ShowingNotice.kind.in_(KINDS),
                ShowingNotice.id > previous_move,
                ShowingNotice.id < notice.id,
            )
        )
    ).all()
    if any(status == "pending" and started is not None for status, started in rows):
        return "wait"
    if any(status == "unknown" for status, _ in rows):
        return MAYBE_CREATED
    if any(status == "captured" for status, _ in rows):
        return "go"
    return NEVER_CREATED
