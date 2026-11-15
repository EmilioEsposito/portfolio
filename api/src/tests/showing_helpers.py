"""Shared set-up for the showing booking tests. Not a test module.

Everything here is made up: names, units, phone numbers on the 555-01xx range
and example.com addresses.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from api.src.database.app_settings import AppSetting
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.settings import SETTINGS_KEY

LOCAL = ZoneInfo("America/New_York")
SHOWING_DAY = date(2027, 4, 14)  # a Wednesday
UNIT = "320-03"
PROSPECT_PHONE = "+14125550117"
RESIDENT_PHONE = "+14125550142"
TEAM_PHONE = "+14125550150"
TEAM_EMAIL = "team.test@example.com"


def at(hour: int, minute: int = 0, day: date = SHOWING_DAY) -> datetime:
    """A local time on the showing day, timezone aware."""
    return datetime.combine(day, time(hour, minute), LOCAL)


async def clear_showing_data() -> None:
    async with AsyncSessionFactory() as session:
        for table in (
            ShowingNotice,
            ShowingAttendee,
            Showing,
            ShowingAgentRecord,
            CalendarConnection,
        ):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting).where(AppSetting.key == SETTINGS_KEY))
        await session.commit()


async def add_agent(name: str = "Jane Doe", email: str = "jane.doe@example.com") -> int:
    async with AsyncSessionFactory() as session:
        agent = ShowingAgentRecord(name=name, email=email, mobile="+14125550101")
        session.add(agent)
        await session.commit()
        return agent.id


async def add_showing(
    agent_id: int,
    *,
    unit: str = UNIT,
    starts_at: datetime | None = None,
    minutes: int = 30,
    status: str = "scheduled",
) -> int:
    starts_at = starts_at or at(16)
    async with AsyncSessionFactory() as session:
        showing = Showing(
            unit=unit,
            address="100 Example Ave",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            agent_id=agent_id,
            status=status,
        )
        session.add(showing)
        await session.commit()
        return showing.id


async def add_attendee(
    showing_id: int,
    mobile: str | None = PROSPECT_PHONE,
    name: str = "Alex Parker",
    *,
    thread: str | None = None,
    source: str = "page",
):
    async with AsyncSessionFactory() as session:
        attendee = ShowingAttendee(
            showing_id=showing_id,
            name=name,
            mobile=mobile,
            email="alex@example.com" if mobile else None,
            source=source,
            texts_accepted=mobile is not None,
            zillow_thread_id=thread,
        )
        session.add(attendee)
        await session.commit()
        return attendee.id


async def store_settings(**fields) -> None:
    async with AsyncSessionFactory() as session:
        await session.merge(AppSetting(key=SETTINGS_KEY, value=fields))
        await session.commit()
