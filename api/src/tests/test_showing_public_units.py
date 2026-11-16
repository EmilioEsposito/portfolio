"""The list of units on offer (``GET /units``) and the address in the unit read.

Uses the test database and the stand-ins in ``showing_public.py``. Nothing is sent.
"""

from datetime import date

import pytest
import pytest_asyncio

from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import open_times, turnstile
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ADDRESS,
    ALEX_EMAIL,
    ALEX_MOBILE,
    JANE,
    NOW,
    UNIT,
    PublicWorld,
    add_world_rows,
    booking,
    install,
)

PUBLIC = "/api/showing-bookings/public"
UNITS = f"{PUBLIC}/units"
TEAM_PHONE = "+14125550100"  # the fictional SERNIA_TEAM_PHONE in conftest.py


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERNIA_TEAM_PHONE", TEAM_PHONE)
    monkeypatch.setenv("SHOWING_TEST_PHONES", ALEX_MOBILE)
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    state = PublicWorld()
    state.other_listings = [
        # Another building, one lease date, listed before 320 Example St by its number.
        RentalListing(
            unit="90-02",
            address="90 Example St",
            asking_rent=1200,
            next_lease_start_earliest=date(2027, 6, 15),
        ),
        # No lease dates and no rent.
        RentalListing(unit="900-01", address="900 Example Ave"),
        # Off the market from today: not listed.
        RentalListing(unit="320-05", address=ADDRESS, off_market_on=NOW.date()),
    ]
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def units(world: PublicWorld):
    async with world.client() as client:
        return await client.get(UNITS)


@pytest.mark.asyncio
async def test_the_list_shows_each_unit_on_offer_in_address_order(world):
    answer = await units(world)

    assert answer.status_code == 200
    page = answer.json()
    assert set(page) == {"units", "turnstile_site_key"}
    assert page["turnstile_site_key"] == turnstile.TEST_SITE_KEY
    assert page["units"] == [
        {
            "unit": "90-02",
            "address": "90 Example St",
            "rent": 1200,
            "lease_start_earliest": "2027-06-15",
            "lease_start_latest": None,
            "lease_start": "on Jun 15, 2027",
            "has_open_times": True,
        },
        {
            "unit": UNIT,
            "address": ADDRESS,
            "rent": 1450,
            "lease_start_earliest": "2027-06-01",
            "lease_start_latest": "2027-07-01",
            "lease_start": "between Jun 1 and Jul 1, 2027",
            "has_open_times": True,
        },
        {
            "unit": "900-01",
            "address": "900 Example Ave",
            "rent": None,
            "lease_start_earliest": None,
            "lease_start_latest": None,
            "lease_start": None,
            "has_open_times": True,
        },
    ]


@pytest.mark.asyncio
async def test_the_lease_start_is_worded_as_the_forms_tick_is(world):
    page = (await units(world)).json()
    async with world.client() as client:
        unit = (await client.get(f"{UNITS}/{UNIT}")).json()

    listed = next(item for item in page["units"] if item["unit"] == UNIT)
    tick = next(item for item in unit["confirmations"] if item["key"] == "lease_start")
    assert tick["wording"] == f"My lease can start {listed['lease_start']}."


@pytest.mark.asyncio
async def test_no_units_on_offer_is_an_empty_list(world):
    world.listing = world.listing.model_copy(update={"off_market_on": NOW.date()})
    world.other_listings = []

    answer = await units(world)

    assert answer.status_code == 200
    assert answer.json()["units"] == []


@pytest.mark.asyncio
async def test_a_unit_with_no_agent_free_is_listed_without_open_times(world):
    world.calendar.lose(JANE)

    page = (await units(world)).json()

    assert [item["unit"] for item in page["units"]] == ["90-02", UNIT, "900-01"]
    assert {item["has_open_times"] for item in page["units"]} == {False}


@pytest.mark.asyncio
async def test_the_list_reads_the_calendars_once_for_every_unit(world, monkeypatch):
    reads = []
    real = world.calendar.busy_blocks

    async def counted(calendar_ids, start, end):
        reads.append(tuple(calendar_ids))
        return await real(calendar_ids, start, end)

    monkeypatch.setattr(world.calendar, "busy_blocks", counted)

    await units(world)
    assert reads == [(JANE,)]

    # The booking page that follows reuses the same read.
    async with world.client() as client:
        assert (await client.get(f"{UNITS}/{UNIT}")).status_code == 200
    await units(world)
    assert reads == [(JANE,)]


@pytest.mark.asyncio
async def test_the_list_shows_no_host_and_nothing_about_who_is_booked(world):
    async with world.client() as client:
        booked = await client.post(f"{UNITS}/{UNIT}/bookings", json=booking())
    assert booked.status_code == 201, booked.text

    answer = await units(world)

    assert answer.status_code == 200
    for private in (
        "Jane",
        JANE,
        "Alex",
        ALEX_MOBILE,
        "412-555-0117",
        ALEX_EMAIL,
        TEAM_PHONE,
        "412-555-0100",
        "places_left",
        "join",
    ):
        assert private not in answer.text, private
    item = next(item for item in answer.json()["units"] if item["unit"] == UNIT)
    assert set(item) == {
        "unit",
        "address",
        "rent",
        "lease_start_earliest",
        "lease_start_latest",
        "lease_start",
        "has_open_times",
    }


@pytest.mark.asyncio
async def test_switched_off_the_list_answers_404(world, monkeypatch):
    await store_settings(public_booking_enabled=False)
    assert (await units(world)).status_code == 404

    await store_settings()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert (await units(world)).status_code == 404

    await store_settings(public_booking_enabled=True)
    assert (await units(world)).status_code == 200


@pytest.mark.asyncio
async def test_the_unit_read_has_the_address(world):
    async with world.client() as client:
        answer = await client.get(f"{UNITS}/{UNIT.lower()}")

    assert answer.status_code == 200
    assert (answer.json()["unit"], answer.json()["address"]) == (UNIT, ADDRESS)


# The call line's route, for a page with no unit (the Sernia home page)

CALL_LINE = f"{PUBLIC}/call-line"


@pytest.mark.asyncio
async def test_the_call_line_route_answers_only_the_site_key_and_reads_nothing(world, monkeypatch):
    from api.src.rental_listings import service as listings_service

    async def no_listings(session):
        raise AssertionError("the call line route read the listings")

    async def no_calendar(calendar_ids, start, end):
        raise AssertionError("the call line route read a calendar")

    monkeypatch.setattr(listings_service, "read_listings", no_listings)
    monkeypatch.setattr(world.calendar, "busy_blocks", no_calendar)

    async with world.client() as client:
        answer = await client.get(CALL_LINE)

    assert answer.status_code == 200
    assert answer.json() == {"turnstile_site_key": turnstile.TEST_SITE_KEY}


@pytest.mark.asyncio
async def test_switched_off_the_call_line_route_answers_404(world, monkeypatch):
    await store_settings(public_booking_enabled=False)
    async with world.client() as client:
        assert (await client.get(CALL_LINE)).status_code == 404

    await store_settings()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    async with world.client() as client:
        assert (await client.get(CALL_LINE)).status_code == 404

    await store_settings(public_booking_enabled=True)
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "0x4AAAAAAA-made-up")
    async with world.client() as client:
        answer = await client.get(CALL_LINE)
    assert (answer.status_code, answer.json()) == (
        200,
        {"turnstile_site_key": "0x4AAAAAAA-made-up"},
    )
