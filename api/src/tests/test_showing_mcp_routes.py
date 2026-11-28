"""The showings routes the Sernia MCP service calls (``showing_bookings/mcp_routes.py``).

What these check is the door, since each route calls the team's own function
and ``test_showing_team_actions.py`` and ``test_showing_cancelling.py`` cover
what those do:

- the router is exactly the six routes the MCP may reach, all behind
  ``mcp_user``;
- ``mcp_user`` accepts only an OAuth access token issued to the MCP's Clerk
  client, and a browser session is refused on every one of these routes;
- the MCP's token is refused on the team's own routes, so it reaches nothing
  outside this router;
- through these routes a booking, a new host, a removal, a cancellation and
  an attendance mark leave the same rows as the Booking settings tab.

Quo is a ``RecordingTextSender`` and the calendars an ``InMemoryCalendar``, as
in the team tests. Nothing is sent anywhere. Names and numbers are made up.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import cancelling, mcp_routes, open_times, routes
from api.src.tests.showing_helpers import clear_showing_data
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    JANE,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    install,
    rows,
)
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.tests.test_showing_team_actions import (
    OTHER_UNIT,
    RESIDENT,
    SAM,
    add_sam,
    caller,
    live,
)
from api.src.utils import clerk as clerk_module

MCP = "/api/showing-bookings/mcp"
TEAM = "/api/showing-bookings"
MCP_CLIENT = "client_mcp"
PAT = "pat@serniacapital.com"


def person(email: str = PAT, status: str = "verified") -> SimpleNamespace:
    return SimpleNamespace(
        id="user_test",
        email_addresses=[
            SimpleNamespace(email_address=email, verification=SimpleNamespace(status=status))
        ],
    )


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE},{RESIDENT}"
    )
    monkeypatch.setenv(
        "SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE},{SAM}"
    )
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()
    state = PublicWorld()
    state.other_listings = [RentalListing(unit=OTHER_UNIT, address="9 Other Rd")]
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()


def mcp_app(world: PublicWorld) -> FastAPI:
    """The MCP's routes with a signed-in team member behind the MCP's token."""
    application = FastAPI()
    application.include_router(mcp_routes.mcp_router, prefix="/api")
    application.dependency_overrides[mcp_routes.mcp_user] = person
    application.dependency_overrides[routes.text_sender] = lambda: world.texts
    application.dependency_overrides[routes.clock] = lambda: world.now
    return application


@pytest.fixture
def clerk(monkeypatch):
    """Clerk, mocked: it knows one OAuth token, issued to the MCP's client for Pat."""
    monkeypatch.setenv(mcp_routes.MCP_CLIENT_ID_ENV, MCP_CLIENT)
    seen: list[list[str]] = []
    state = SimpleNamespace(
        oauth=SimpleNamespace(
            is_signed_in=True,
            payload={"client_id": MCP_CLIENT, "subject": "user_test", "scopes": ["email"]},
        )
    )

    def authenticate(request, options):
        seen.append(options.accepts_token)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if options.accepts_token == ["oauth_token"] and token.startswith("oat_"):
            return state.oauth
        return SimpleNamespace(is_signed_in=False, payload=None)

    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=authenticate,
            users=SimpleNamespace(get_async=AsyncMock(return_value=person())),
        ),
    )
    clerk_module._user_cache.clear()
    yield SimpleNamespace(state=state, seen=seen)
    clerk_module._user_cache.clear()


def every_call(client: httpx.AsyncClient, base: str, token: str) -> list:
    """One request to each of the six routes, under ``base``, with ``token``."""
    headers = {"authorization": f"Bearer {token}"}
    return [
        client.get(f"{base}/showings", headers=headers),
        client.post(f"{base}/showings/bookings", json=caller(at(17)), headers=headers),
        client.post(f"{base}/showings/1/cancel", headers=headers),
        client.post(f"{base}/showings/1/attendees/1/remove", headers=headers),
        client.post(f"{base}/showings/1/host", json={"agent_id": 1}, headers=headers),
        client.put(
            f"{base}/showings/1/attendees/1/attendance",
            json={"attendance": "attended"},
            headers=headers,
        ),
    ]


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


def test_the_router_is_the_six_routes_the_mcp_may_reach_all_behind_its_own_check():
    gate = [dependency.dependency for dependency in mcp_routes.mcp_router.dependencies]
    assert gate == [mcp_routes.mcp_user]

    reachable = {
        (method, route.path) for route in mcp_routes.mcp_router.routes for method in route.methods
    }
    assert reachable == {
        ("GET", f"{MCP.removeprefix('/api')}/showings"),
        ("POST", f"{MCP.removeprefix('/api')}/showings/bookings"),
        ("POST", f"{MCP.removeprefix('/api')}/showings/{{showing_id}}/cancel"),
        (
            "POST",
            f"{MCP.removeprefix('/api')}/showings/{{showing_id}}/attendees/{{attendee_id}}/remove",
        ),
        ("POST", f"{MCP.removeprefix('/api')}/showings/{{showing_id}}/host"),
        (
            "PUT",
            f"{MCP.removeprefix('/api')}/showings/{{showing_id}}"
            "/attendees/{attendee_id}/attendance",
        ),
    }


class TestMcpUser:
    """Which callers ``mcp_user`` accepts, with Clerk mocked."""

    def request(self, token: str):
        return SimpleNamespace(
            headers={"authorization": f"Bearer {token}"},
            method="GET",
            url=SimpleNamespace(path=f"{MCP}/showings"),
        )

    @pytest.mark.asyncio
    async def test_the_mcp_s_oauth_token_for_a_sernia_user_is_accepted(self, clerk):
        user = await mcp_routes.mcp_user(self.request("oat_abc"))

        assert user.id == "user_test"
        assert clerk.seen == [["oauth_token"]]

    @pytest.mark.asyncio
    async def test_a_token_issued_to_another_oauth_client_is_refused(self, clerk):
        clerk.state.oauth.payload["client_id"] = "client_someone_else"

        with pytest.raises(Exception) as refused:
            await mcp_routes.mcp_user(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    async def test_oauth_tokens_are_refused_when_no_mcp_client_is_configured(
        self, clerk, monkeypatch
    ):
        monkeypatch.delenv(mcp_routes.MCP_CLIENT_ID_ENV)

        with pytest.raises(Exception) as refused:
            await mcp_routes.mcp_user(self.request("oat_abc"))
        assert refused.value.status_code == 401

    @pytest.mark.asyncio
    async def test_the_user_must_have_a_verified_sernia_email(self, clerk, monkeypatch):
        monkeypatch.setattr(
            clerk_module.clerk_client.users,
            "get_async",
            AsyncMock(return_value=person(email="pat@example.com")),
        )

        with pytest.raises(Exception) as refused:
            await mcp_routes.mcp_user(self.request("oat_abc"))
        assert refused.value.status_code == 401


@pytest.mark.asyncio
async def test_a_browser_session_is_refused_on_every_mcp_route(world, clerk):
    application = FastAPI()
    application.include_router(mcp_routes.mcp_router, prefix="/api")
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for call in every_call(client, MCP, "eyJhbGciOi.session.jwt"):
            assert (await call).status_code == 401
    # Clerk was only ever asked for an OAuth token here: a session is never tried.
    assert clerk.seen and all(accepts == ["oauth_token"] for accepts in clerk.seen)
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_the_mcp_s_token_is_refused_on_the_team_s_own_routes(world, clerk):
    """So the MCP reaches nothing in this module outside its own router."""
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        calls = every_call(client, TEAM, "oat_abc") + [
            client.get(f"{TEAM}/settings", headers={"authorization": "Bearer oat_abc"}),
            client.get(f"{TEAM}/agents", headers={"authorization": "Bearer oat_abc"}),
        ]
        for call in calls:
            assert (await call).status_code == 401
    assert all(accepts == ["session_token"] for accepts in clerk.seen)
    assert await rows() == ([], [], [])


# ---------------------------------------------------------------------------
# The same actions as the Booking settings tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_booking_through_the_mcp_is_the_team_s_booking_and_shows_in_the_list(world):
    await live()
    body = caller(at(16), mobile="412-555-0119", email=CASEY_EMAIL, texts=True)
    async with world.client(mcp_app(world)) as client:
        booked = await client.post(f"{MCP}/showings/bookings", json=body)
        assert booked.status_code == 201, booked.text
        open_times.forget_free_busy()
        listed = await client.get(f"{MCP}/showings")

    result = booked.json()
    assert (result["kind"], result["texts_accepted"], result["confirmation"]) == (
        "new",
        True,
        "sent",
    )
    _, (casey,), _ = await rows()
    # The consent record names the team member behind the MCP's token.
    assert (casey.name, casey.source, casey.texts_consent_page) == (
        "Casey Morgan",
        "team",
        f"team:{PAT}",
    )
    assert listed.status_code == 200, listed.text
    (showing,) = listed.json()["booked"]
    assert (showing["id"], showing["unit"]) == (result["showing_id"], UNIT)
    assert [p["name"] for p in showing["people"]] == ["Casey Morgan"]
    assert any(unit["unit"] == UNIT for unit in listed.json()["open_times"]["units"])


@pytest.mark.asyncio
async def test_a_refused_booking_answers_as_the_team_s_route_does(world):
    async with world.client(mcp_app(world)) as client:
        answer = await client.post(f"{MCP}/showings/bookings", json=caller(at(16), name=" "))

    assert answer.status_code == 422
    assert answer.json()["detail"]["code"] == "invalid"
    assert "name" in answer.json()["detail"]["fields"]
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_new_host_a_removal_and_a_cancellation_through_the_mcp(world):
    sam = await add_sam()
    async with world.client(mcp_app(world)) as client:
        first = (await client.post(f"{MCP}/showings/bookings", json=caller(at(16)))).json()
        second = (
            await client.post(f"{MCP}/showings/bookings", json=caller(at(16), name="Robin Lee"))
        ).json()
        showing_id = first["showing_id"]
        assert second["showing_id"] == showing_id and second["kind"] == "join"

        moved = await client.post(f"{MCP}/showings/{showing_id}/host", json={"agent_id": sam})
        assert moved.status_code == 200, moved.text
        assert (moved.json()["host_name"], moved.json()["previous_host_name"]) == (
            "Sam Roe",
            "Jane Doe",
        )

        removed = await client.post(
            f"{MCP}/showings/{showing_id}/attendees/{second['attendee_id']}/remove"
        )
        assert removed.status_code == 200, removed.text
        assert (removed.json()["attendees_cancelled"], removed.json()["showing_status"]) == (
            1,
            "scheduled",
        )

        cancelled = await client.post(f"{MCP}/showings/{showing_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert (cancelled.json()["attendees_cancelled"], cancelled.json()["showing_status"]) == (
            1,
            "cancelled",
        )
        missing = await client.post(f"{MCP}/showings/999999/cancel")
        assert missing.status_code == 404

    (showing,), attendees, _ = await rows()
    assert (showing.status, showing.agent_id) == ("cancelled", sam)
    assert [(a.name, a.status) for a in attendees] == [
        ("Casey Morgan", "cancelled"),
        ("Robin Lee", "cancelled"),
    ]


@pytest.mark.asyncio
async def test_who_came_is_marked_through_the_mcp_once_the_showing_has_started(world):
    async with world.client(mcp_app(world)) as client:
        booked = (await client.post(f"{MCP}/showings/bookings", json=caller(at(16)))).json()
        path = f"{MCP}/showings/{booked['showing_id']}/attendees/{booked['attendee_id']}/attendance"
        early = await client.put(path, json={"attendance": "attended"})
        assert (early.status_code, early.json()["detail"]["code"]) == (409, "not_started")

        world.now = at(16, 5)
        marked = await client.put(path, json={"attendance": "attended"})

    assert marked.status_code == 200, marked.text
    _, (casey,), _ = await rows()
    assert casey.attendance == "attended"
