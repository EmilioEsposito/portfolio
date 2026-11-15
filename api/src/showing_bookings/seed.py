"""Made-up showing agents and a made-up week of their calendars, for non-production use.

``api/seed_db.py`` saves ``SAMPLE_AGENTS`` in local and preview databases.
``fill_sample_week`` puts a week of busy blocks for them in an
``InMemoryCalendar``, so tests and later steps of the booking work can compute
open times without reading a real calendar. Names, numbers and addresses are
fictional.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from api.src.rental_listings.schema import PreferredWindow
from api.src.showing_bookings.fakes import InMemoryCalendar
from api.src.showing_bookings.schema import ShowingAgent

TIMEZONE = ZoneInfo("America/New_York")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]

# Hours and preferred windows are start times, as on listings: Jane's last
# weekday start is 6:30 PM, so her last showing ends at 7.
SAMPLE_AGENTS: tuple[ShowingAgent, ...] = (
    ShowingAgent(
        name="Jane Doe",
        email="jane.doe@example.com",
        mobile="+14125550101",
        busy_calendar_ids=["jane.doe@example.com"],
        weekly_hours=[
            PreferredWindow(days=WEEKDAYS, start=time(16, 0), end=time(18, 30)),
            PreferredWindow(days=["sat"], start=time(10, 0), end=time(13, 30)),
        ],
        preferred_windows=[PreferredWindow(days=WEEKDAYS, start=time(16, 0), end=time(17, 0))],
        priority=1,
    ),
    ShowingAgent(
        name="Sam Rivera",
        email="sam.rivera@example.com",
        mobile="+14125550102",
        busy_calendar_ids=["sam.rivera@example.com"],
        weekly_hours=[
            PreferredWindow(days=[*WEEKDAYS, "sat"], start=time(9, 0), end=time(17, 30)),
        ],
        priority=2,
    ),
)

# (calendar, weekday with Monday as 0, start, end) in local time.
_SAMPLE_WEEK: tuple[tuple[str, int, time, time], ...] = (
    # Jane works a day job on weekdays and has a standing Wednesday call at 5.
    *(("jane.doe@example.com", day, time(9, 0), time(15, 30)) for day in range(5)),
    ("jane.doe@example.com", 2, time(17, 0), time(17, 30)),
    ("jane.doe@example.com", 5, time(12, 0), time(13, 0)),
    # Sam is out Tuesday and Thursday afternoons and all of Friday morning.
    ("sam.rivera@example.com", 1, time(13, 0), time(17, 0)),
    ("sam.rivera@example.com", 3, time(13, 0), time(17, 0)),
    ("sam.rivera@example.com", 4, time(9, 0), time(12, 0)),
)


def fill_sample_week(calendar: InMemoryCalendar, week_of: date) -> InMemoryCalendar:
    """Add the sample agents' busy blocks for the Monday-to-Saturday week containing ``week_of``."""
    monday = week_of - timedelta(days=week_of.weekday())
    for calendar_id, weekday, start, end in _SAMPLE_WEEK:
        day = monday + timedelta(days=weekday)
        calendar.add_busy(
            calendar_id,
            datetime.combine(day, start, TIMEZONE),
            datetime.combine(day, end, TIMEZONE),
        )
    return calendar
