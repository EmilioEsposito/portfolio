"""Showing tools: a read, and booking and cancelling behind an Approve card.

The API is replaced by a fake with the same paths and answers as
``api/src/showing_bookings/mcp_routes.py``; nothing leaves the process. What
every card shares (buttons call listed tools, toasts, plain clients refused)
is in ``test_approvals.py``. All names and numbers are invented.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.apps.config import UI_EXTENSION_ID
from fastmcp.exceptions import ToolError
from mcp.client import advertise

from sernia_mcp.core import showing_bookings as core
from sernia_mcp.core.errors import ExternalServiceError, ValidationError
from sernia_mcp.tools import approvals
from sernia_mcp.tools import showing_bookings as tools

BOOK = {"unit": "12-A", "start": "2027-04-14T16:00:00-04:00", "name": "Casey Morgan"}
BOOKS = "Books Casey Morgan into a new showing of 12 Example St (unit 12-A) on Wed, Apr 14."
CANCELS = "Cancels the showing of 12 Example St (unit 12-A) on Wed, Apr 14."


class FakeApi:
    """The API's MCP showing routes, in memory. ``effect`` is what a preview says now."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.booking_effect = BOOKS
        self.cancel_effect = CANCELS
        self.refuse: core.RefusedError | None = None

    async def __call__(self, method, path, token, *, json=None, params=None):
        assert token == "oat_test"
        self.calls.append((method, path, json if json is not None else params))
        if self.refuse:
            raise self.refuse
        if (method, path) == ("GET", "/showings"):
            return {"units": [{"unit": "12-A", "booking_link": "https://example.test/12-A"}]}
        if (method, path) == ("POST", "/showings/bookings/preview"):
            return {"effect": self.booking_effect}
        if (method, path) == ("POST", "/showings/cancellations/preview"):
            return {"effect": self.cancel_effect}
        if (method, path) == ("POST", "/showings/bookings"):
            if json["effect"] != self.booking_effect:
                raise core.EffectChangedError("Changed.", self.booking_effect)
            return {"result": "Booked Casey Morgan at 12-A.", "booking": {}}
        if (method, path) == ("POST", "/showings/cancellations"):
            if json["effect"] != self.cancel_effect:
                raise core.EffectChangedError("Changed.", self.cancel_effect)
            return {"result": "Cancelled. Showing 7 is now cancelled.", "cancellation": {}}
        raise AssertionError(f"unexpected {method} {path}")

    def saves(self) -> list[str]:
        return [path for method, path, _ in self.calls if not path.endswith("/preview")]


@pytest.fixture(autouse=True)
def _clear_pending():
    approvals._PENDING.clear()
    yield
    approvals._PENDING.clear()


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(core, "_request", fake)
    monkeypatch.setattr(tools, "_caller", lambda: "oat_test")
    return fake


def _server() -> FastMCP:
    server = FastMCP("test_showing_bookings")
    server.add_tool(tools.showings_read)
    server.add_provider(approvals.approvals_app)
    return server


@pytest_asyncio.fixture
async def client():
    """A client that draws cards, as Claude does."""
    async with Client(_server(), extensions=[advertise(UI_EXTENSION_ID, {})]) as connected:
        yield connected


@pytest_asyncio.fixture
async def plain_client():
    async with Client(_server()) as connected:
        yield connected


def _texts(value) -> list[str]:
    if isinstance(value, dict):
        found = [value["content"]] if value.get("type") == "Text" else []
        return found + [text for child in value.values() for text in _texts(child)]
    if isinstance(value, list):
        return [text for child in value for text in _texts(child)]
    return []


def _click(card, decision: str) -> tuple[str, dict]:
    def actions(value):
        if isinstance(value, dict):
            if value.get("action") == "toolCall":
                yield value
            for child in value.values():
                yield from actions(child)
        elif isinstance(value, list):
            for child in value:
                yield from actions(child)

    action = next(
        a for a in actions(card.structured_content) if a["arguments"]["decision"] == decision
    )
    return action["tool"], action["arguments"]


# ---------------------------------------------------------------------- reading


@pytest.mark.asyncio
async def test_the_read_passes_the_unit_and_thread_and_answers_the_api_s_document(api, client):
    answer = await client.call_tool(
        "showings_read", {"unit": "12-A", "zillow_thread_id": " thread-1 "}
    )

    assert json.loads(answer.data)["units"][0]["booking_link"] == "https://example.test/12-A"
    assert api.calls == [("GET", "/showings", {"unit": "12-A", "zillow_thread_id": "thread-1"})]


@pytest.mark.asyncio
async def test_the_read_works_in_a_client_without_cards(api, plain_client):
    answer = await plain_client.call_tool("showings_read", {})

    assert "booking_link" in answer.data
    assert api.calls == [("GET", "/showings", None)]


# ---------------------------------------------------------------------- booking


@pytest.mark.asyncio
async def test_the_booking_card_shows_the_api_s_paragraph_and_books_nothing(api, client):
    card = await client.call_tool(
        "showing_book", {**BOOK, "texts_accepted": True, "zillow_thread_id": " thread-1 "}
    )

    assert BOOKS in _texts(card.structured_content)
    assert api.saves() == []
    (_, path, sent) = api.calls[0]
    assert path == "/showings/bookings/preview"
    assert sent["texts_accepted"] is True and sent["zillow_thread_id"] == "thread-1"


@pytest.mark.asyncio
async def test_approve_books_once_with_the_paragraph_the_person_saw(api, client):
    card = await client.call_tool("showing_book", BOOK)
    tool, arguments = _click(card, "approve")

    said = await client.call_tool(tool, arguments)

    assert said.data == "Booked Casey Morgan at 12-A."
    (_, path, sent) = api.calls[-1]
    assert path == "/showings/bookings" and sent["effect"] == BOOKS
    with pytest.raises(ToolError, match="already decided"):
        await client.call_tool(tool, arguments)
    assert api.saves() == ["/showings/bookings"]


@pytest.mark.asyncio
async def test_reject_books_nothing(api, client):
    card = await client.call_tool("showing_book", BOOK)

    said = await client.call_tool(*_click(card, "reject"))

    assert said.data == "Nothing was booked."
    assert api.saves() == []


@pytest.mark.asyncio
async def test_a_booking_whose_effect_changed_is_not_made_and_says_what_it_would_do_now(
    api, client
):
    card = await client.call_tool("showing_book", BOOK)
    api.booking_effect = "Books Casey Morgan into the showing already booked."

    with pytest.raises(ToolError, match=r"changed after this card was shown.*already booked"):
        await client.call_tool(*_click(card, "approve"))


@pytest.mark.asyncio
async def test_a_refused_preview_queues_nothing(api, client):
    api.refuse = core.RefusedError("Check the marked fields. (name: Enter the caller's name.)")

    with pytest.raises(ToolError, match=r"Not booked. Check the marked fields"):
        await client.call_tool("showing_book", {**BOOK, "name": " "})
    assert approvals._PENDING == {}


@pytest.mark.asyncio
async def test_a_client_without_cards_is_refused_before_the_api_is_asked(api, plain_client):
    with pytest.raises(ToolError, match="cannot show one"):
        await plain_client.call_tool("showing_book", BOOK)
    with pytest.raises(ToolError, match="cannot show one"):
        await plain_client.call_tool("showing_cancel", {"showing_id": 7})
    assert api.calls == [] and approvals._PENDING == {}


# ------------------------------------------------------------------- cancelling


@pytest.mark.asyncio
@pytest.mark.parametrize("attendee_id", [None, 31])
async def test_approve_cancels_once_with_the_paragraph_the_person_saw(api, client, attendee_id):
    card = await client.call_tool("showing_cancel", {"showing_id": 7, "attendee_id": attendee_id})
    texts = _texts(card.structured_content)
    assert CANCELS in texts

    said = await client.call_tool(*_click(card, "approve"))

    assert said.data.startswith("Cancelled.")
    assert api.calls[-1] == (
        "POST",
        "/showings/cancellations",
        {"showing_id": 7, "attendee_id": attendee_id, "effect": CANCELS},
    )


@pytest.mark.asyncio
async def test_the_cancel_buttons_say_what_they_do(api, client):
    whole = await client.call_tool("showing_cancel", {"showing_id": 7})
    one = await client.call_tool("showing_cancel", {"showing_id": 7, "attendee_id": 31})

    def labels(card) -> list[str]:
        def find(value):
            if isinstance(value, dict):
                if value.get("type") == "Button":
                    yield value["label"]
                for child in value.values():
                    yield from find(child)
            elif isinstance(value, list):
                for child in value:
                    yield from find(child)

        return list(find(card.structured_content))

    assert labels(whole) == ["Cancel the showing", "Keep it"]
    assert labels(one) == ["Remove them", "Keep it"]


@pytest.mark.asyncio
async def test_keep_it_cancels_nothing(api, client):
    card = await client.call_tool("showing_cancel", {"showing_id": 7})

    said = await client.call_tool(*_click(card, "reject"))

    assert said.data == "Nothing was cancelled."
    assert api.saves() == []


@pytest.mark.asyncio
async def test_a_cancellation_whose_effect_changed_is_not_made(api, client):
    card = await client.call_tool("showing_cancel", {"showing_id": 7})
    api.cancel_effect = "Cancels the showing and tells only Alex."

    with pytest.raises(ToolError, match=r"Not cancelled.*tells only Alex"):
        await client.call_tool(*_click(card, "approve"))


# --------------------------------------------------------- the client underneath


class TestRequest:
    """``core._request`` against the API's answers, through a mock transport."""

    @pytest.fixture
    def answer(self, monkeypatch):
        state: dict[str, httpx.Response] = {}
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return state["response"]

        real = httpx.AsyncClient
        monkeypatch.setattr(
            core.httpx, "AsyncClient", lambda: real(transport=httpx.MockTransport(respond))
        )
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "https://api.test/")

        def set_answer(status: int, body: Any) -> list[httpx.Request]:
            state["response"] = httpx.Response(status, json=body)
            return seen

        return set_answer

    @pytest.mark.asyncio
    async def test_it_calls_the_mcp_routes_as_the_user(self, answer):
        seen = answer(200, {"effect": "Books Casey."})

        assert await core.preview_cancellation("oat_test", 7, None) == "Books Casey."
        (request,) = seen
        assert (
            str(request.url)
            == "https://api.test/api/showing-bookings/mcp/showings/cancellations/preview"
        )
        assert request.headers["authorization"] == "Bearer oat_test"

    @pytest.mark.asyncio
    async def test_a_changed_effect_comes_back_with_the_new_paragraph(self, answer):
        answer(409, {"detail": {"code": "effect_changed", "message": "Changed.", "effect": "New."}})

        with pytest.raises(core.EffectChangedError) as changed:
            await core.cancel("oat_test", 7, None, "Old.")
        assert changed.value.effect == "New."

    @pytest.mark.asyncio
    async def test_a_refusal_carries_its_reason_and_fields(self, answer):
        answer(
            422,
            {
                "detail": {
                    "code": "invalid",
                    "message": "Check the marked fields.",
                    "fields": {"name": "Enter the caller's name."},
                }
            },
        )

        with pytest.raises(core.RefusedError, match=r"marked fields. \(name: Enter") as refused:
            await core.preview_booking("oat_test", BOOK)
        assert refused.value.code == "invalid"

    @pytest.mark.asyncio
    async def test_an_api_without_these_routes_says_so(self, answer):
        answer(404, {"detail": "Not Found"})

        with pytest.raises(ExternalServiceError, match="no showings routes"):
            await core.read_showings("oat_test")

    @pytest.mark.asyncio
    async def test_a_user_the_api_refuses_is_not_a_refusal_of_the_booking(self, answer):
        answer(401, {"detail": "This OAuth access token is not accepted here."})

        with pytest.raises(ExternalServiceError, match="did not accept this user"):
            await core.read_showings("oat_test")

    @pytest.mark.asyncio
    async def test_without_an_api_address_the_tools_are_off(self, monkeypatch):
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "")

        with pytest.raises(ExternalServiceError, match="SERNIA_MCP_API_BASE_URL"):
            await core.read_showings("oat_test")

    @pytest.mark.asyncio
    async def test_without_a_token_nothing_is_sent(self, monkeypatch):
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "https://api.test")

        with pytest.raises(ValidationError, match="signed-in user"):
            await core.read_showings("")
