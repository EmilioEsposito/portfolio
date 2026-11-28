"""The team's showing booking API: settings, the notice list, the job run by hand, and who may call.

Uses the test database. Sign-in is replaced by a stand-in Sernia user, except
in the auth tests at the end, which run the real check with Clerk mocked.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from sqlalchemy import delete, select

from api.src.database.app_settings import AppSetting
from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.models import RentalListingRecord
from api.src.showing_bookings import outbox, routes
from api.src.showing_bookings.schema import CalendarPayload, EmailPayload, TextPayload
from api.src.showing_bookings.settings import SETTINGS_KEY
from api.src.tests.showing_helpers import (
    PROSPECT_PHONE,
    add_agent,
    add_showing,
    at,
    clear_showing_data,
    store_settings,
)
from api.src.utils import clerk as clerk_module

LISTED_UNIT = "991-A"  # made up, and removed after each test
DEFAULTS = {
    "send_mode": "capture",
    "live_units": [],
    "live_all_units": False,
    "lead_agent_uses_open_times": False,
    "public_booking_enabled": None,
    "public_limits": {
        "upcoming_per_mobile": 2,
        "upcoming_per_email": 2,
        "bookings_per_ip_per_day": 6,
        "bookings_per_day": 50,
    },
}
PERSON = SimpleNamespace(id="user_test")


def app(signed_in: bool = True) -> FastAPI:
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    if signed_in:
        application.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    return application


async def clear_listing():
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(RentalListingRecord).where(RentalListingRecord.unit == LISTED_UNIT)
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("SHOWING_TEST_PHONES", raising=False)
    monkeypatch.delenv("SHOWING_TEST_EMAILS", raising=False)
    await clear_showing_data()
    await clear_listing()
    async with AsyncSessionFactory() as session:
        session.add(RentalListingRecord(unit=LISTED_UNIT, address="991 Example St"))
        await session.commit()
    yield
    await clear_showing_data()
    await clear_listing()


@pytest_asyncio.fixture
async def api():
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def every_listing() -> list[dict]:
    """Every rental listing in this database, as the tab lists them."""
    async with AsyncSessionFactory() as session:
        rows = await session.execute(
            select(RentalListingRecord.unit, RentalListingRecord.address).order_by(
                RentalListingRecord.unit
            )
        )
        return [{"unit": unit, "address": address} for unit, address in rows]


async def stored() -> dict | None:
    async with AsyncSessionFactory() as session:
        row = await session.get(AppSetting, SETTINGS_KEY)
        return None if row is None else row.value


# Settings


@pytest.mark.asyncio
async def test_settings_start_in_capture_and_say_where_they_run(api, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0150")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", "team.test@example.com")

    response = await api.get("/api/showing-bookings/settings")

    assert response.status_code == 200
    assert response.json() == {
        "settings": DEFAULTS,
        "environment": "local",
        "is_production": False,
        "test_recipients": ["t…@example.com", "•••-0150"],
        "public_booking_on": True,
        "listings": await every_listing(),
    }
    assert {"unit": LISTED_UNIT, "address": "991 Example St"} in response.json()["listings"]


@pytest.mark.asyncio
async def test_production_is_reported_as_production(api, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    body = (await api.get("/api/showing-bookings/settings")).json()
    assert (body["environment"], body["is_production"]) == ("production", True)


@pytest.mark.asyncio
async def test_a_save_stores_live_units_as_their_listings_spell_them(api):
    new = {**DEFAULTS, "send_mode": "live", "live_units": ["991-a"]}

    response = await api.patch(
        "/api/showing-bookings/settings", json={"settings": new, "based_on": DEFAULTS}
    )

    assert response.status_code == 200
    saved = {**new, "live_units": [LISTED_UNIT]}
    assert response.json() == saved
    assert await stored() == saved


@pytest.mark.asyncio
async def test_a_unit_with_no_listing_is_refused(api):
    new = {**DEFAULTS, "send_mode": "live", "live_units": [LISTED_UNIT, "404-Z"]}

    response = await api.patch(
        "/api/showing-bookings/settings", json={"settings": new, "based_on": DEFAULTS}
    )

    assert response.status_code == 422
    assert "404-Z" in response.json()["detail"]
    assert await stored() is None


@pytest.mark.asyncio
async def test_a_live_unit_whose_listing_is_gone_never_stops_a_save(api):
    """It stays live until someone unticks it; only a newly ticked unit must be listed."""
    await store_settings(**{**DEFAULTS, "send_mode": "live", "live_units": ["404-Z", LISTED_UNIT]})
    shown = (await api.get("/api/showing-bookings/settings")).json()["settings"]
    assert shown["live_units"] == ["404-Z", LISTED_UNIT]

    async def save(**changes):
        before = (await api.get("/api/showing-bookings/settings")).json()["settings"]
        return await api.patch(
            "/api/showing-bookings/settings",
            json={"settings": {**before, **changes}, "based_on": before},
        )

    to_capture = await save(send_mode="capture")
    assert to_capture.status_code == 200, to_capture.text
    assert to_capture.json()["live_units"] == ["404-Z", LISTED_UNIT]
    another = await save(live_units=["404-Z", "405-Z"])
    assert another.status_code == 422 and "405-Z" in another.json()["detail"]
    assert "404-Z" not in another.json()["detail"]
    unticked = await save(live_units=[LISTED_UNIT])
    assert unticked.json()["live_units"] == [LISTED_UNIT]
    back = await save(live_units=["404-Z", LISTED_UNIT])
    assert back.status_code == 422


@pytest.mark.asyncio
async def test_all_units_and_the_ticks_are_both_saved(api):
    new = {**DEFAULTS, "send_mode": "live", "live_units": [LISTED_UNIT], "live_all_units": True}

    response = await api.patch(
        "/api/showing-bookings/settings", json={"settings": new, "based_on": DEFAULTS}
    )

    assert response.status_code == 200, response.text
    assert response.json() == new == await stored()


@pytest.mark.asyncio
async def test_an_old_row_is_shown_and_saved_as_what_it_meant(api):
    """No data migration: the row as production has it, then a save over it."""
    await store_settings(
        send_mode="live",
        live_units=[],
        test_sends_enabled=True,
        lead_agent_uses_open_times=True,
        public_booking_enabled=True,
    )

    shown = (await api.get("/api/showing-bookings/settings")).json()["settings"]

    old = {**DEFAULTS, "lead_agent_uses_open_times": True, "public_booking_enabled": True}
    assert shown == {**old, "send_mode": "test"}
    saved = await api.patch(
        "/api/showing-bookings/settings",
        json={"settings": {**shown, "send_mode": "capture"}, "based_on": shown},
    )
    assert saved.status_code == 200, saved.text
    assert await stored() == old


@pytest.mark.asyncio
async def test_a_tab_left_open_from_before_still_saves(api):
    """The dropped key is accepted and ignored, on a save too."""
    before = {**DEFAULTS, "test_sends_enabled": False}
    del before["live_all_units"]

    response = await api.patch(
        "/api/showing-bookings/settings",
        json={"settings": {**before, "send_mode": "off"}, "based_on": before},
    )

    assert response.status_code == 200, response.text
    assert await stored() == DEFAULTS


@pytest.mark.asyncio
async def test_a_save_from_a_stale_screen_is_refused(api):
    await store_settings(send_mode="test", live_units=[])

    response = await api.patch(
        "/api/showing-bookings/settings",
        json={"settings": {**DEFAULTS, "send_mode": "live"}, "based_on": DEFAULTS},
    )

    assert response.status_code == 409
    assert (await stored())["send_mode"] == "test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"send_mode": "loud"},
        {"live_all_units": "true"},
        {"live_units": [" "]},
        {"surprise": True},
    ],
)
async def test_invalid_settings_are_not_saved(api, change):
    response = await api.patch(
        "/api/showing-bookings/settings",
        json={"settings": {**DEFAULTS, **change}, "based_on": DEFAULTS},
    )
    assert response.status_code == 422
    assert await stored() is None


# Notices


async def queue_notices() -> tuple[int, int]:
    """A text and an invite, due now: the job run by hand uses the real clock."""
    showing = await add_showing(await add_agent(), starts_at=at(16))
    queued_at = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        text = await outbox.enqueue(
            session,
            kind="prospect_confirmation",
            channel="sms",
            showing_id=showing,
            recipient=PROSPECT_PHONE,
            recipient_key="attendee:1",
            payload=TextPayload(body="You're booked for Wed, Apr 14 at 4:00 PM."),
            now=queued_at,
        )
        invite = await outbox.enqueue(
            session,
            kind="prospect_invite",
            channel="calendar_invite",
            showing_id=showing,
            recipient="alex@example.com",
            recipient_key="attendee:1",
            payload=CalendarPayload(
                operation="create",
                calendar_id="showings@example.com",
                summary="Showing of 320-03",
                location="100 Example Ave",
                description="Cancel: https://example.com/c/token",
                starts_at=at(16),
                ends_at=at(16, 30),
                attendees=["alex@example.com"],
            ),
            now=queued_at,
        )
        await session.commit()
        return text.id, invite.id


@pytest.mark.asyncio
async def test_notices_are_listed_newest_first_with_their_wording(api):
    text, invite = await queue_notices()

    body = (await api.get("/api/showing-bookings/notices")).json()

    assert [notice["id"] for notice in body] == [invite, text]
    assert body[1]["wording"] == "You're booked for Wed, Apr 14 at 4:00 PM."
    assert body[0]["wording"] == (
        "New event: Showing of 320-03\n100 Example Ave\n"
        "Cancel: https://example.com/c/token\nGuests: alex@example.com"
    )
    assert {(n["status"], n["mode"], n["unit"]) for n in body} == {("pending", None, "320-03")}


@pytest.mark.asyncio
async def test_an_email_notice_shows_its_subject_and_body(api):
    showing = await add_showing(await add_agent(), starts_at=at(16))
    async with AsyncSessionFactory() as session:
        await outbox.enqueue(
            session,
            kind="lead_showing_agreed",
            channel="email",
            showing_id=showing,
            recipient="alex@example.com",
            recipient_key="thread:thread-a",
            payload=EmailPayload(subject="Your showing", body="See you Wed at 4:00 PM."),
            now=datetime.now(UTC),
        )
        await session.commit()

    (notice,) = (await api.get("/api/showing-bookings/notices")).json()
    assert (notice["channel"], notice["wording"]) == (
        "email",
        "Subject: Your showing\n\nSee you Wed at 4:00 PM.",
    )


@pytest.mark.asyncio
async def test_in_production_the_job_is_not_run_by_hand(api, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await queue_notices()

    response = await api.post("/api/showing-bookings/notices/send-pending")

    assert response.status_code == 403
    body = (await api.get("/api/showing-bookings/notices")).json()
    assert {n["status"] for n in body} == {"pending"}


@pytest.mark.asyncio
async def test_outside_production_the_job_is_run_once_by_hand(api):
    await queue_notices()

    response = await api.post("/api/showing-bookings/notices/send-pending")

    assert response.status_code == 200
    assert response.json() == {
        "captured": 2,
        "sent": 0,
        "failed": 0,
        "unknown": 0,
        "skipped": 0,
        "retrying": 0,
        "interrupted": 0,
        "released": 0,
        "stopped": 0,
        "prospect_reminders": 0,
        "resident_reminders": 0,
        "host_mornings": 0,
        "apply_texts": 0,
        "hour_texts": 0,
        "lead_email_reminders": 0,
        "alerts": 0,
    }
    body = (await api.get("/api/showing-bookings/notices")).json()
    assert {(n["status"], n["mode"], n["guard_reason"]) for n in body} == {
        ("captured", "capture", "send_mode_capture")
    }


# Who may call


def depends_on(dependant: Dependant, call) -> bool:
    return any(d.call is call or depends_on(d, call) for d in dependant.dependencies)


def test_every_route_requires_a_sernia_user():
    assert routes.router.routes
    for route in routes.router.routes:
        assert depends_on(route.dependant, routes.verify_serniacapital_user), route.path


@pytest.mark.asyncio
async def test_without_a_sign_in_every_route_is_refused_and_nothing_runs(monkeypatch):
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=lambda request, options: SimpleNamespace(is_signed_in=False)
        ),
    )
    drain, deliver = AsyncMock(), AsyncMock()
    monkeypatch.setattr(outbox, "drain", drain)
    monkeypatch.setattr(outbox, "deliver", deliver)
    transport = httpx.ASGITransport(app=app(signed_in=False))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        calls = [
            client.get("/api/showing-bookings/settings"),
            client.patch(
                "/api/showing-bookings/settings",
                json={"settings": {**DEFAULTS, "send_mode": "live"}, "based_on": DEFAULTS},
            ),
            client.get("/api/showing-bookings/notices"),
            client.get("/api/showing-bookings/notices/captured"),
            client.post("/api/showing-bookings/notices/send-captured", json={"ids": [1]}),
            client.post("/api/showing-bookings/notices/send-pending"),
        ]
        for call in calls:
            assert (await call).status_code == 401
    drain.assert_not_called()
    deliver.assert_not_called()
    assert await stored() is None


@pytest.mark.asyncio
async def test_a_user_without_a_verified_sernia_email_is_refused(monkeypatch):
    person = SimpleNamespace(
        id="user_other",
        email_addresses=[
            SimpleNamespace(
                email_address="pat@example.com", verification=SimpleNamespace(status="verified")
            )
        ],
    )
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=lambda request, options: SimpleNamespace(
                is_signed_in=True, payload={"sub": "user_other"}
            ),
            users=SimpleNamespace(get_async=AsyncMock(return_value=person)),
        ),
    )
    clerk_module._user_cache.clear()
    transport = httpx.ASGITransport(app=app(signed_in=False))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/showing-bookings/settings", headers={"Authorization": "Bearer token"}
        )
    clerk_module._user_cache.clear()
    assert response.status_code == 401
