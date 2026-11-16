"""Joining a booked showing until 2 hours before its start.

A showing already booked that has room is offered as a join until 2 hours
before it starts, on the booking page and for the team, even inside the notice
period; a new time still needs the full notice, and the re-check at booking
applies the same rule. The Zillow lead agent keeps its own 24-hour floor.
Through the public and team routes with the stand-ins and a moved clock;
nothing is sent anywhere.
"""

from datetime import datetime, timedelta

import pytest

from api.src.showing_bookings import lead_tours, open_times
from api.src.showing_bookings.schema import JOIN_CLOSES_HOURS
from api.src.tests.showing_public import PublicWorld, at, rows
from api.src.tests.test_showing_reminders import PUBLIC, book, casey, robin, run
from api.src.tests.test_showing_reminders import world as reminder_world
from api.src.tests.test_showing_team_actions import caller, team_book
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.tours import tours_for_case

world = reminder_world

FOUR_PM = at(16)
FIVE_PM = at(17)


async def offers(world: PublicWorld) -> dict:
    open_times.forget_free_busy()
    async with world.client() as client:
        answer = await client.get(f"{PUBLIC}/units/320-03")
    assert answer.status_code == 200, answer.text
    return {offer["start"]: offer for offer in answer.json()["offers"]}


def key(moment) -> str:
    return moment.isoformat()


async def try_book(world: PublicWorld, body: dict):
    async with world.client() as client:
        return await client.post(f"{PUBLIC}/units/320-03/bookings", json=body)


def test_joins_close_2_hours_before_the_start():
    assert JOIN_CLOSES_HOURS == 2


@pytest.mark.asyncio
async def test_a_join_is_offered_3_hours_ahead_and_not_1_hour_ahead(world):
    await book(world)  # Alex at 4 PM, booked the day before

    world.now = FOUR_PM - timedelta(hours=3)
    seen = await offers(world)
    assert seen[key(FOUR_PM)]["join"] is True
    assert seen[key(FOUR_PM)]["places_left"] == 3
    # Inside the 24-hour notice, no new time is offered.
    inside = [
        offer
        for offer in seen.values()
        if datetime.fromisoformat(offer["start"]) < world.now + timedelta(hours=24)
    ]
    assert [(offer["start"], offer["join"]) for offer in inside] == [(key(FOUR_PM), True)]

    world.now = FOUR_PM - timedelta(hours=2)
    assert key(FOUR_PM) in await offers(world)

    world.now = FOUR_PM - timedelta(hours=2) + timedelta(minutes=1)
    assert key(FOUR_PM) not in await offers(world)
    world.now = FOUR_PM - timedelta(hours=1)
    assert key(FOUR_PM) not in await offers(world)


@pytest.mark.asyncio
async def test_joining_3_hours_ahead_books_into_the_showing(world):
    await book(world)
    world.now = FOUR_PM - timedelta(hours=3)
    answer = await try_book(world, robin(FOUR_PM))
    assert answer.status_code == 201, answer.text
    assert answer.json()["kind"] == "join"
    showings, attendees, _ = await rows()
    assert len(showings) == 1
    assert [a.status for a in attendees] == ["booked", "booked"]


@pytest.mark.asyncio
async def test_joining_1_hour_ahead_is_refused(world):
    await book(world)
    world.now = FOUR_PM - timedelta(hours=1)
    answer = await try_book(world, robin(FOUR_PM))
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "time_gone"
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_a_new_time_inside_the_notice_period_is_still_refused(world):
    await book(world)
    world.now = FOUR_PM - timedelta(hours=3)
    assert key(FIVE_PM) not in await offers(world)
    answer = await try_book(world, robin(FIVE_PM))
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "time_gone"
    showings, _, _ = await rows()
    assert len(showings) == 1


@pytest.mark.asyncio
async def test_the_recheck_at_booking_applies_the_same_rule(world):
    await book(world)
    # Offered just before the line, booked just after it.
    world.now = FOUR_PM - timedelta(hours=2, minutes=1)
    assert (await offers(world))[key(FOUR_PM)]["join"] is True
    world.now = FOUR_PM - timedelta(hours=1, minutes=59)
    answer = await try_book(world, robin(FOUR_PM))
    assert answer.status_code == 409, answer.text

    check = await open_times.recheck("320-03", FOUR_PM, now=FOUR_PM - timedelta(hours=3))
    assert (check.ok, check.kind) == (True, "join")
    check = await open_times.recheck("320-03", FOUR_PM, now=FOUR_PM - timedelta(hours=1))
    assert (check.ok, check.refusal) == (False, "inside_notice")
    check = await open_times.recheck("320-03", FIVE_PM, now=FOUR_PM - timedelta(hours=3))
    assert (check.ok, check.refusal) == (False, "inside_notice")


@pytest.mark.asyncio
async def test_the_team_can_add_a_caller_to_a_showing_3_hours_ahead_but_not_1(world):
    await book(world)
    world.now = FOUR_PM - timedelta(hours=3)
    answer = await team_book(world, caller(FOUR_PM, name="Sky Rivers"))
    assert answer.status_code == 201, answer.text
    assert answer.json()["kind"] == "join"

    world.now = FOUR_PM - timedelta(hours=1)
    answer = await team_book(world, caller(FOUR_PM, name="Jo Banks"))
    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "time_gone"
    # A new time inside the notice period is refused to the team too.
    world.now = FOUR_PM - timedelta(hours=3)
    answer = await team_book(world, caller(FIVE_PM, name="Jo Banks"))
    assert answer.status_code == 409


@pytest.mark.asyncio
async def test_a_full_showing_is_not_offered_however_far_ahead(world):
    await book(world)
    await book(world, robin())
    await book(world, casey())
    async with world.client() as client:
        fourth = await client.post(
            f"{PUBLIC}/units/320-03/bookings",
            json=robin(FOUR_PM)
            | {"name": "Sky Rivers", "mobile": "412-555-0120", "email": "sky@example.com"},
        )
    assert fourth.status_code == 201, fourth.text
    world.now = FOUR_PM - timedelta(hours=3)
    assert key(FOUR_PM) not in await offers(world)


@pytest.mark.asyncio
async def test_the_lead_agents_times_are_unchanged(world):
    """The lead agent keeps its own 24-hour floor: a join 3 hours ahead is not passed on."""
    await book(world)
    now = FOUR_PM - timedelta(hours=3)
    world.now = now
    open_times.forget_free_busy()
    listing_data = as_listing_data([world.listing], world.rules)
    tours = await tours_for_case(lead_tours.BookingTourSource(), listing_data, now)
    assert FOUR_PM not in [tour.start for tour in tours.booked]
    starts = [slot.start for slot in tours.open_times]
    assert FOUR_PM not in starts
    assert min(starts) >= now + timedelta(hours=24)
    # The page offers it.
    assert (await offers(world))[key(FOUR_PM)]["join"] is True


@pytest.mark.asyncio
async def test_a_join_inside_24_hours_tells_the_residents_nothing_new(world):
    world.residents.add("Morgan Ellis", "+14125550142", unit="320-03")
    await book(world)
    await run(world, world.now + timedelta(minutes=10))
    world.now = FOUR_PM - timedelta(hours=3)
    answer = await try_book(world, robin(FOUR_PM))
    assert answer.status_code == 201
    await run(world, world.now + timedelta(minutes=10))
    _, _, notices = await rows()
    assert [n.kind for n in notices].count("resident_new_showing") == 1
