"""In live mode the public and the lead agent may book only the units that are live.

Two units in one building: ``320-03`` and ``320-05``. Every public and lead path
is tried with one of them unticked, with all units, and in the other modes; then
what must not be touched by it: cancel links, existing showings and their
details, and the team's own actions. The gate is a setting, so it holds in every
environment; nothing here sends anything.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from api.src.showing_bookings import lead_bookings, lead_tours, open_times, routes
from api.src.tests.showing_helpers import store_settings
from api.src.tests.showing_public import (
    ADDRESS,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    at,
    booking,
    by_kind,
    rows,
)
from api.src.tests.test_zillow_lead_bookings import (  # noqa: F401  (fixtures)
    PUBLIC,
    THREAD,
    Lead,
    details,
    lead,
    outcome,
    pending_cards,
    stored,
    world,
)
from api.src.zillow_leads import handle, lead_links
from api.src.zillow_leads import listing_data as listing_data_module
from api.src.zillow_leads.agent import LeadFacts, TourState
from api.src.zillow_leads.listing_data import as_listing_data

TEAM = "/api/showing-bookings"
OTHER = "320-05"
CLOSED = {
    "send_mode": "live",
    "live_units": [OTHER],
    "lead_agent_uses_open_times": True,
}
# Settings with which 320-03 may be booked.
OPEN = [
    {"send_mode": "live", "live_units": [UNIT]},
    {"send_mode": "live", "live_units": [UNIT.lower(), OTHER]},
    {"send_mode": "live", "live_units": [], "live_all_units": True},
    {"send_mode": "test", "live_units": []},
    {"send_mode": "test", "live_units": [OTHER]},
    {"send_mode": "capture", "live_units": []},
    {"send_mode": "capture", "live_units": [OTHER]},
]


@pytest_asyncio.fixture
async def two_units(lead):  # noqa: F811  (the fixture)
    lead.world.other_listings = [lead.world.listing.model_copy(update={"unit": OTHER})]
    yield lead


def listing_data(lead: Lead):  # noqa: F811
    return as_listing_data([lead.world.listing, *lead.world.other_listings], lead.world.rules)


async def public(lead: Lead, method: str, path: str, **kwargs):  # noqa: F811
    async with lead.world.client() as client:
        return await client.request(method, f"{PUBLIC}{path}", **kwargs)


async def book(lead: Lead, unit: str = UNIT, **fields):  # noqa: F811
    return await public(lead, "POST", f"/units/{unit}/bookings", json=booking(**fields))


def robin(**fields) -> dict:
    return {"name": "Robin Lee", "mobile": "412-555-0118", "email": ROBIN_EMAIL, **fields}


async def on_offer(lead: Lead) -> list[str]:  # noqa: F811
    answer = await public(lead, "GET", "/units")
    assert answer.status_code == 200
    return [unit["unit"] for unit in answer.json()["units"]]


def refusal(answer) -> tuple[int, str, str]:
    detail = answer.json()["detail"]
    return answer.status_code, detail["code"], detail["message"]


NOT_OPEN = (404, "unknown_unit", "This unit is not open for showings.")


# The public


@pytest.mark.asyncio
async def test_a_unit_that_is_not_live_is_not_on_offer_to_the_public(two_units):
    await store_settings(**CLOSED)

    assert await on_offer(two_units) == [OTHER]
    assert refusal(await public(two_units, "GET", f"/units/{UNIT}")) == NOT_OPEN
    assert refusal(await book(two_units)) == NOT_OPEN
    assert await rows() == ([], [], [])
    assert two_units.world.turnstile_checks == []
    # The other one is, in any spelling of its key.
    assert (await public(two_units, "GET", f"/units/{OTHER}")).status_code == 200
    assert (await book(two_units, OTHER)).status_code == 201


@pytest.mark.asyncio
async def test_with_no_unit_live_nothing_is_on_offer(two_units):
    await store_settings(send_mode="live")

    assert await on_offer(two_units) == []
    for unit in (UNIT, OTHER):
        assert refusal(await public(two_units, "GET", f"/units/{unit}")) == NOT_OPEN
        assert refusal(await book(two_units, unit)) == NOT_OPEN


@pytest.mark.asyncio
async def test_joining_a_showing_at_a_unit_that_is_not_live_is_refused(two_units):
    assert (await book(two_units)).json()["kind"] == "new"
    await store_settings(**CLOSED)

    answer = await book(two_units, **robin())

    assert refusal(answer) == NOT_OPEN
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", OPEN)
async def test_every_unit_on_offer_is_open_otherwise(two_units, settings):
    await store_settings(**settings)

    assert UNIT in await on_offer(two_units)
    assert (await public(two_units, "GET", f"/units/{UNIT}")).status_code == 200
    assert (await book(two_units)).json()["kind"] == "new"
    assert (await book(two_units, **robin())).json()["kind"] == "join"


@pytest.mark.asyncio
async def test_a_settings_row_that_does_not_read_captures_and_closes_no_unit(two_units):
    await store_settings(send_mode="loud", live_units=[OTHER])

    assert await on_offer(two_units) == [UNIT, OTHER]


@pytest.mark.asyncio
async def test_all_units_includes_one_added_later(two_units):
    await store_settings(send_mode="live", live_all_units=True)
    two_units.world.other_listings.append(
        two_units.world.listing.model_copy(update={"unit": "412-01", "address": "412 Example St"})
    )

    assert "412-01" in await on_offer(two_units)
    assert (await book(two_units, "412-01")).status_code == 201


# What a showing already booked there keeps


@pytest.mark.asyncio
async def test_a_booking_at_a_unit_no_longer_live_is_untouched(two_units):
    booked = (await book(two_units)).json()
    token = {"cancel_token": booked["cancel_token"]}
    await store_settings(**CLOSED)

    showings, attendees, _ = await rows()
    assert (showings[0].status, attendees[0].status) == ("scheduled", "booked")
    looked_up = await public(two_units, "POST", "/bookings/lookup", json=token)
    assert (looked_up.status_code, looked_up.json()["status"]) == (200, "booked")
    corrected = await public(
        two_units, "POST", "/bookings/mobile", json={**token, "mobile": ROBIN_MOBILE}
    )
    assert corrected.status_code == 200
    cancelled = await public(two_units, "POST", "/bookings/cancel", json=token)
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")


@pytest.mark.asyncio
async def test_the_team_books_and_sees_every_unit(two_units):
    await store_settings(**CLOSED)
    app = two_units.world.team_app()
    app.dependency_overrides[routes.clock] = lambda: two_units.world.now
    app.dependency_overrides[routes.text_sender] = lambda: two_units.world.texts

    async with two_units.world.client(app) as client:
        seen = (await client.get(f"{TEAM}/showings")).json()
        booked = await client.post(
            f"{TEAM}/showings/bookings",
            json={"unit": UNIT, "start": at(16).isoformat(), "name": "Casey Park"},
        )

    assert [unit["unit"] for unit in seen["open_times"]["units"]] == [UNIT, OTHER]
    assert all(unit["offers"] for unit in seen["open_times"]["units"])
    assert booked.status_code == 201, booked.text


# The lead agent


async def case_for(lead: Lead):  # noqa: F811
    """The case the lead agent reads for the thread, as production builds it."""
    loaded = listing_data_module.Loaded(data=listing_data(lead))
    lead.monkeypatch.setattr(listing_data_module, "load", AsyncMock(return_value=loaded))
    await lead.decide(outcome("no_reply", status="none", tour={"start": None}))
    (case,) = handle.respond.call_args.args
    return case


def links(case) -> dict[str, str]:
    return {listing.unit: listing.booking_url for listing in case.listings}


@pytest.mark.asyncio
async def test_the_lead_agent_offers_no_time_at_a_unit_that_is_not_live(two_units):
    assert (await book(two_units)).status_code == 201  # a showing there with room to join
    source = lead_tours.BookingTourSource()
    before = await source.tours(listing_data(two_units), NOW)
    assert {slot.unit for slot in before.open_times} == {UNIT, OTHER}
    assert {tour.unit for tour in before.booked} == {UNIT}
    await store_settings(**CLOSED)

    after = await source.tours(listing_data(two_units), NOW)

    assert {slot.unit for slot in after.open_times} == {OTHER}
    assert after.booked == []
    case = await case_for(two_units)
    assert {slot.unit for slot in case.offerable_slots} == {OTHER}
    assert case.booked_tours == []


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", OPEN)
async def test_the_lead_agent_offers_the_unit_otherwise(two_units, settings):
    await store_settings(**{**settings, "lead_agent_uses_open_times": True})

    case = await case_for(two_units)

    assert UNIT in {slot.unit for slot in case.offerable_slots}
    assert links(case)[UNIT].startswith(f"https://serniacapital.com/showings/{UNIT}?t=")


@pytest.mark.asyncio
async def test_settings_that_do_not_read_offer_the_lead_agent_nothing(two_units):
    await store_settings(send_mode="loud", lead_agent_uses_open_times=True)

    case = await case_for(two_units)

    assert case.offerable_slots == [] and case.own_showings is None


@pytest.mark.asyncio
async def test_a_lead_gets_no_link_to_a_page_that_says_the_unit_is_not_open(two_units):
    """The listing's own link instead; no link at all when that is such a page too."""
    own_link = "https://book.example/320-03"
    page = f"https://serniacapital.com/showings/{UNIT}"
    token = await lead_links.token_for_thread(THREAD, now=NOW)
    for settings, listing_link, expected in [
        (CLOSED, own_link, own_link),
        (CLOSED, "", ""),
        (CLOSED, page, ""),
        ({**CLOSED, "live_units": [UNIT]}, page, f"{page}?t={token}"),
        ({**CLOSED, "live_units": [], "live_all_units": True}, own_link, f"{page}?t={token}"),
        ({**CLOSED, "send_mode": "capture"}, own_link, f"{page}?t={token}"),
        # With public booking off, every one of these pages answers that it is not open.
        ({**CLOSED, "live_units": [UNIT], "public_booking_enabled": False}, own_link, own_link),
        ({**CLOSED, "live_units": [UNIT], "public_booking_enabled": False}, page, ""),
    ]:
        await store_settings(**settings)
        two_units.world.listing = two_units.world.listing.model_copy(
            update={"showing_booking_url": listing_link}
        )
        assert links(await case_for(two_units))[UNIT] == expected, (settings, listing_link)


@pytest.mark.asyncio
async def test_the_rules_own_link_is_no_way_round_it(two_units):
    """The booking page for any unit without its own link, when it is a unit's page here."""
    await store_settings(**CLOSED)
    two_units.world.rules = two_units.world.rules.model_copy(
        update={"showing_booking_url": f"https://serniacapital.com/showings/{UNIT}"}
    )
    assert links(await case_for(two_units))[UNIT] == ""

    two_units.world.rules = two_units.world.rules.model_copy(
        update={"showing_booking_url": "https://serniacapital.com/showings"}
    )
    # The units list says which units are open, and never that one is not.
    assert links(await case_for(two_units))[UNIT] == "https://serniacapital.com/showings"


@pytest.mark.asyncio
async def test_a_reply_that_books_a_unit_that_is_not_live_is_held(two_units):
    conversation_id = await two_units.decide(outcome())
    await store_settings(**CLOSED)

    await two_units.decide_on(conversation_id)

    handle.send_reply.assert_not_awaited()
    assert await rows() == ([], [], [])
    (card,) = await pending_cards(conversation_id)
    assert (
        "Not sent: the showing could not be booked. This unit is not open for showings."
        in card["args"]["team_notes"]
    )


@pytest.mark.asyncio
async def test_a_move_to_a_new_time_at_a_unit_that_is_not_live_is_held(two_units):
    await two_units.decide_on(await two_units.decide(outcome()))
    await store_settings(**CLOSED)

    prepared = await lead_bookings.prepare_reply(
        conversation_id="test-move",
        thread_id=THREAD,
        lead=LeadFacts(name="Jordan Lee", unit=UNIT),
        tour=TourState(status="rescheduled", start=at(17)),
        listed_units=[UNIT, OTHER],
        now=two_units.world.now,
    )

    assert prepared.refused and prepared.refusal_code == "unknown_unit"
    showings, attendees, _ = await rows()
    assert [s.starts_at for s in showings] == [at(16)] and attendees[0].status == "booked"


@pytest.mark.asyncio
async def test_a_lead_already_booked_there_is_untouched(two_units):
    """Confirmed again, cancelled, and their details added, at a unit no longer live."""
    await two_units.decide_on(await two_units.decide(outcome()))
    await store_settings(**CLOSED)
    token = await lead_links.token_for_thread(THREAD, now=NOW)

    again = await lead_bookings.prepare_reply(
        conversation_id="test-again",
        thread_id=THREAD,
        lead=LeadFacts(name="Jordan Lee", unit=UNIT),
        tour=TourState(status="confirmed", start=at(16)),
        listed_units=[UNIT, OTHER],
        now=two_units.world.now,
    )
    assert again.action == "already_booked"

    # Their own link is kept, because the page shows their place.
    case = await case_for(two_units)
    assert links(case)[UNIT] == f"https://serniacapital.com/showings/{UNIT}?t={token}"
    assert [(own.unit, own.status) for own in case.own_showings] == [(UNIT, "booked")]
    path = f"/units/{UNIT}/lead-booking"
    assert refusal(await public(two_units, "GET", f"/units/{UNIT}")) == NOT_OPEN
    place = await public(two_units, "POST", path, json={"lead_token": token})
    assert place.status_code == 200 and place.json()["details_added"] is False
    unit = await public(two_units, "POST", f"{path}/unit", json={"lead_token": token})
    assert unit.status_code == 200
    page = unit.json()
    assert (page["unit"], page["address"], page["offers"]) == (UNIT, ADDRESS, [])
    assert [item["key"] for item in page["confirmations"]] == ["lease_start", "pets", "texts"]
    done = await public(two_units, "POST", f"{path}/details", json=details(token))
    assert done.status_code == 201, done.text

    cancelled = await lead_bookings.cancel_from_decision(
        thread_id=THREAD,
        lead=LeadFacts(unit=UNIT),
        tour=TourState(status="cancelled"),
        now=two_units.world.now,
    )
    assert len(cancelled.cancelled) == 1
    _, attendees, notices = await rows()
    assert attendees[0].status == "cancelled" and "prospect_cancelled" in by_kind(notices)


@pytest.mark.asyncio
async def test_the_unit_is_read_for_a_place_only_by_its_own_thread(two_units):
    await two_units.decide_on(await two_units.decide(outcome()))
    await store_settings(**CLOSED)
    mine = await lead_links.token_for_thread(THREAD, now=NOW)
    other = await lead_links.token_for_thread("thread-someone-else", now=NOW)

    for unit, token in [(UNIT, other), (UNIT, "x" * 22), (UNIT, ""), (OTHER, mine)]:
        answer = await public(
            two_units, "POST", f"/units/{unit}/lead-booking/unit", json={"lead_token": token}
        )
        assert (answer.status_code, answer.json()["detail"]["code"]) == (404, "no_booking")
    await store_settings(**CLOSED, public_booking_enabled=False)
    answer = await public(
        two_units, "POST", f"/units/{UNIT}/lead-booking/unit", json={"lead_token": mine}
    )
    assert answer.status_code == 404 and answer.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_the_booking_record_of_a_held_reply_says_why(two_units):
    conversation_id = await two_units.decide(outcome())
    await store_settings(**CLOSED)

    await two_units.decide_on(conversation_id)

    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["refusal"] == "unknown_unit"


@pytest.mark.asyncio
async def test_open_times_themselves_know_nothing_of_it(two_units):
    """The team's view, the re-check and the rules are the same in every mode."""
    await store_settings(**CLOSED)

    found = await open_times.open_times(now=NOW)
    check = await open_times.recheck(UNIT, at(16), now=NOW)

    assert [unit.unit for unit in found.units] == [UNIT, OTHER]
    assert check.ok
