"""Rental listing endpoints: who may call them, what they save, and what they refuse.

Uses the test database. Sign-in is replaced by a stand-in user; the gate
itself is ``verify_serniacapital_user``, tested where it lives. All unit data
here is invented.
"""

import asyncio
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import routes, service
from api.src.rental_listings.models import RentalListingRecord
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.tests.test_rental_listings import LISTING, clear_rental_data
from api.src.utils.clerk import verify_serniacapital_user

PERSON = SimpleNamespace(
    id="user_test",
    first_name="Pat",
    last_name="Example",
    email_addresses=[SimpleNamespace(email_address="pat@serniacapital.com")],
)
WHO = "Pat Example <pat@serniacapital.com>"


def _app(user=PERSON) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def signed_in():
        if user is None:
            raise HTTPException(401, "User is not signed in.")
        return user

    app.dependency_overrides[verify_serniacapital_user] = signed_in
    return app


@pytest_asyncio.fixture
async def api():
    await clear_rental_data()
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await clear_rental_data()


def _json(unit: RentalListing) -> dict:
    return unit.model_dump(mode="json")


async def _history(subject: str | None = None) -> list:
    async with AsyncSessionFactory() as session:
        return await service.read_history(session, subject)


def test_every_endpoint_is_behind_the_sernia_sign_in():
    gate = [dependency.dependency for dependency in routes.router.dependencies]
    assert gate == [verify_serniacapital_user]


@pytest.mark.asyncio
async def test_without_a_sernia_user_nothing_is_read_or_saved():
    await clear_rental_data()
    transport = httpx.ASGITransport(app=_app(user=None))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/rental-listings")).status_code == 401
        created = await client.post(
            "/api/rental-listings/listings", json={"listing": _json(LISTING)}
        )
        assert created.status_code == 401
    assert await _history() == []


@pytest.mark.asyncio
async def test_with_nothing_on_file_the_list_is_empty_and_the_rules_are_the_defaults(api):
    response = await api.get("/api/rental-listings")
    assert response.status_code == 200
    assert response.json() == {
        "listings": [],
        "showing_rules": ShowingRules().model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_a_new_unit_is_saved_with_the_person_and_the_screen_in_its_history(api):
    response = await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})

    assert response.status_code == 201
    assert response.json() == _json(LISTING)
    listed = (await api.get("/api/rental-listings")).json()["listings"]
    assert listed == [_json(LISTING)]
    [change] = await _history("12-A")
    assert (change.changed_by, change.source) == (WHO, routes.SOURCE)
    assert change.snapshot == _json(LISTING)


@pytest.mark.asyncio
async def test_a_second_unit_of_the_same_name_is_refused_whatever_the_case(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    again = LISTING.model_copy(update={"unit": "12-a", "asking_rent": 9999})

    response = await api.post("/api/rental-listings/listings", json={"listing": _json(again)})

    assert response.status_code == 409
    assert "already has a listing" in response.json()["detail"]["problems"][0]
    assert len(await _history()) == 1


@pytest.mark.asyncio
async def test_an_edit_replaces_the_unit_and_needs_no_approval_for_free_text(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    edited = LISTING.model_copy(
        update={
            "asking_rent": 1550,
            "off_market_on": date(2027, 6, 15),
            "public_facts": [*LISTING.public_facts, "Washer and dryer in unit"],
            "internal_notes": [],
        }
    )

    response = await api.put(
        "/api/rental-listings/listings/12-A",
        json={"listing": _json(edited), "based_on": _json(LISTING)},
    )

    assert response.status_code == 200
    assert response.json() == _json(edited)
    latest, first = await _history("12-A")
    assert latest.snapshot == _json(edited) and first.snapshot == _json(LISTING)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("public_facts", ["Call 412-555-0134 for a tour"], "phone number"),
        ("internal_notes", ["Door code is 4821"], "access code"),
        ("internal_notes", ["Resident: sam@example.com"], "email address"),
        ("public_facts", ["Photos at https://example.com"], "link"),
    ],
)
@pytest.mark.asyncio
async def test_text_that_fails_the_checks_is_refused_with_422_and_the_reason(
    api, field, value, reason
):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    edited = LISTING.model_copy(update={field: value})

    response = await api.put("/api/rental-listings/listings/12-A", json={"listing": _json(edited)})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "Not saved."
    assert any(reason in problem for problem in detail["problems"])
    assert len(await _history("12-A")) == 1  # only the create
    assert (await api.get("/api/rental-listings")).json()["listings"] == [_json(LISTING)]


@pytest.mark.asyncio
async def test_a_new_unit_that_fails_the_checks_is_not_created(api):
    bad = LISTING.model_copy(update={"next_lease_start_earliest": date(2027, 9, 1)})
    response = await api.post("/api/rental-listings/listings", json={"listing": _json(bad)})
    assert response.status_code == 422
    assert "next_lease_start_earliest is after" in response.json()["detail"]["problems"][0]
    assert await _history() == []


@pytest.mark.asyncio
async def test_a_field_of_the_wrong_type_or_unknown_is_refused(api):
    wrong_type = {**_json(LISTING), "asking_rent": "a lot"}
    unknown = {**_json(LISTING), "door_code": "1234"}
    for body in (wrong_type, unknown):
        response = await api.post("/api/rental-listings/listings", json={"listing": body})
        assert response.status_code == 422
    assert await _history() == []


@pytest.mark.asyncio
async def test_an_edit_from_a_stale_screen_does_not_undo_a_newer_change(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    rented = LISTING.model_copy(update={"off_market_on": date(2027, 6, 1)})
    async with AsyncSessionFactory() as session:  # someone else marks it rented
        await service.save_listing(session, rented, changed_by="Agent", source="chat")
    stale_edit = LISTING.model_copy(update={"asking_rent": 1600})

    response = await api.put(
        "/api/rental-listings/listings/12-A",
        json={"listing": _json(stale_edit), "based_on": _json(LISTING)},
    )

    assert response.status_code == 409
    units = (await api.get("/api/rental-listings")).json()["listings"]
    assert units[0]["off_market_on"] == "2027-06-01" and units[0]["asking_rent"] == 1500


@pytest.mark.asyncio
async def test_an_edit_cannot_rename_a_unit_or_create_one(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    renamed = LISTING.model_copy(update={"unit": "12-B"})

    assert (
        await api.put("/api/rental-listings/listings/12-A", json={"listing": _json(renamed)})
    ).status_code == 422
    assert (
        await api.put("/api/rental-listings/listings/12-B", json={"listing": _json(renamed)})
    ).status_code == 404
    assert [
        unit["unit"] for unit in (await api.get("/api/rental-listings")).json()["listings"]
    ] == ["12-A"]


@pytest.mark.asyncio
async def test_showing_rules_are_saved_and_in_the_history(api):
    rules = ShowingRules(
        blackout_dates=[date(2027, 7, 4)], showing_booking_url="https://book.example.com"
    )

    response = await api.put(
        "/api/rental-listings/showing-rules",
        json={"showing_rules": rules.model_dump(mode="json"), "based_on": _json_rules()},
    )

    assert response.status_code == 200
    assert response.json() == rules.model_dump(mode="json")
    [change] = await _history(service.SHOWING_RULES_SUBJECT)
    assert (change.changed_by, change.source) == (WHO, routes.SOURCE)


def _json_rules(rules: ShowingRules | None = None) -> dict:
    return (rules or ShowingRules()).model_dump(mode="json")


@pytest.mark.asyncio
async def test_showing_rules_that_fail_the_checks_or_are_stale_are_refused(api):
    backwards = ShowingRules(earliest_start="19:00", latest_start="09:00")
    response = await api.put(
        "/api/rental-listings/showing-rules", json={"showing_rules": _json_rules(backwards)}
    )
    assert response.status_code == 422
    assert "earliest_start is after" in response.json()["detail"]["problems"][0]

    await api.put(
        "/api/rental-listings/showing-rules",
        json={"showing_rules": _json_rules(ShowingRules(blackout_dates=[date(2027, 7, 4)]))},
    )
    stale = await api.put(
        "/api/rental-listings/showing-rules",
        json={"showing_rules": _json_rules(), "based_on": _json_rules()},
    )
    assert stale.status_code == 409
    assert len(await _history(service.SHOWING_RULES_SUBJECT)) == 1


@pytest.mark.asyncio
async def test_history_for_one_unit_the_rules_or_everything_newest_first(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    await api.post(
        "/api/rental-listings/listings", json={"listing": _json(RentalListing(unit="12-B"))}
    )
    await api.put("/api/rental-listings/showing-rules", json={"showing_rules": _json_rules()})

    everything = (await api.get("/api/rental-listings/history")).json()
    one_unit = (await api.get("/api/rental-listings/history", params={"subject": "12-a"})).json()
    rules = (
        await api.get("/api/rental-listings/history", params={"subject": "showing_rules"})
    ).json()
    limited = (await api.get("/api/rental-listings/history", params={"limit": 1})).json()

    assert [change["subject"] for change in everything] == ["showing_rules", "12-B", "12-A"]
    assert [change["subject"] for change in one_unit] == ["12-A"]
    assert [change["subject"] for change in rules] == ["showing_rules"]
    assert len(limited) == 1
    assert (await api.get("/api/rental-listings/history", params={"limit": 0})).status_code == 422


@pytest.mark.asyncio
async def test_a_row_broken_by_a_hand_edit_is_reported_not_passed_on(api):
    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(RentalListingRecord).values(internal_notes=["Lockbox on the back door"])
        )
        await session.commit()

    response = await api.get("/api/rental-listings")

    assert response.status_code == 409
    assert "access code" in response.json()["detail"]["problems"][0]


@pytest.mark.asyncio
async def test_a_unit_named_with_a_slash_can_be_edited(api):
    slashed = RentalListing(unit="12/14", asking_rent=1500)
    assert (
        await api.post("/api/rental-listings/listings", json={"listing": _json(slashed)})
    ).status_code == 201
    edited = slashed.model_copy(update={"asking_rent": 1550})

    response = await api.put(
        "/api/rental-listings/listings/12%2F14",
        json={"listing": _json(edited), "based_on": _json(slashed)},
    )

    assert response.status_code == 200
    assert response.json()["asking_rent"] == 1550


@pytest.mark.asyncio
async def test_two_rule_saves_from_the_same_screen_state_do_not_both_win(api):
    loaded = _json_rules()
    first = ShowingRules(blackout_dates=[date(2027, 7, 4)])
    second = ShowingRules(blackout_dates=[date(2027, 12, 25)])

    responses = await asyncio.gather(
        *(
            api.put(
                "/api/rental-listings/showing-rules",
                json={"showing_rules": _json_rules(rules), "based_on": loaded},
            )
            for rules in (first, second)
        )
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(await _history(service.SHOWING_RULES_SUBJECT)) == 1


@pytest.mark.asyncio
async def test_two_adds_of_one_unit_in_different_case_do_not_both_win(api):
    responses = await asyncio.gather(
        *(
            api.post(
                "/api/rental-listings/listings",
                json={"listing": _json(RentalListing(unit=name))},
            )
            for name in ("12-C", "12-c")
        )
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert [
        listing["unit"] for listing in (await api.get("/api/rental-listings")).json()["listings"]
    ] in (["12-C"], ["12-c"])


@pytest.mark.asyncio
async def test_an_add_that_hits_the_unique_index_is_a_409_not_a_500(api, monkeypatch):
    async def races_another_writer(session, listing, *, changed_by, source=None):
        raise IntegrityError("INSERT INTO rental_listings", {}, Exception("duplicate key"))

    monkeypatch.setattr(service, "save_listing", races_another_writer)

    response = await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})

    assert response.status_code == 409
    assert "already has a listing" in response.json()["detail"]["problems"][0]
