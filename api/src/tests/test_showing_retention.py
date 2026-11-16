"""Removing prospects' details 12 months after their showing.

Showings are booked through the public and team routes on dates in the past
(April and May 2025), so the database's own clock is well past them; the
removal step is then run with a moved clock, which can only make its cut-off
earlier than the database's. Rows are checked field by field. Capture mode:
nothing is sent anywhere, and every name, number and address is made up.
"""

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import retention
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.tests.showing_helpers import LOCAL
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    booking,
    rows,
)
from api.src.tests.test_showing_reminders import RESIDENT, book, robin, run
from api.src.tests.test_showing_reminders import world as reminder_world
from api.src.tests.test_showing_team_actions import caller, team_book

world = reminder_world

PUBLIC = "/api/showing-bookings/public"
# A Wednesday a year and a half before the tests were written, and one a month later.
OLD_DAY = date(2025, 4, 9)
NEWER_DAY = date(2025, 5, 7)


def on(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL)


OLD_START = on(OLD_DAY, 16)
NEWER_START = on(NEWER_DAY, 16)
ONE_YEAR_LATER = on(date(2026, 4, 9), 16)  # 12 months after OLD_START


async def book_old_showings(world: PublicWorld) -> dict:
    """Two prospects and a caller in a showing on OLD_DAY, a resident told of it, and a
    second showing a month later. Delivered in capture mode, the job run past both."""
    world.residents.add("Morgan Ellis", RESIDENT, unit=UNIT)
    world.now = on(OLD_DAY - timedelta(days=1), 14)
    alex = await book(world, booking(OLD_START.isoformat()))
    robin_booking = await book(world, robin(OLD_START))
    answer = await team_book(
        world, caller(OLD_START, mobile="412-555-0119", email="casey@example.com", texts=True)
    )
    assert answer.status_code == 201, answer.text
    await run(world, on(OLD_DAY - timedelta(days=1), 14, 10))
    await run(world, on(OLD_DAY, 8))  # the host's morning text
    await run(world, on(OLD_DAY, 18, 30))  # the apply texts

    world.now = on(NEWER_DAY - timedelta(days=1), 14)
    newer = await book(world, booking(NEWER_START.isoformat()))
    await run(world, on(NEWER_DAY - timedelta(days=1), 14, 10))
    return {"alex": alex, "robin": robin_booking, "newer": newer}


async def snapshot() -> dict:
    showings, attendees, notices = await rows()
    return {
        "showings": [(s.id, s.unit, s.starts_at, s.status, s.agent_id) for s in showings],
        "attendees": [
            (
                a.id,
                a.showing_id,
                a.status,
                a.attendance,
                a.source,
                a.texts_accepted,
                a.texts_consent_at,
                a.texts_consent_version,
                a.cancel_reason,
                a.created_at,
            )
            for a in attendees
        ],
        "notices": [
            (n.id, n.kind, n.channel, n.showing_id, n.attendee_id, n.status, n.delivered_at)
            for n in notices
        ],
    }


async def of_showing(starts_at: datetime):
    async with AsyncSessionFactory() as session:
        showing = (
            await session.scalars(select(Showing).where(Showing.starts_at == starts_at))
        ).one()
        attendees = list(
            await session.scalars(
                select(ShowingAttendee)
                .where(ShowingAttendee.showing_id == showing.id)
                .order_by(ShowingAttendee.id)
            )
        )
        notices = list(
            await session.scalars(
                select(ShowingNotice)
                .where(ShowingNotice.showing_id == showing.id)
                .order_by(ShowingNotice.id)
            )
        )
    return showing, attendees, notices


def assert_untouched(attendees, notices) -> None:
    for person in attendees:
        assert person.name != retention.REMOVED_NAME
        assert person.mobile and person.cancel_token
        assert not person.cancel_token.startswith(retention.REMOVED_TOKEN_PREFIX)
    for notice in notices:
        assert notice.recipient != retention.REMOVED_RECIPIENT
        assert retention.REMOVED not in str(notice.payload)


async def lookup(world: PublicWorld, token: str):
    async with world.client() as client:
        return await client.post(f"{PUBLIC}/bookings/lookup", json={"cancel_token": token})


@pytest.mark.asyncio
async def test_details_stay_until_12_months_less_a_day(world):
    tokens = await book_old_showings(world)
    before = await snapshot()

    report = await retention.remove_old_details(now=ONE_YEAR_LATER - timedelta(days=1))

    assert (report.showings, report.attendees, report.notices) == (0, 0, 0)
    assert await snapshot() == before
    _, attendees, notices = await of_showing(OLD_START)
    assert_untouched(attendees, notices)
    world.now = ONE_YEAR_LATER
    assert (await lookup(world, tokens["alex"]["cancel_token"])).status_code == 200


@pytest.mark.asyncio
async def test_details_are_removed_at_12_months_field_by_field(world):
    await book_old_showings(world)
    before = await snapshot()
    _, old_attendees, old_notices = await of_showing(OLD_START)
    assert len(old_attendees) == 3
    kinds = {notice.kind for notice in old_notices}
    # Prospects', the host's and the resident's notices are all there to be cleared.
    assert {"prospect_confirmation", "host_event", "resident_new_showing"} <= kinds
    assert {"prospect_apply", "prospect_invite", "host_morning"} <= kinds

    report = await retention.remove_old_details(now=ONE_YEAR_LATER)

    assert (report.showings, report.attendees, report.notices) == (1, 3, len(old_notices))
    # Rows, ids, times, statuses and counts are kept.
    assert await snapshot() == before
    showing, attendees, notices = await of_showing(OLD_START)
    assert (showing.unit, showing.starts_at, showing.status) == (UNIT, OLD_START, "scheduled")
    tokens_before = {a.id: a.cancel_token for a in old_attendees}
    for person in attendees:
        assert person.name == retention.REMOVED_NAME
        assert person.mobile is None
        assert person.email is None
        assert person.user_agent is None
        assert person.signature_agent is None
        assert person.client_ip_hash is None
        assert person.lead_token is None
        assert person.texts_consent_page is None
        assert person.requirement_confirmations == []
        assert person.cancel_token.startswith(retention.REMOVED_TOKEN_PREFIX)
        assert person.cancel_token != tokens_before[person.id]
        # Kept: how they booked, their consent's time and version, attendance.
        assert person.status == "booked" and person.source in {"page", "team"}
        assert person.texts_accepted is True and person.texts_consent_at is not None
    for notice in notices:
        assert notice.recipient == retention.REMOVED_RECIPIENT
        assert notice.payload["removed"] is True
        text = str(notice.payload)
        for detail in (ALEX_MOBILE, ROBIN_MOBILE, RESIDENT, ALEX_EMAIL, "Alex", "Robin", "Casey"):
            assert detail not in text
    # The showing a month later, 11 months old then, is untouched.
    _, newer_attendees, newer_notices = await of_showing(NEWER_START)
    assert_untouched(newer_attendees, newer_notices)


@pytest.mark.asyncio
async def test_a_removed_bookings_cancel_link_answers_not_found(world):
    tokens = await book_old_showings(world)
    await retention.remove_old_details(now=ONE_YEAR_LATER)
    world.now = ONE_YEAR_LATER
    for name in ("alex", "robin"):
        answer = await lookup(world, tokens[name]["cancel_token"])
        assert answer.status_code == 404, answer.text
        assert answer.json()["detail"]["code"] == "unknown_booking"
    # The newer booking's link still works.
    assert (await lookup(world, tokens["newer"]["cancel_token"])).status_code == 200


@pytest.mark.asyncio
async def test_running_it_twice_changes_nothing_the_second_time(world):
    await book_old_showings(world)
    first = await retention.remove_old_details(now=ONE_YEAR_LATER)
    assert first.showings == 1
    after_first = await rows()

    second = await retention.remove_old_details(now=ONE_YEAR_LATER)
    assert (second.showings, second.attendees, second.notices) == (0, 0, 0)
    after_second = await rows()
    for one, two in zip(after_first, after_second, strict=True):
        assert [vars_of(row) for row in one] == [vars_of(row) for row in two]


@pytest.mark.asyncio
async def test_a_month_later_the_second_showing_goes_too(world):
    await book_old_showings(world)
    await retention.remove_old_details(now=ONE_YEAR_LATER)
    report = await retention.remove_old_details(now=ONE_YEAR_LATER + timedelta(days=28))
    assert report.showings == 1
    _, attendees, _ = await of_showing(NEWER_START)
    assert [person.name for person in attendees] == [retention.REMOVED_NAME]


@pytest.mark.asyncio
async def test_a_later_clock_cannot_remove_a_younger_showing(world):
    """The cut-off is 12 months before the database's clock at the latest: a far
    later ``now`` removes only what the database's clock already allows."""
    recent = await book(world)  # Wednesday 14 April 2027, ahead of the database's clock
    report = await retention.remove_old_details(now=datetime(2099, 1, 1, tzinfo=LOCAL))
    assert report.showings == 0
    _, attendees, _ = await rows()
    (person,) = attendees
    assert person.cancel_token == recent["cancel_token"]
    assert person.mobile == ALEX_MOBILE


@pytest.mark.asyncio
async def test_the_daily_job_uses_the_database_clock_alone(world):
    """The job passes no time: showings over 12 months old by the database's clock go."""
    await book_old_showings(world)
    await retention.scheduled_run()
    _, old_attendees, _ = await of_showing(OLD_START)
    _, newer_attendees, _ = await of_showing(NEWER_START)
    # Both are well over 12 months old by the database's clock.
    assert {p.name for p in old_attendees + newer_attendees} == {retention.REMOVED_NAME}


def vars_of(row) -> dict:
    return {
        key: value
        for key, value in vars(row).items()
        if not key.startswith("_") and key != "updated_at"
    }
