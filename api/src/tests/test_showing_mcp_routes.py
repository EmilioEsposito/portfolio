"""The showings routes the Sernia MCP service calls (``showing_bookings/mcp_routes.py``).

- The door: the router is exactly its five routes, all behind ``mcp_user``,
  which accepts only an OAuth access token issued to the MCP's Clerk client;
  a browser session is refused on these routes, and the MCP's token on the
  team's own.
- The read is the same document the Sernia agent's ``showings_read`` gives.
- A booking or cancellation happens only with the paragraph a preview gave,
  the same paragraph the agent's approval card shows; a stale paragraph
  changes nothing and answers with the new one, whether the route's first
  comparison catches it or the action's own check under its lock does.

``test_showing_tools.py`` and ``test_showing_team_actions.py`` cover what the
shared functions do. Quo is a ``RecordingTextSender``, the calendars an
``InMemoryCalendar``. Nothing is sent anywhere. Names and numbers are made up.
"""

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api.src.sernia_ai.tools import showing_tools as tools
from api.src.showing_bookings import effects, mcp_routes, open_times, routes
from api.src.showing_bookings.team_actions import TeamBookingRequest
from api.src.tests import test_showing_tools as agent_tests
from api.src.tests.showing_public import (
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    UNIT,
    PublicWorld,
    at,
    rows,
)
from api.src.tests.test_showing_tools import (
    PLACE,
    THREAD,
    UNKNOWN_THREAD,
    WHEN,
    add_lead_thread,
    ctx,
    page_book,
)
from api.src.utils import clerk as clerk_module

# The agent tests' world: the same unit, agents, calendars and send mode.
world = agent_tests.world

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


def mcp_app(world: PublicWorld) -> FastAPI:
    """The MCP's routes with a signed-in team member behind the MCP's token."""
    application = FastAPI()
    application.include_router(mcp_routes.mcp_router, prefix="/api")
    application.dependency_overrides[mcp_routes.mcp_user] = person
    application.dependency_overrides[routes.text_sender] = lambda: world.texts
    application.dependency_overrides[routes.clock] = lambda: world.now
    return application


def casey(**changes) -> dict:
    """The MCP's ``showing_book`` arguments for Casey at 4 PM on Wednesday."""
    return {
        "unit": UNIT,
        "start": at(16).isoformat(),
        "name": "Casey Morgan",
        "mobile": CASEY_MOBILE,
        "email": CASEY_EMAIL,
        "texts_accepted": True,
        **changes,
    }


async def preview_and_book(client: httpx.AsyncClient, body: dict) -> httpx.Response:
    preview = await client.post(f"{MCP}/showings/bookings/preview", json=body)
    assert preview.status_code == 200, preview.text
    return await client.post(
        f"{MCP}/showings/bookings", json={**body, "effect": preview.json()["effect"]}
    )


@pytest.fixture
def clerk(monkeypatch):
    """Clerk, mocked: it knows OAuth tokens starting ``oat_``, issued to the MCP for Pat."""
    monkeypatch.setenv(mcp_routes.MCP_CLIENT_ID_ENV, MCP_CLIENT)
    seen: list[list[str]] = []
    oauth = SimpleNamespace(
        is_signed_in=True,
        payload={"client_id": MCP_CLIENT, "subject": "user_test", "scopes": ["email"]},
    )

    def authenticate(request, options):
        seen.append(options.accepts_token)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if options.accepts_token == ["oauth_token"] and token.startswith("oat_"):
            return oauth
        return SimpleNamespace(is_signed_in=False, payload=None)

    users = SimpleNamespace(get_async=AsyncMock(return_value=person()))
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(authenticate_request=authenticate, users=users),
    )
    clerk_module._user_cache.clear()
    yield SimpleNamespace(oauth=oauth, users=users, seen=seen)
    clerk_module._user_cache.clear()


def every_call(client: httpx.AsyncClient, token: str) -> list:
    """One request to each of the five MCP routes, with ``token``."""
    headers = {"authorization": f"Bearer {token}"}
    cancellation = {"showing_id": 1, "effect": "x"}
    return [
        client.get(f"{MCP}/showings", headers=headers),
        client.post(f"{MCP}/showings/bookings/preview", json=casey(), headers=headers),
        client.post(f"{MCP}/showings/bookings", json={**casey(), "effect": "x"}, headers=headers),
        client.post(
            f"{MCP}/showings/cancellations/preview", json={"showing_id": 1}, headers=headers
        ),
        client.post(f"{MCP}/showings/cancellations", json=cancellation, headers=headers),
    ]


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


def test_the_router_is_its_five_routes_all_behind_the_mcp_s_own_check():
    gate = [dependency.dependency for dependency in mcp_routes.mcp_router.dependencies]
    assert gate == [mcp_routes.mcp_user]
    prefix = MCP.removeprefix("/api")
    reachable = {
        (method, route.path) for route in mcp_routes.mcp_router.routes for method in route.methods
    }
    assert reachable == {
        ("GET", f"{prefix}/showings"),
        ("POST", f"{prefix}/showings/bookings/preview"),
        ("POST", f"{prefix}/showings/bookings"),
        ("POST", f"{prefix}/showings/cancellations/preview"),
        ("POST", f"{prefix}/showings/cancellations"),
    }


class TestMcpUser:
    """Which callers ``mcp_user`` accepts, with Clerk mocked."""

    def request(self, token: str):
        return SimpleNamespace(headers={"authorization": f"Bearer {token}"})

    @pytest.mark.asyncio
    async def test_the_mcp_s_oauth_token_for_a_sernia_user_is_accepted(self, clerk):
        user = await mcp_routes.mcp_user(self.request("oat_abc"))

        assert user.id == "user_test"
        assert clerk.seen == [["oauth_token"]]

    @pytest.mark.asyncio
    async def test_a_token_issued_to_another_oauth_client_is_refused(self, clerk):
        clerk.oauth.payload["client_id"] = "client_someone_else"

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
    async def test_the_user_must_have_a_verified_sernia_email(self, clerk):
        clerk.users.get_async.return_value = person(email="pat@example.com")

        with pytest.raises(Exception) as refused:
            await mcp_routes.mcp_user(self.request("oat_abc"))
        assert refused.value.status_code == 401


@pytest.mark.asyncio
async def test_a_browser_session_is_refused_on_every_mcp_route(world, clerk):
    application = FastAPI()
    application.include_router(mcp_routes.mcp_router, prefix="/api")
    async with world.client(application) as client:
        for call in every_call(client, "eyJhbGciOi.session.jwt"):
            assert (await call).status_code == 401
    # Only an OAuth token was ever asked for here: a session is never tried.
    assert clerk.seen and all(accepts == ["oauth_token"] for accepts in clerk.seen)
    assert (await rows())[1] == []


@pytest.mark.asyncio
async def test_the_mcp_s_token_is_refused_on_the_team_s_own_routes(world, clerk):
    """So the MCP reaches nothing in this module outside its own router."""
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    headers = {"authorization": "Bearer oat_abc"}
    async with world.client(application) as client:
        calls = [
            client.get(f"{TEAM}/showings", headers=headers),
            client.get(f"{TEAM}/settings", headers=headers),
            client.get(f"{TEAM}/agents", headers=headers),
            client.post(f"{TEAM}/showings/1/cancel", headers=headers),
            client.post(f"{TEAM}/showings/1/host", json={"agent_id": 1}, headers=headers),
        ]
        for call in calls:
            assert (await call).status_code == 401
    assert all(accepts == ["session_token"] for accepts in clerk.seen)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_read_is_the_agent_s_read(world):
    await add_lead_thread(THREAD)
    for args in ({}, {"unit": UNIT, "zillow_thread_id": THREAD}):
        open_times.forget_free_busy()
        agent = json.loads(await tools.showings_read(ctx(), **args))
        open_times.forget_free_busy()
        async with world.client(mcp_app(world)) as client:
            answer = await client.get(f"{MCP}/showings", params=args)

        assert answer.status_code == 200, answer.text
        assert answer.json() == agent
    assert answer.json()["units"][0]["booking_link"]
    assert answer.json()["thread"]["zillow_thread_id"] == THREAD


@pytest.mark.asyncio
async def test_an_unknown_unit_is_refused_with_the_units_on_file(world):
    async with world.client(mcp_app(world)) as client:
        answer = await client.get(f"{MCP}/showings", params={"unit": "99-Z"})

    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "unknown_unit"
    assert UNIT in answer.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_preview_is_the_agent_s_paragraph_and_books_nothing(world):
    async with world.client(mcp_app(world)) as client:
        answer = await client.post(f"{MCP}/showings/bookings/preview", json=casey())

    assert answer.status_code == 200, answer.text
    request = TeamBookingRequest(
        unit=UNIT,
        start=at(16).isoformat(),
        name="Casey Morgan",
        mobile=CASEY_MOBILE,
        email=CASEY_EMAIL,
        texts=True,
    )
    expected = await effects.preview_booking(request, now=world.now)
    assert answer.json()["effect"] == expected.sentence
    assert PLACE in expected.sentence and WHEN in expected.sentence
    assert (await rows())[1] == []


@pytest.mark.asyncio
async def test_a_booking_with_the_approved_paragraph_is_made_under_the_person(world):
    async with world.client(mcp_app(world)) as client:
        answer = await preview_and_book(client, casey())

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert result["result"].startswith(f"Booked Casey Morgan at {UNIT} on {WHEN} (a new showing).")
    assert result["booking"]["confirmation"] == "sent"
    _, (attendee,), _ = await rows()
    assert (attendee.source, attendee.texts_consent_page) == (
        "team",
        f"team:{PAT} (via Sernia MCP)",
    )
    assert [text.to for text in world.texts.sent][:1] == [CASEY_MOBILE]


@pytest.mark.asyncio
async def test_a_stale_paragraph_books_nothing_and_answers_with_the_new_one(world):
    async with world.client(mcp_app(world)) as client:
        preview = await client.post(f"{MCP}/showings/bookings/preview", json=casey())
        # Someone books the time on the page meanwhile: Casey would now join them.
        await page_book(world)
        answer = await client.post(
            f"{MCP}/showings/bookings", json={**casey(), "effect": preview.json()["effect"]}
        )

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["code"] == mcp_routes.EFFECT_CHANGED
    assert "already booked" in detail["effect"] and detail["effect"] != preview.json()["effect"]
    _, attendees, _ = await rows()
    assert [a.name for a in attendees] != [] and "Casey Morgan" not in [a.name for a in attendees]


@pytest.mark.asyncio
async def test_a_booking_for_a_zillow_lead_is_the_thread_s_own(world):
    await add_lead_thread(THREAD)
    async with world.client(mcp_app(world)) as client:
        answer = await preview_and_book(client, casey(zillow_thread_id=f" {THREAD} "))
        unknown = await client.post(
            f"{MCP}/showings/bookings/preview", json=casey(zillow_thread_id=UNKNOWN_THREAD)
        )

    assert answer.status_code == 201, answer.text
    _, (attendee,), _ = await rows()
    assert attendee.zillow_thread_id == THREAD
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "unknown_thread")


@pytest.mark.asyncio
async def test_a_refused_booking_answers_with_the_reason_and_the_fields(world):
    async with world.client(mcp_app(world)) as client:
        nameless = await client.post(f"{MCP}/showings/bookings/preview", json=casey(name=" "))
        closed = await client.post(
            f"{MCP}/showings/bookings/preview", json=casey(start=at(3).isoformat())
        )
        extra = await client.post(f"{MCP}/showings/bookings/preview", json=casey(texts="yes"))

    assert nameless.status_code == 422
    assert nameless.json()["detail"]["code"] == "invalid"
    assert "name" in nameless.json()["detail"]["fields"]
    assert (closed.status_code, closed.json()["detail"]["code"]) == (409, effects.NOT_OPEN)
    assert extra.status_code == 422  # an argument the tool does not have
    assert (await rows())[1] == []


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


async def two_booked(world: PublicWorld) -> tuple[int, int, int]:
    """Alex booked on the page, Casey joined through the MCP: the showing and both ids."""
    await page_book(world)
    async with world.client(mcp_app(world)) as client:
        answer = await preview_and_book(client, casey())
    assert answer.status_code == 201, answer.text
    (showing,), (alex, casey_row), _ = await rows()
    return showing.id, alex.id, casey_row.id


async def preview_and_cancel(client: httpx.AsyncClient, body: dict) -> httpx.Response:
    preview = await client.post(f"{MCP}/showings/cancellations/preview", json=body)
    assert preview.status_code == 200, preview.text
    return await client.post(
        f"{MCP}/showings/cancellations", json={**body, "effect": preview.json()["effect"]}
    )


@pytest.mark.asyncio
async def test_one_person_is_removed_then_the_showing_is_cancelled(world):
    showing_id, alex_id, casey_id = await two_booked(world)
    async with world.client(mcp_app(world)) as client:
        preview = await client.post(
            f"{MCP}/showings/cancellations/preview",
            json={"showing_id": showing_id, "attendee_id": casey_id},
        )
        assert preview.json()["effect"] == await effects.preview_cancellation(
            showing_id, casey_id, now=world.now
        )
        removed = await preview_and_cancel(
            client, {"showing_id": showing_id, "attendee_id": casey_id}
        )
        cancelled = await preview_and_cancel(client, {"showing_id": showing_id})
        again = await client.post(
            f"{MCP}/showings/cancellations/preview", json={"showing_id": showing_id}
        )

    assert removed.status_code == 200, removed.text
    assert removed.json()["cancellation"]["showing_status"] == "scheduled"
    assert cancelled.json()["result"].startswith(
        f"Cancelled. Showing {showing_id} is now cancelled"
    )
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "already_cancelled")
    (showing,), attendees, _ = await rows()
    assert showing.status == "cancelled"
    assert {a.id: a.status for a in attendees} == {alex_id: "cancelled", casey_id: "cancelled"}
    assert ALEX_MOBILE in [text.to for text in world.texts.sent]


@pytest.mark.asyncio
async def test_a_stale_cancellation_paragraph_cancels_nothing(world):
    showing_id, alex_id, casey_id = await two_booked(world)
    async with world.client(mcp_app(world)) as client:
        whole = await client.post(
            f"{MCP}/showings/cancellations/preview", json={"showing_id": showing_id}
        )
        # Casey is removed meanwhile, so cancelling the showing would now tell only Alex.
        await preview_and_cancel(client, {"showing_id": showing_id, "attendee_id": casey_id})
        answer = await client.post(
            f"{MCP}/showings/cancellations",
            json={"showing_id": showing_id, "effect": whole.json()["effect"]},
        )
        unknown = await client.post(
            f"{MCP}/showings/cancellations/preview", json={"showing_id": 999999}
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == mcp_routes.EFFECT_CHANGED
    (showing,), attendees, _ = await rows()
    assert showing.status == "scheduled"
    assert {a.id: a.status for a in attendees}[alex_id] == "booked"
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "unknown_showing")


# ---------------------------------------------------------------------------
# The check under the action's own lock
# ---------------------------------------------------------------------------


def stale_once(monkeypatch, name: str, stale):
    """``effects.<name>`` answers ``stale(real answer)`` on its next call, then as it really would.

    Stands in for a change that lands after the route's first comparison and
    before the action takes its lock: only the check under the lock can see it.
    """
    real = getattr(effects, name)
    calls = {"n": 0}

    async def first_stale(*args, **kwargs):
        calls["n"] += 1
        answer = await real(*args, **kwargs)
        return stale(answer) if calls["n"] == 1 else answer

    monkeypatch.setattr(effects, name, first_stale)


@pytest.mark.asyncio
async def test_a_booking_that_changed_after_the_first_comparison_is_refused_under_the_lock(
    world, monkeypatch
):
    stale_once(
        monkeypatch, "preview_booking", lambda real: dataclasses.replace(real, sentence="STALE")
    )
    async with world.client(mcp_app(world)) as client:
        answer = await client.post(f"{MCP}/showings/bookings", json={**casey(), "effect": "STALE"})

    assert answer.status_code == 409, answer.text
    detail = answer.json()["detail"]
    assert detail["code"] == mcp_routes.EFFECT_CHANGED
    assert detail["effect"].startswith("Books Casey Morgan into a new showing")
    assert (await rows())[1] == []
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_a_cancellation_that_changed_after_the_first_comparison_is_refused_under_the_lock(
    world, monkeypatch
):
    showing_id, _, _ = await two_booked(world)
    sent_before = len(world.texts.sent)
    stale_once(monkeypatch, "preview_cancellation", lambda real: "STALE")
    async with world.client(mcp_app(world)) as client:
        answer = await client.post(
            f"{MCP}/showings/cancellations", json={"showing_id": showing_id, "effect": "STALE"}
        )

    assert answer.status_code == 409, answer.text
    detail = answer.json()["detail"]
    assert detail["code"] == mcp_routes.EFFECT_CHANGED
    assert detail["effect"].startswith("Cancels the showing")
    (showing,), attendees, _ = await rows()
    assert showing.status == "scheduled"
    assert {a.status for a in attendees} == {"booked"}
    assert len(world.texts.sent) == sent_before
