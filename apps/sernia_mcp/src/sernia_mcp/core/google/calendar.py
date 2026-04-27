"""Google Calendar — read-only event listing.

Lift from ``api/src/sernia_ai/tools/google_tools.py:list_calendar_events``.
Uses ``zoneinfo`` instead of ``pytz`` (sernia_ai's choice) — both compute
the same instant; ``zoneinfo`` is stdlib in Python 3.11+ so we avoid an
extra dep.

Calendar writes (create / delete events) require HITL approval cards and
will land with the approval-flow batch — see ``apps/sernia_mcp/TODOS.md``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from sernia_mcp.clients.google_auth import get_delegated_credentials

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def _get_calendar_service(user_email: str):
    creds = get_delegated_credentials(user_email=user_email, scopes=CALENDAR_SCOPES)
    return build("calendar", "v3", credentials=creds)


async def list_calendar_events_core(
    *,
    user_email: str,
    days_ahead: int = 7,
    days_behind: int = 0,
) -> str:
    """List the user's primary-calendar events around now.

    Always includes all of today's events (window starts at midnight ET of
    ``today - days_behind``, ends at ``now + days_ahead``).

    Args:
        user_email: Workspace user to impersonate.
        days_ahead: Days forward from now (default 7).
        days_behind: Days backward from today's midnight (default 0 — today only).
    """
    service = _get_calendar_service(user_email)
    et_tz = ZoneInfo("America/New_York")
    now = datetime.now(tz=et_tz)
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = start_of_today - timedelta(days=days_behind)
    time_max = now + timedelta(days=days_ahead)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])

    if not events:
        behind_str = f" (including {days_behind} days back)" if days_behind else ""
        return f"No calendar events in the next {days_ahead} days{behind_str}."

    lines: list[str] = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))
        summary = event.get("summary", "(no title)")
        event_id = event.get("id", "?")
        attendees = event.get("attendees", [])
        attendee_str = (
            ", ".join(a.get("email", "?") for a in attendees) if attendees else "none"
        )
        lines.append(
            f"- {summary}\n"
            f"  Start: {start} | End: {end}\n"
            f"  Event ID: {event_id}\n"
            f"  Attendees: {attendee_str}"
        )
    return "\n".join(lines)
