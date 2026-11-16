"""The team's routes for showing agents, the Showings section and the lead agent's switch.

Uses the test database. Sign-in is replaced by a stand-in Sernia user. The
open-times rules themselves are tested in ``test_showing_open_times.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import open_times, routes
from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.showing_bookings.seed import SAMPLE_AGENTS
from api.src.tests.showing_helpers import (
    add_attendee,
    add_showing,
    clear_showing_data,
    store_settings,
)

PERSON = SimpleNamespace(id="user_test")
AGENTS = "/api/showing-bookings/agents"
JANE = {
    "name": "Jane Doe",
    "email": "Jane.Doe@Example.com",
    "mobile": "412-555-0101",
    "busy_calendar_ids": ["jane.doe@example.com"],
    "weekly_hours": [{"days": ["mon", "wed"], "start": "16:00", "end": "18:30"}],
    "preferred_windows": [{"days": ["wed"], "start": "16:00", "end": "17:00"}],
    "priority": 1,
}


def app() -> FastAPI:
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    application.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    return application


@pytest_asyncio.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    open_times.forget_free_busy()
    await clear_showing_data()
    yield
    open_times.forget_free_busy()
    await clear_showing_data()


@pytest_asyncio.fixture
async def api():
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def add_connection(email: str = "team@example.com", *, days_ago: int = 30) -> int:
    async with AsyncSessionFactory() as session:
        row = CalendarConnection(
            google_email=email,
            google_subject=f"sub-{email}",
            connected_by="pat@serniacapital.com",
            connected_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
        session.add(row)
        await session.commit()
        return row.id


# Adding, changing and deactivating agents


@pytest.mark.asyncio
async def test_an_agent_is_added_with_their_hours_and_read_through_the_teams_connection(api):
    await add_connection("later@example.com", days_ago=1)
    await add_connection("team@example.com", days_ago=30)

    response = await api.post(AGENTS, json=JANE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "jane.doe@example.com" and body["mobile"] == "+14125550101"
    assert body["weekly_hours"] == [
        {"days": ["mon", "wed"], "start": "16:00:00", "end": "18:30:00"}
    ]
    # No connection chosen: the team's, the oldest connected one.
    assert body["connection_id"] is None and body["reads_through"] == "team@example.com"
    assert body["active"] is True
    listed = (await api.get(AGENTS)).json()
    assert [agent["name"] for agent in listed] == ["Jane Doe"]


@pytest.mark.asyncio
async def test_an_agent_is_checked_before_saving(api):
    bad = {
        **JANE,
        "priority": 0,
        "busy_calendar_ids": ["jane doe", "x@example.com", "X@example.com"],
        "weekly_hours": [{"days": [], "start": "18:00", "end": "16:00"}],
        "connection_id": 999999,
    }
    response = await api.post(AGENTS, json=bad)
    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert any(problem.startswith("priority") for problem in problems)
    assert any("'jane doe' is not a calendar address" in problem for problem in problems)
    assert "calendars: a calendar is listed twice" in problems
    assert any(problem.startswith("hours: a window") for problem in problems)
    assert "connection: no such calendar connection" in problems

    assert (await api.post(AGENTS, json={**JANE, "mobile": "555"})).status_code == 422
    assert (await api.post(AGENTS, json=JANE)).status_code == 201
    again = await api.post(AGENTS, json={**JANE, "name": "Jane Again"})
    assert again.status_code == 422
    assert "already another agent's" in again.json()["detail"]["problems"][0]


@pytest.mark.asyncio
async def test_an_agent_is_changed_and_deactivated(api):
    connection = await add_connection("jane.own@example.com")
    agent_id = (await api.post(AGENTS, json=JANE)).json()["id"]

    changed = await api.put(
        f"{AGENTS}/{agent_id}", json={**JANE, "priority": 3, "connection_id": connection}
    )
    assert changed.status_code == 200
    assert (changed.json()["priority"], changed.json()["reads_through"]) == (
        3,
        "jane.own@example.com",
    )

    off = await api.post(f"{AGENTS}/{agent_id}/deactivate")
    assert off.json()["active"] is False
    on = await api.post(f"{AGENTS}/{agent_id}/activate")
    assert on.json()["active"] is True
    assert (await api.put(f"{AGENTS}/999999", json=JANE)).status_code == 404
    assert (await api.post(f"{AGENTS}/999999/deactivate")).status_code == 404


@pytest.mark.asyncio
async def test_sample_agents_load_once_outside_production(api):
    first = await api.post(f"{AGENTS}/load-samples")
    assert first.status_code == 200
    assert first.json()["added"] == [agent.name for agent in SAMPLE_AGENTS]
    assert (await api.post(f"{AGENTS}/load-samples")).json()["added"] == []


@pytest.mark.asyncio
async def test_sample_agents_are_refused_in_production(api, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    response = await api.post(f"{AGENTS}/load-samples")
    assert response.status_code == 403
    async with AsyncSessionFactory() as session:
        assert (await session.scalars(select(ShowingAgentRecord))).all() == []


# The Showings section


@pytest.mark.asyncio
async def test_the_showings_section_shows_offers_booked_showings_and_the_sample_calendar(
    api, monkeypatch
):
    unit = "991-A"

    async def read_listings(session):
        return [RentalListing(unit=unit, address="991 Example St")]

    async def read_rules(session, lock=False):
        return ShowingRules()

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    await api.post(f"{AGENTS}/load-samples")
    async with AsyncSessionFactory() as session:
        jane = await session.scalar(
            select(ShowingAgentRecord.id).where(ShowingAgentRecord.name == "Jane Doe")
        )
    soon = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    showing = await add_showing(jane, unit=unit, starts_at=soon)
    await add_attendee(showing)

    response = await api.get("/api/showing-bookings/showings")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lead_agent_uses_open_times"] is False
    found = body["open_times"]
    # No calendar is connected here, so the made-up week stands in, and says so.
    assert found["sample_calendar_in_use"] is True
    assert {agent["status"] for agent in found["agents"]} == {"sample"}
    (offers,) = found["units"]
    assert offers["unit"] == unit and offers["offers"]
    assert [(item["unit"], item["host_name"], item["attendees"]) for item in body["booked"]] == [
        (unit, "Jane Doe", 1)
    ]


@pytest.mark.asyncio
async def test_the_showings_section_says_why_a_unit_has_no_times(api, monkeypatch):
    async def read_listings(session):
        return [RentalListing(unit="991-A")]

    async def read_rules(session, lock=False):
        return ShowingRules()

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)

    body = (await api.get("/api/showing-bookings/showings")).json()

    (unit,) = body["open_times"]["units"]
    assert unit["offers"] == [] and unit["why_none"] == "No active showing agents."


# The lead agent's switch


@pytest.mark.asyncio
async def test_the_lead_agent_switch_changes_only_itself(api):
    await store_settings(send_mode="off", live_units=[], test_sends_enabled=True)

    response = await api.put(
        "/api/showing-bookings/settings/lead-agent", json={"lead_agent_uses_open_times": True}
    )

    assert response.status_code == 200
    assert response.json() == {
        "send_mode": "off",
        "live_units": [],
        "test_sends_enabled": True,
        "lead_agent_uses_open_times": True,
    }
    shown = (await api.get("/api/showing-bookings/settings")).json()["settings"]
    assert shown["lead_agent_uses_open_times"] is True and shown["send_mode"] == "off"
