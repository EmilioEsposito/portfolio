"""Open times: which start times a unit offers, in what order, and who would host.

Uses the test database for agents, connections and showings, and an
``InMemoryCalendar`` in place of Google (``calendar_for_connection`` is
replaced), except where a test says otherwise. The listings and showing rules
are passed in, so the tests do not depend on the listings saved in the
database. Everything is made up.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
import pytest_asyncio

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import PreferredWindow, RentalListing, ShowingRules
from api.src.showing_bookings import connections, open_times, token_crypto
from api.src.showing_bookings.fakes import InMemoryCalendar
from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.tests.showing_google import FakeGoogle, configure
from api.src.tests.showing_helpers import (
    LOCAL,
    UNIT,
    add_attendee,
    add_showing,
    clear_showing_data,
)

TUESDAY = date(2027, 4, 13)
WEDNESDAY = date(2027, 4, 14)
NOW = datetime.combine(TUESDAY, time(14, 0), LOCAL)  # the worked example: Tuesday, 2:00 PM
ADDRESS = "320 Example St"
OTHER_UNIT = "412-01"
JANE = "jane.doe@example.com"
SAM = "sam.rivera@example.com"


def at(hour: int, minute: int = 0, day: date = WEDNESDAY) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL)


def window(days: list[str], start: time, end: time) -> dict:
    return PreferredWindow(days=days, start=start, end=end).model_dump(mode="json")


class World:
    """The listings, rules and calendar the open-times code sees in a test."""

    def __init__(self) -> None:
        self.listings = [RentalListing(unit=UNIT, address=ADDRESS)]
        self.rules = ShowingRules()
        self.calendar = InMemoryCalendar()
        self.connection_id: int | None = None

    def listing(self, unit: str = UNIT, **changes) -> None:
        self.listings = [item for item in self.listings if item.unit != unit]
        address = changes.pop("address", ADDRESS if unit == UNIT else "9 Other Rd")
        self.listings.append(RentalListing(unit=unit, address=address, **changes))


@pytest_asyncio.fixture
async def world(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    open_times.forget_free_busy()
    await clear_showing_data()
    state = World()

    async def read_listings(session):
        return list(state.listings)

    async def read_rules(session, lock=False):
        return state.rules

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    monkeypatch.setattr(connections, "calendar_for_connection", lambda row: state.calendar)
    monkeypatch.setattr(connections, "unusable_reason", lambda row: None)
    async with AsyncSessionFactory() as session:
        row = CalendarConnection(
            google_email="team@example.com",
            google_subject="100000000000000000001",
            connected_by="pat@serniacapital.com",
            connected_at=NOW - timedelta(days=30),
        )
        session.add(row)
        await session.commit()
        state.connection_id = row.id
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def agent(
    name: str = "Jane Doe",
    email: str = JANE,
    *,
    hours: list[dict] | None = None,
    preferred: list[dict] | None = None,
    priority: int = 1,
    calendars: list[str] | None = None,
    active: bool = True,
) -> int:
    async with AsyncSessionFactory() as session:
        record = ShowingAgentRecord(
            name=name,
            email=email,
            busy_calendar_ids=[email] if calendars is None else calendars,
            weekly_hours=hours or [],
            preferred_windows=preferred or [],
            priority=priority,
            active=active,
        )
        session.add(record)
        await session.commit()
        return record.id


async def offers(unit: str = UNIT, now: datetime = NOW) -> open_times.UnitOffers:
    return await open_times.offers_for_unit(unit, now=now)


def starts_on(found: open_times.UnitOffers, day: date) -> list[datetime]:
    return [offer.start for offer in found.offers if offer.start.astimezone(LOCAL).date() == day]


def summary(found: open_times.UnitOffers, day: date) -> list[tuple[str, str, bool]]:
    return [
        (offer.start.astimezone(LOCAL).strftime("%H:%M"), offer.kind, offer.preferred)
        for offer in found.offers
        if offer.start.astimezone(LOCAL).date() == day
    ]


# The worked example


@pytest.mark.asyncio
async def test_the_designs_worked_example(world):
    jane = await agent(
        hours=[window(["wed"], time(16, 0), time(18, 30))],
        preferred=[window(["wed"], time(16, 0), time(17, 0))],
    )
    world.calendar.add_busy(JANE, at(17, 0), at(17, 30))
    four_thirty = await add_showing(jane, starts_at=at(16, 30))
    await add_attendee(four_thirty, "+14125550117")
    await add_attendee(four_thirty, "+14125550118", "Robin Lee")

    found = await offers()

    # 1:30 is inside 24 hours, 3:30 is before Jane's hours, 5:00 she is busy.
    assert summary(found, WEDNESDAY) == [
        ("16:30", "join", True),
        ("16:00", "new", True),
        ("17:30", "new", False),
        ("18:00", "new", False),
    ]
    join = found.offers[0]
    assert (join.places_left, join.host_name, join.showing_id) == (2, "Jane Doe", four_thirty)
    assert all(offer.host_id == jane for offer in found.offers)


# When a unit may be shown


@pytest.mark.asyncio
async def test_the_first_start_is_exactly_24_hours_out(world):
    await agent()
    found = await offers()
    assert min(offer.start for offer in found.offers) == at(14, 0)  # Wednesday 2:00 PM
    assert at(13, 30) not in [offer.start for offer in found.offers]
    assert found.notice_hours == 24


@pytest.mark.asyncio
async def test_a_listing_can_ask_for_longer_notice_but_not_shorter(world):
    await agent()
    world.listing(showing_notice_hours=48)
    longer = await offers()
    assert min(offer.start for offer in longer.offers) == at(14, 0, day=date(2027, 4, 15))
    assert longer.notice_hours == 48

    world.listing(showing_notice_hours=12)
    assert min(offer.start for offer in (await offers()).offers) == at(14, 0)


@pytest.mark.asyncio
async def test_the_rules_notice_applies_to_every_unit(world):
    await agent()
    world.rules = ShowingRules(showing_notice_hours=72)
    found = await offers()
    assert min(offer.start for offer in found.offers) == at(14, 0, day=date(2027, 4, 16))


@pytest.mark.asyncio
async def test_times_are_offered_up_to_14_days_out(world):
    await agent()
    found = await offers()
    assert max(offer.start for offer in found.offers) == NOW + timedelta(days=14)
    every_day = {offer.start.astimezone(LOCAL).date() for offer in found.offers}
    assert len(every_day) == 14  # Wednesday afternoon to the Tuesday two weeks on


@pytest.mark.asyncio
async def test_starts_are_every_half_hour_inside_the_window(world):
    await agent()
    found = await offers()
    thursday = starts_on(found, date(2027, 4, 15))
    assert [start.astimezone(LOCAL).strftime("%H:%M") for start in sorted(thursday)][:3] == [
        "09:00",
        "09:30",
        "10:00",
    ]
    assert len(thursday) == 19  # 9:00 to 6:00 PM


@pytest.mark.asyncio
async def test_blackout_dates_are_skipped(world):
    await agent()
    world.rules = ShowingRules(blackout_dates=[date(2027, 4, 15)])
    found = await offers()
    assert starts_on(found, date(2027, 4, 15)) == []
    assert starts_on(found, date(2027, 4, 16))


@pytest.mark.asyncio
async def test_nothing_is_offered_from_the_off_market_date(world):
    await agent()
    world.listing(off_market_on=date(2027, 4, 16))
    found = await offers()
    days = {offer.start.astimezone(LOCAL).date() for offer in found.offers}
    assert days == {WEDNESDAY, date(2027, 4, 15)}

    world.listing(off_market_on=TUESDAY)
    gone = await offers()
    assert gone.offers == [] and gone.why_none == "Off the market from 2027-04-13."


@pytest.mark.asyncio
async def test_times_stay_on_the_local_clock_across_a_daylight_saving_change(world):
    await agent()
    # Clocks go back on Sunday 7 November 2027 and forward on Sunday 14 March 2027.
    for before, after, offset in (
        (date(2027, 11, 6), date(2027, 11, 8), -5),
        (date(2027, 3, 13), date(2027, 3, 15), -4),
    ):
        found = await offers(now=datetime.combine(before - timedelta(days=2), time(8), LOCAL))
        for day in (before, after):
            starts = sorted(starts_on(found, day))
            assert len(starts) == 19
            assert starts[0].astimezone(LOCAL).time() == time(9, 0)
            assert starts[-1].astimezone(LOCAL).time() == time(18, 0)
        first_after = min(starts_on(found, after))
        assert first_after.astimezone(UTC).hour == 9 - offset


# Agents


@pytest.mark.asyncio
async def test_an_agent_is_offered_only_inside_their_hours(world):
    await agent(hours=[window(["thu"], time(10, 0), time(11, 0))])
    found = await offers()
    assert [
        start.astimezone(LOCAL).strftime("%a %H:%M")
        for start in sorted(offer.start for offer in found.offers)
    ] == ["Thu 10:00", "Thu 10:30", "Thu 11:00", "Thu 10:00", "Thu 10:30", "Thu 11:00"]


@pytest.mark.asyncio
async def test_a_busy_block_takes_out_the_times_it_overlaps(world):
    await agent()
    world.calendar.add_busy(
        JANE, at(10, 0, day=date(2027, 4, 15)), at(11, 0, day=date(2027, 4, 15))
    )
    thursday = {
        s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(await offers(), date(2027, 4, 15))
    }
    assert {"09:30", "11:00"} <= thursday
    assert not {"10:00", "10:30"} & thursday


@pytest.mark.asyncio
async def test_an_event_marked_free_does_not_block_a_time(world, monkeypatch):
    """Through the real Google adapter: free/busy leaves out an event marked free."""
    monkeypatch.undo()  # the real calendar_for_connection and unusable_reason
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)

    async def read_listings(session):
        return list(world.listings)

    async def read_rules(session, lock=False):
        return world.rules

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    configure(monkeypatch)
    google = FakeGoogle().install(monkeypatch)
    connections.forget_access_tokens()
    async with AsyncSessionFactory() as session:
        row = await session.get(CalendarConnection, world.connection_id)
        row.encrypted_refresh_token = token_crypto.encrypt(google.refresh_token_for())
        await session.commit()
    await agent()
    thursday = date(2027, 4, 15)
    google.add_busy(JANE, at(10, 0, day=thursday), at(11, 0, day=thursday), "transparent")
    google.add_busy(JANE, at(12, 0, day=thursday), at(13, 0, day=thursday))

    found = await open_times.open_times(now=NOW, units=[UNIT])

    times = {s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(found.units[0], thursday)}
    assert {"10:00", "10:30"} <= times
    assert not {"12:00", "12:30"} & times
    assert found.agents[0].status == "read"
    connections.forget_access_tokens()


@pytest.mark.asyncio
async def test_travel_time_between_addresses_and_none_at_the_same_address(world):
    jane = await agent()
    thursday = date(2027, 4, 15)
    world.listing(OTHER_UNIT)
    await add_showing(jane, unit=OTHER_UNIT, starts_at=at(12, 0, day=thursday))
    # add_showing saves "100 Example Ave"; that unit's listing is elsewhere.
    times = {s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(await offers(), thursday)}
    assert {"11:00", "13:00"} <= times
    assert not {"11:30", "12:00", "12:30"} & times

    # The same showing at a unit at this listing's address needs no travel.
    world.listing(address="100 Example Ave")
    open_times.forget_free_busy()
    times = {s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(await offers(), thursday)}
    assert {"11:30", "12:30"} <= times
    assert "12:00" not in times


def test_units_in_one_building_share_an_address():
    same = open_times.same_address
    assert same("320-03", "320 Example St Apt 3", "320-05", "320 Example St Apt 5")
    assert same(" 320-03", "", "320-05 ", "")
    assert same("A-12-3", "", "a-12-7", "")  # the part before the last hyphen
    assert not same("320-03", "320 Example St", "659-01", "659 Other Rd")
    # A key with no hyphen falls back to the address.
    assert not same("32003", "320 Example St Apt 3", "32005", "320 Example St Apt 5")
    assert same("32003", "320 Example St", "32005", "320 example st.")
    assert not same("320-03", "", "32005", "")


@pytest.mark.asyncio
async def test_no_travel_time_between_units_in_one_building(world):
    jane = await agent()
    thursday = date(2027, 4, 15)
    # Another unit in this building, at an address that names its own apartment.
    world.listing("320-05", address="320 Example St Apt 5")
    await add_showing(jane, unit="320-05", starts_at=at(12, 0, day=thursday))
    times = {s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(await offers(), thursday)}
    assert {"11:30", "12:30"} <= times
    assert "12:00" not in times

    # A unit in another building still needs the travel gap.
    friday = date(2027, 4, 16)
    await add_showing(jane, unit="659-01", starts_at=at(12, 0, day=friday))
    times = {s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(await offers(), friday)}
    assert not {"11:30", "12:00", "12:30"} & times
    assert {"11:00", "13:00"} <= times


@pytest.mark.asyncio
async def test_an_agent_in_the_same_building_hosts_next(world):
    thursday = date(2027, 4, 15)
    await agent(priority=1)
    sam = await agent("Sam Rivera", SAM, priority=2)
    await add_showing(sam, unit="320-05", starts_at=at(11, 30, day=thursday))
    found = await offers()
    assert host_at(found, at(12, 0, day=thursday)) == "Sam Rivera"
    assert host_at(found, at(14, 0, day=thursday)) == "Jane Doe"


def host_at(found: open_times.UnitOffers, start: datetime) -> str:
    return next(offer.host_name for offer in found.offers if offer.start == start)


@pytest.mark.asyncio
async def test_the_host_is_chosen_by_place_then_preference_then_priority_then_load(world):
    thursday, friday = date(2027, 4, 15), date(2027, 4, 16)
    jane = await agent(priority=1)
    sam = await agent(
        "Sam Rivera", SAM, priority=2, preferred=[window(["thu"], time(10, 0), time(10, 0))]
    )
    # Sam is already at this unit at 11:30 on Thursday.
    await add_showing(sam, starts_at=at(11, 30, day=thursday))

    found = await offers()
    assert host_at(found, at(12, 0, day=thursday)) == "Sam Rivera"  # next door
    assert host_at(found, at(10, 0, day=thursday)) == "Sam Rivera"  # prefers it
    assert host_at(found, at(14, 0, day=thursday)) == "Jane Doe"  # lower priority number

    # With the same priority, whoever has fewer showings that week.
    async with AsyncSessionFactory() as session:
        (await session.get(ShowingAgentRecord, sam)).priority = 1
        await session.commit()
    await add_showing(jane, unit=OTHER_UNIT, starts_at=at(9, 0, day=date(2027, 4, 19)))
    await add_showing(jane, unit=OTHER_UNIT, starts_at=at(9, 0, day=date(2027, 4, 20)))
    await add_showing(sam, unit=OTHER_UNIT, starts_at=at(9, 0, day=date(2027, 4, 21)))
    found = await offers()
    # This week Jane has none and Sam one; next week Jane has two and Sam one.
    assert host_at(found, at(15, 0, day=friday)) == "Jane Doe"
    assert host_at(found, at(15, 0, day=date(2027, 4, 22))) == "Sam Rivera"


@pytest.mark.asyncio
async def test_a_join_with_room_is_offered_and_a_full_showing_is_not(world):
    jane = await agent()
    thursday = date(2027, 4, 15)
    room = await add_showing(jane, starts_at=at(10, 0, day=thursday))
    for number in range(3):
        await add_attendee(room, f"+1412555011{number}", f"Guest {number}")
    full = await add_showing(jane, starts_at=at(14, 0, day=thursday))
    for number in range(4):
        await add_attendee(full, f"+1412555012{number}", f"Guest {number}")

    found = await offers()
    join = next(offer for offer in found.offers if offer.start == at(10, 0, day=thursday))
    assert (join.kind, join.places_left, join.showing_id) == ("join", 1, room)
    assert found.offers[0] == join  # joins come first
    assert at(14, 0, day=thursday) not in [offer.start for offer in found.offers]


@pytest.mark.asyncio
async def test_an_unreadable_calendar_counts_as_busy(world):
    await agent()
    await agent("Sam Rivera", SAM, priority=2)
    world.calendar.lose(JANE)

    found = await open_times.open_times(now=NOW, units=[UNIT])

    assert {offer.host_name for offer in found.units[0].offers} == {"Sam Rivera"}
    jane = next(item for item in found.agents if item.name == "Jane Doe")
    assert jane.status == "unreadable" and "revoked" in jane.problem

    world.calendar.lose(SAM)
    open_times.forget_free_busy()
    none = await offers()
    assert none.offers == []
    assert none.why_none == "No agent's calendar can be read, so every agent counts as busy."


@pytest.mark.asyncio
async def test_an_agent_with_no_calendars_listed_counts_as_busy(world):
    await agent(calendars=[])
    found = await open_times.open_times(now=NOW, units=[UNIT])
    assert found.units[0].offers == []
    assert found.agents[0].status == "unreadable"


@pytest.mark.asyncio
async def test_an_inactive_agent_gets_no_new_showings(world):
    await agent(active=False)
    found = await open_times.open_times(now=NOW, units=[UNIT])
    assert found.units[0].offers == []
    assert found.units[0].why_none == "No active showing agents."
    assert found.agents[0].status == "inactive"


# Free/busy reads


@pytest.mark.asyncio
async def test_free_busy_is_reused_for_90_seconds_and_the_recheck_reads_afresh(world, monkeypatch):
    await agent()
    start = at(10, 0, day=date(2027, 4, 15))
    clock = [1000.0]
    monkeypatch.setattr(open_times, "_clock", lambda: clock[0])
    assert start in [offer.start for offer in (await offers()).offers]

    world.calendar.add_busy(JANE, start, start + timedelta(minutes=30))
    clock[0] += 60
    assert start in [offer.start for offer in (await offers()).offers]  # the kept answer

    check = await open_times.recheck(UNIT, start, now=NOW)
    assert (check.ok, check.refusal) == (False, "no_agent_free")

    clock[0] += 91
    assert start not in [offer.start for offer in (await offers()).offers]


@pytest.mark.asyncio
async def test_the_recheck_answers_the_host_or_why_not(world):
    jane = await agent()
    thursday = date(2027, 4, 15)
    check = await open_times.recheck(UNIT, at(10, 0, day=thursday), now=NOW)
    assert (check.ok, check.kind, check.host_id, check.places_left) == (True, "new", jane, 4)

    joined = await add_showing(jane, starts_at=at(11, 0, day=thursday))
    await add_attendee(joined)
    check = await open_times.recheck(UNIT, at(11, 0, day=thursday), now=NOW)
    assert (check.kind, check.showing_id, check.places_left) == ("join", joined, 3)

    refusals = {
        at(13, 30): "inside_notice",
        at(10, 15, day=thursday): "not_a_start_time",
        at(18, 30, day=thursday): "outside_window",
        NOW + timedelta(days=15): "too_far_ahead",
    }
    for start, code in refusals.items():
        check = await open_times.recheck(UNIT, start, now=NOW)
        assert (check.ok, check.refusal) == (False, code), start
    unknown = await open_times.recheck("999-Z", at(10, 0, day=thursday), now=NOW)
    assert unknown.refusal == "unknown_unit"

    full = await add_showing(jane, starts_at=at(15, 0, day=thursday))
    for number in range(4):
        await add_attendee(full, f"+1412555013{number}", f"Guest {number}")
    check = await open_times.recheck(UNIT, at(15, 0, day=thursday), now=NOW)
    assert check.refusal == "full"


# The made-up calendar outside production


@pytest.mark.asyncio
async def test_outside_production_an_agent_with_no_usable_connection_uses_the_sample_week(
    world, monkeypatch
):
    monkeypatch.setattr(connections, "unusable_reason", lambda row: "No key here.")
    await agent(hours=[window(["thu"], time(9, 0), time(18, 0))])

    found = await open_times.open_times(now=NOW, units=[UNIT])

    assert found.sample_calendar_in_use and found.agents[0].status == "sample"
    thursday = {
        s.astimezone(LOCAL).strftime("%H:%M") for s in starts_on(found.units[0], date(2027, 4, 15))
    }
    # The sample week has Jane busy 9:00 to 3:30 on weekdays.
    assert "15:30" in thursday and "15:00" not in thursday and "09:00" not in thursday


@pytest.mark.asyncio
async def test_production_never_uses_the_sample_week(world, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setattr(connections, "unusable_reason", lambda row: "No key here.")
    monkeypatch.setattr(
        connections,
        "calendar_for_connection",
        lambda row: connections.UnusableCalendar("x", "No key here."),
    )
    await agent()

    found = await open_times.open_times(now=NOW, units=[UNIT])

    assert not found.sample_calendar_in_use
    assert found.agents[0].status == "unreadable" and found.agents[0].problem == "No key here."
    assert found.units[0].offers == []

    async with AsyncSessionFactory() as session:
        await session.delete(await session.get(CalendarConnection, world.connection_id))
        await session.commit()
    open_times.forget_free_busy()
    found = await open_times.open_times(now=NOW, units=[UNIT])
    assert found.agents[0].problem == "No calendar connection is set up to read it."
    assert not found.sample_calendar_in_use
