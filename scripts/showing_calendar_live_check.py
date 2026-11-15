"""Check a calendar connection against Google by hand: one free/busy read, then one event
created and cancelled. A manual check, never part of a test run.

Run from the repository root, against the database and environment where the account
was connected, since they hold its encrypted token and the key that reads it:

    uv run python -m scripts.showing_calendar_live_check --account ACCOUNT --guest GUEST

ACCOUNT is the connected Google account. GUEST must be one of the test recipients in
SHOWING_TEST_EMAILS: any other address is refused before anything reaches Google, and
the event invites the guest and no one else, so Google sends that address an
invitation and then a cancellation. The event goes on the account's "Sernia showings"
calendar. Nothing here prints a token.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import connections
from api.src.showing_bookings.models import CalendarConnection
from api.src.showing_bookings.providers import CalendarUnreadable, EventDraft, NotSent
from api.src.showing_bookings.schema import normalize_email
from api.src.showing_bookings.settings import read_test_recipients

EVENT_SUMMARY = "Sernia showings live check"
EVENT_DESCRIPTION = (
    "A test event from the showing booking system's live check. It is cancelled straight away."
)


class Refused(Exception):
    """The check will not run; the message says why."""


async def run(account: str, guest: str, *, now: datetime) -> list[str]:
    """Read free/busy, then create and cancel one event, and say what happened.

    Raises ``Refused`` before anything reaches Google.
    """
    guest_address = normalize_email(guest)
    if guest_address is None or guest_address not in read_test_recipients():
        raise Refused("The guest must be one of the test recipients in SHOWING_TEST_EMAILS.")
    async with AsyncSessionFactory() as session:
        connection = await session.scalar(
            select(CalendarConnection).where(
                CalendarConnection.google_email == normalize_email(account)
            )
        )
    if connection is None:
        raise Refused(f"No calendar connection for {account}.")
    problem = connections.unusable_reason(connection)
    if problem is not None:
        raise Refused(f"{account} cannot be used here: {problem}")
    if not connection.showings_calendar_id:
        raise Refused(f"{account} has no Sernia showings calendar. Reconnect it.")

    calendar = connections.calendar_for_connection(connection)
    lines: list[str] = []
    try:
        busy = await calendar.busy_blocks([connection.google_email], now, now + timedelta(days=1))
    except CalendarUnreadable as unreadable:
        reason = unreadable.unreadable.get(connection.google_email, "unreadable")
        lines.append(f"Free/busy: could not read the account's own calendar: {reason}")
    else:
        blocks = busy[connection.google_email]
        lines.append(f"Free/busy: read; {len(blocks)} busy block(s) in the next day.")
        lines += [
            f"  busy {block.start.isoformat()} to {block.end.isoformat()}" for block in blocks[:5]
        ]

    starts_at = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    event = EventDraft(
        summary=EVENT_SUMMARY,
        description=EVENT_DESCRIPTION,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        attendees=[guest_address],
    )
    try:
        event_id = await calendar.create_event(connection.showings_calendar_id, event)
    except NotSent as refused:
        lines.append(f"Create: refused ({refused.reason}): {refused.detail}")
        return lines
    lines.append(
        f"Create: made event {event_id} on the showings calendar, inviting {guest_address}."
    )
    try:
        await calendar.cancel_event(connection.showings_calendar_id, event_id)
    except NotSent as refused:
        lines.append(
            f"Cancel: refused ({refused.reason}): {refused.detail} Delete event {event_id} by hand."
        )
    else:
        lines.append("Cancel: done. Google sends the guest the cancellation.")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One free/busy read, then one event created and cancelled, through a "
        "calendar connection."
    )
    parser.add_argument("--account", required=True, help="the connected Google account")
    parser.add_argument("--guest", required=True, help="an address in SHOWING_TEST_EMAILS")
    args = parser.parse_args(argv)
    try:
        lines = asyncio.run(run(args.account, args.guest, now=datetime.now(UTC)))
    except Refused as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
