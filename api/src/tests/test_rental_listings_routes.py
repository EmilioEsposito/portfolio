"""Rental listing endpoints: who may call them, what they save, and what they refuse.

Uses the test database. Sign-in is replaced by a stand-in caller; the gate
itself, ``rental_listing_caller``, is tested at the end with Clerk mocked. All unit data
here is invented.
"""

import asyncio
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from api.src.utils import clerk as clerk_module

PERSON = SimpleNamespace(
    id="user_test",
    first_name="Pat",
    last_name="Example",
    email_addresses=[SimpleNamespace(email_address="pat@serniacapital.com")],
)
WHO = "Pat Example <pat@serniacapital.com>"


def _app(user=PERSON, source=routes.SOURCE) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def signed_in():
        if user is None:
            raise HTTPException(401, "User is not signed in.")
        return routes.Caller(user=user, source=source)

    app.dependency_overrides[routes.rental_listing_caller] = signed_in
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
    assert gate == [routes.rental_listing_caller]


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


@pytest.mark.asyncio
async def test_a_dry_run_answers_as_a_save_would_and_saves_nothing(api):
    created = await api.post(
        "/api/rental-listings/listings?dry_run=true", json={"listing": _json(LISTING)}
    )
    assert created.status_code == 201 and created.json() == _json(LISTING)
    assert (await api.get("/api/rental-listings")).json()["listings"] == []

    await api.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})
    changed = LISTING.model_copy(update={"asking_rent": 1600})
    edited = await api.put(
        "/api/rental-listings/listings/12-A?dry_run=true",
        json={"listing": _json(changed), "based_on": _json(LISTING)},
    )
    refused = await api.put(
        "/api/rental-listings/listings/12-A?dry_run=true",
        json={
            "listing": _json(LISTING.model_copy(update={"public_facts": ["Door code is 4821"]})),
            "based_on": _json(LISTING),
        },
    )
    stale = await api.put(
        "/api/rental-listings/listings/12-A?dry_run=true",
        json={"listing": _json(changed), "based_on": _json(changed)},
    )
    rules = ShowingRules(blackout_dates=[date(2027, 7, 4)])
    rules_run = await api.put(
        "/api/rental-listings/showing-rules?dry_run=true",
        json={"showing_rules": rules.model_dump(mode="json")},
    )

    assert edited.status_code == 200 and edited.json()["asking_rent"] == 1600
    assert refused.status_code == 422 and "access code" in str(refused.json())
    assert stale.status_code == 409
    assert rules_run.status_code == 200
    on_file = (await api.get("/api/rental-listings")).json()
    assert on_file["listings"] == [_json(LISTING)]
    assert on_file["showing_rules"] == ShowingRules().model_dump(mode="json")
    assert len(await _history()) == 1  # the one real save


@pytest.mark.asyncio
async def test_a_change_through_the_mcp_service_is_recorded_as_such():
    await clear_rental_data()
    transport = httpx.ASGITransport(app=_app(source=routes.MCP_SOURCE))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/rental-listings/listings", json={"listing": _json(LISTING)})

    (change,) = await _history("12-A")
    assert (change.changed_by, change.source) == (WHO, "Sernia MCP")
    await clear_rental_data()


class TestCaller:
    """Which tokens ``rental_listing_caller`` accepts, with Clerk mocked."""

    MCP_CLIENT = "client_mcp"

    def request(self, token: str):
        return SimpleNamespace(headers={"authorization": f"Bearer {token}"})

    def user(self, email="pat@serniacapital.com", status="verified"):
        return SimpleNamespace(
            id="user_test",
            email_addresses=[
                SimpleNamespace(email_address=email, verification=SimpleNamespace(status=status))
            ],
        )

    @pytest.fixture
    def clerk(self, monkeypatch):
        monkeypatch.setenv(routes.MCP_CLIENT_ID_ENV, self.MCP_CLIENT)
        state = SimpleNamespace(
            is_signed_in=True,
            payload={"client_id": self.MCP_CLIENT, "subject": "user_test", "scopes": ["email"]},
        )
        seen = {}

        def authenticate(request, options):
            seen["accepts"] = options.accepts_token
            return state

        users = SimpleNamespace(get_async=AsyncMock(return_value=self.user()))
        monkeypatch.setattr(
            clerk_module,
            "clerk_client",
            SimpleNamespace(authenticate_request=authenticate, users=users),
        )
        clerk_module._user_cache.clear()
        yield SimpleNamespace(state=state, users=users, seen=seen)
        clerk_module._user_cache.clear()

    @pytest.mark.asyncio
    async def test_an_mcp_oauth_token_for_a_sernia_user_is_accepted_as_the_mcp(self, clerk):
        caller = await routes.rental_listing_caller(self.request("oat_abc"))

        assert caller.source == "Sernia MCP" and caller.user.id == "user_test"
        assert clerk.seen["accepts"] == ["oauth_token"]

    @pytest.mark.asyncio
    async def test_a_cached_profile_still_expires_however_often_the_caller_comes_back(self, clerk):
        """A cache hit must not reset the cache's clock, or the profile never refreshes."""
        cached_at = time.time() - 200
        clerk_module._user_cache["user_test"] = {"user": self.user(), "ts": cached_at}

        await routes.rental_listing_caller(self.request("oat_abc"))

        clerk.users.get_async.assert_not_called()
        assert clerk_module._user_cache["user_test"]["ts"] == cached_at

    @pytest.mark.asyncio
    async def test_a_token_issued_to_another_oauth_client_is_refused(self, clerk):
        clerk.state.payload["client_id"] = "client_someone_else"

        with pytest.raises(HTTPException) as refused:
            await routes.rental_listing_caller(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    async def test_an_invalid_or_revoked_token_is_refused(self, clerk):
        clerk.state.is_signed_in = False

        with pytest.raises(HTTPException) as refused:
            await routes.rental_listing_caller(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    async def test_oauth_tokens_are_refused_when_no_mcp_client_is_configured(
        self, clerk, monkeypatch
    ):
        monkeypatch.delenv(routes.MCP_CLIENT_ID_ENV)

        with pytest.raises(HTTPException) as refused:
            await routes.rental_listing_caller(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("email", "status"),
        [("pat@gmail.com", "verified"), ("pat@serniacapital.com", "unverified")],
    )
    async def test_the_user_must_have_a_verified_sernia_email(self, clerk, email, status):
        clerk.users.get_async.return_value = self.user(email=email, status=status)

        with pytest.raises(HTTPException) as refused:
            await routes.rental_listing_caller(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    async def test_a_token_that_is_not_for_a_user_is_refused(self, clerk):
        clerk.state.payload["subject"] = "mch_machine"

        with pytest.raises(HTTPException) as refused:
            await routes.rental_listing_caller(self.request("oat_abc"))
        assert refused.value.status_code == 401
        clerk.users.get_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_any_other_token_goes_through_the_browser_sign_in(self, monkeypatch):
        person = self.user()
        monkeypatch.setattr(routes, "verify_serniacapital_user", AsyncMock(return_value=person))

        caller = await routes.rental_listing_caller(self.request("eyJhbGciOi.session.jwt"))

        assert caller == routes.Caller(user=person, source=routes.SOURCE)
