"""Data for the booking page's browser test (apps/web-react-router/e2e/): a reset and a read.

    uv run python -m scripts.showing_browser_test reset
    uv run python -m scripts.showing_browser_test rows

``reset`` makes the database ready for a run: it removes every showing,
attendee and notice, puts the send mode back to capture with the default
limits and public booking at the environment's default, saves one made-up
listing on offer with lease dates (``UNIT``), and adds the sample agents from
``api/src/showing_bookings/seed.py`` if they are missing. With no calendar
connection, open times read the agents' sample calendar, as everywhere outside
production. ``rows`` prints the showings, attendees and notices for ``UNIT`` as
JSON, for the test to check what a booking left.

It refuses to run in production or against a database that is not on this
machine, because ``reset`` deletes showing data. Point it at a disposable
database. Names, numbers and addresses are made up.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
UNIT = "900-01"
ADDRESS = "900 Example Ave"


class Refused(Exception):
    """The script will not touch this database; the message says why."""


def check_target() -> None:
    if os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() == "production":
        raise Refused("Refusing to run in production.")
    for name in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        host = urlparse(os.getenv(name, "")).hostname
        if host not in LOCAL_HOSTS:
            raise Refused(f"{name} must point at a database on this machine.")


def lease_dates(today: date) -> tuple[date, date]:
    """The first of the month two months out, and the first of the month after."""
    first = (today.replace(day=1) + timedelta(days=62)).replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1)


async def reset() -> None:
    from sqlalchemy import delete, select

    from api.src.database.app_settings import AppSetting
    from api.src.database.database import AsyncSessionFactory
    from api.src.rental_listings import service as listings
    from api.src.rental_listings.schema import RentalListing
    from api.src.showing_bookings import seed
    from api.src.showing_bookings.models import (
        Showing,
        ShowingAgentRecord,
        ShowingAttendee,
        ShowingNotice,
    )
    from api.src.showing_bookings.settings import SETTINGS_KEY, ShowingBookingSettings

    earliest, latest = lease_dates(date.today())
    async with AsyncSessionFactory() as session:
        for table in (ShowingNotice, ShowingAttendee, Showing):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting).where(AppSetting.key == SETTINGS_KEY))
        session.add(
            AppSetting(key=SETTINGS_KEY, value=ShowingBookingSettings().model_dump(mode="json"))
        )
        for agent in seed.SAMPLE_AGENTS:
            found = await session.scalar(
                select(ShowingAgentRecord).where(ShowingAgentRecord.email == agent.email)
            )
            if found is None:
                session.add(ShowingAgentRecord(**agent.model_dump(mode="json")))
            else:
                found.active = True
        await session.commit()
        await listings.save_listing(
            session,
            RentalListing(
                unit=UNIT,
                address=ADDRESS,
                asking_rent=1450,
                next_lease_start_earliest=earliest,
                next_lease_start_latest=latest,
            ),
            changed_by="browser-test",
            source="scripts/showing_browser_test.py",
        )


async def rows() -> dict:
    from sqlalchemy import select

    from api.src.database.database import AsyncSessionFactory
    from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice

    async with AsyncSessionFactory() as session:
        showings = list(
            await session.scalars(select(Showing).where(Showing.unit == UNIT).order_by(Showing.id))
        )
        ids = [showing.id for showing in showings]
        attendees = list(
            await session.scalars(
                select(ShowingAttendee)
                .where(ShowingAttendee.showing_id.in_(ids))
                .order_by(ShowingAttendee.id)
            )
        )
        notices = list(
            await session.scalars(
                select(ShowingNotice)
                .where(ShowingNotice.showing_id.in_(ids))
                .order_by(ShowingNotice.id)
            )
        )
    return {
        "showings": [
            {"id": s.id, "starts_at": s.starts_at.isoformat(), "status": s.status} for s in showings
        ],
        "attendees": [
            {
                "showing_id": a.showing_id,
                "name": a.name,
                "mobile": a.mobile,
                "email": a.email,
                "status": a.status,
                "source": a.source,
                "texts_accepted": a.texts_accepted,
                "requirement_confirmations": a.requirement_confirmations,
                "lead_token": a.lead_token,
                "mobile_corrections": a.mobile_corrections,
                "user_agent": a.user_agent,
            }
            for a in attendees
        ],
        "notices": [
            {
                "kind": n.kind,
                "channel": n.channel,
                "status": n.status,
                "recipient": n.recipient,
                "body": (n.payload or {}).get("body"),
            }
            for n in notices
        ],
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["reset", "rows"])
    args = parser.parse_args(arguments)
    try:
        check_target()
    except Refused as refused:
        print(str(refused), file=sys.stderr)
        return 2
    if args.command == "reset":
        asyncio.run(reset())
        print(json.dumps({"unit": UNIT}))
    else:
        print(json.dumps(asyncio.run(rows())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
