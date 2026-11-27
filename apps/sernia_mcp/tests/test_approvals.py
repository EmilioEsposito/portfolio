"""Tests for the approval cards: who can reach the confirm tools, and what a click does.

Two kinds of client are used throughout:

  * a card client declares the MCP Apps extension, as Claude does. It is given
    the confirm tools, so it can forward a card's button press to them;
  * a plain client does not. For it the confirm tools do not exist and the
    entry tools refuse, because nobody could click.

Covers:

  * every button on every card calls a tool the card client was given in
    ``tools/list``. A client forwards a card's call only to a listed tool, so
    this is the check that an Approve click can arrive at all;
  * a click carries out its decision once, and a card says "sent" only when
    the confirm tool returned;
  * the confirm tools raise whenever the decision was not carried out
    (unknown, already decided, expired, another user's, a failed send);
  * a plain client can neither list nor call a confirm tool, by name or by
    hash, and its request is never queued.

NOT covered (needs a real MCP Apps host): how a specific client draws the
card, and whether that client really keeps app-only tools from its model.
Sends are mocked; nothing leaves the process.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.apps.config import UI_EXTENSION_ID
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.addressing import hashed_backend_name
from mcp.client import advertise

from sernia_mcp.core.errors import ExternalServiceError
from sernia_mcp.core.types import EmailSendResult, GroupSmsRouting, SmsResult, SmsRouting
from sernia_mcp.tools import approvals, quo

CONFIRM_TOOLS = {"_confirm_send_sms", "_confirm_send_email", "_confirm_delete_quo_task"}
ENTRY_TOOLS = {"quo_send_sms", "google_send_email", "quo_delete_task"}


@pytest.fixture(autouse=True)
def _clear_pending():
    """Each test starts with nothing waiting."""
    approvals._PENDING.clear()
    yield
    approvals._PENDING.clear()


def _server() -> FastMCP:
    server = FastMCP("test_sernia_mcp")
    server.add_provider(approvals.approvals_app)
    return server


@pytest_asyncio.fixture(params=["auto", "legacy"])
async def card_client(request):
    """A client that declares MCP Apps, in the modern and the legacy protocol."""
    async with Client(
        _server(), mode=request.param, extensions=[advertise(UI_EXTENSION_ID, {})]
    ) as connected:
        yield connected


@pytest_asyncio.fixture(params=["auto", "legacy"])
async def plain_client(request):
    """A client that does not draw cards."""
    async with Client(_server(), mode=request.param) as connected:
        yield connected


def _card_actions(value):
    """The tool calls a card's buttons make, as the renderer receives them."""
    if isinstance(value, dict):
        if value.get("action") == "toolCall":
            yield value
        for child in value.values():
            yield from _card_actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _card_actions(child)


def _buttons(value):
    if isinstance(value, dict):
        if isinstance(value.get("onClick"), list):
            yield value
        for child in value.values():
            yield from _buttons(child)
    elif isinstance(value, list):
        for child in value:
            yield from _buttons(child)


def _action(card, decision: str) -> dict:
    return next(
        action
        for action in _card_actions(card.structured_content)
        if action["arguments"]["decision"] == decision
    )


ROUTING = SmsRouting(
    contact_id="x",
    contact_name="Alice",
    is_internal=True,
    from_phone_id="y",
    line_name="Sernia AI",
)
SMS_SENT = SmsResult(
    to_phone="+15551230000",
    contact_name="Alice",
    line_name="Sernia AI",
    parts_sent=1,
    message_chars=5,
)
EMAIL_SENT = EmailSendResult(
    to=["ok@serniacapital.com"],
    subject="s",
    from_address="pat@serniacapital.com",
    message_id="TEST_ONLY",
)


@pytest.fixture
def upstream(monkeypatch):
    """Every upstream the cards touch, mocked. Returns the send mocks by kind."""
    monkeypatch.setattr(approvals, "resolve_sms_routing_core", AsyncMock(return_value=ROUTING))
    monkeypatch.setattr(
        approvals,
        "resolve_group_sms_routing_core",
        AsyncMock(
            return_value=GroupSmsRouting(
                phones=["+15551230000", "+15551230001"],
                recipient_names=["Alice", "Bob"],
                all_internal=True,
                from_phone_id="y",
                line_name="Sernia AI",
            )
        ),
    )
    sends = {
        "sms": AsyncMock(return_value=SMS_SENT),
        "group": AsyncMock(return_value=SMS_SENT),
        "email": AsyncMock(return_value=EMAIL_SENT),
    }
    monkeypatch.setattr(approvals, "send_sms_core", sends["sms"])
    monkeypatch.setattr(approvals, "send_group_sms_core", sends["group"])
    monkeypatch.setattr(approvals, "send_email_core", sends["email"])

    task_methods: list[str] = []

    def quo_api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/tasks/TKtest"
        task_methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"title": "Call back"}})
        return httpx.Response(204)

    monkeypatch.setattr(
        quo,
        "build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://quo.test", transport=httpx.MockTransport(quo_api)
        ),
    )
    sends["task_methods"] = task_methods
    return sends


CARDS = {
    "sms": ("quo_send_sms", {"to_phone": "+15551230000", "message": "hello"}),
    "group": (
        "quo_send_sms",
        {"to_phone": ["+15551230000", "+15551230001"], "message": "hello"},
    ),
    "email": (
        "google_send_email",
        {"to": ["ok@serniacapital.com"], "subject": "s", "body": "b"},
    ),
    "task": ("quo_delete_task", {"task_id": "TKtest"}),
}


# ------------------------------------------------- a click can reach the server


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(CARDS))
async def test_every_button_calls_a_tool_the_client_was_given(card_client, upstream, kind):
    """A client forwards a card's call only to a tool from ``tools/list``.

    This failed from the FastMCP 4 upgrade until the fix: the buttons called
    ``<hash>_confirm_...``, which no client was ever given, so no Approve
    click reached the server.
    """
    listed = {tool.name: tool for tool in await card_client.list_tools()}

    card = await card_client.call_tool(*CARDS[kind])

    called = {action["tool"] for action in _card_actions(card.structured_content)}
    assert len(called) == 1 and called <= CONFIRM_TOOLS
    (name,) = called
    assert name in listed
    # Marked for the card only, which is what tells the client to keep it from its model.
    assert listed[name].meta["ui"]["visibility"] == ["app"]


@pytest.mark.asyncio
async def test_a_card_client_is_given_the_entry_and_the_confirm_tools(card_client):
    names = {tool.name for tool in await card_client.list_tools()}

    assert ENTRY_TOOLS | CONFIRM_TOOLS <= names


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(CARDS))
async def test_a_card_says_done_only_when_the_confirm_tool_returned(card_client, upstream, kind):
    card = await card_client.call_tool(*CARDS[kind])

    buttons = list(_buttons(card.structured_content))
    assert len(buttons) == 2
    for button in buttons:
        first, call = button["onClick"]
        assert first["action"] == "setState"  # both buttons go dead on the first click
        assert call["action"] == "toolCall"
        assert call["onSuccess"]["action"] == "showToast"
        # The reason comes from the confirm tool, or from the client if the call never arrived.
        assert call["onError"]["variant"] == "error" and "$error" in call["onError"]["message"]
        assert button["disabled"] == "{{ decided }}"


# ----------------------------------------------------- a click decides once


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sms", "group", "email"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_click_carries_out_its_decision_once(card_client, upstream, kind, decision):
    card = await card_client.call_tool(*CARDS[kind])
    upstream[kind].assert_not_awaited()
    action = _action(card, decision)
    pending_id = action["arguments"]["pending_id"]
    assert pending_id in approvals._PENDING

    said = await card_client.call_tool(action["tool"], action["arguments"])

    sent = 1 if decision == "approve" else 0
    assert ("sent" in said.data) if sent else said.data.startswith("Cancelled")
    assert pending_id not in approvals._PENDING
    assert upstream[kind].await_count == sent
    with pytest.raises(ToolError, match="already decided"):
        await card_client.call_tool(action["tool"], action["arguments"])
    assert upstream[kind].await_count == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sms", "email"])
async def test_a_send_that_fails_is_reported_as_failed(card_client, upstream, kind):
    """The confirm tool raises, so the card shows the reason and never "sent"."""
    upstream[kind].side_effect = ExternalServiceError("upstream said no")
    card = await card_client.call_tool(*CARDS[kind])
    action = _action(card, "approve")

    with pytest.raises(ToolError, match="The send failed: upstream said no"):
        await card_client.call_tool(action["tool"], action["arguments"])


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject", "expired", "different_user"])
async def test_quo_task_deletion_card(card_client, upstream, monkeypatch, decision):
    methods = upstream["task_methods"]
    card = await card_client.call_tool(*CARDS["task"])
    assert methods == ["GET"]
    action = _action(card, "reject" if decision == "reject" else "approve")
    args = action["arguments"]
    if decision == "expired":
        approvals._PENDING[args["pending_id"]]["created_at"] -= approvals._PENDING_TTL_SECONDS + 1
        with pytest.raises(ToolError, match="expired"):
            await card_client.call_tool(action["tool"], args)
    elif decision == "different_user":
        monkeypatch.setattr(approvals, "_task_approver", lambda: "other@example.test")
        with pytest.raises(ToolError, match="another user"):
            await card_client.call_tool(action["tool"], args)
        assert methods == ["GET"]
        assert args["pending_id"] in approvals._PENDING  # left for the user who asked
        return
    else:
        await card_client.call_tool(action["tool"], args)
    with pytest.raises(ToolError, match="already decided"):
        await card_client.call_tool(action["tool"], args)
    assert methods == (["GET", "DELETE"] if decision == "approve" else ["GET"])


# ------------------------------------------------ a client that cannot show a card


class TestAPlainClient:
    """For a client that does not draw cards, the confirm tools do not exist."""

    @pytest.mark.asyncio
    async def test_is_given_the_entry_tools_only(self, plain_client):
        names = {tool.name for tool in await plain_client.list_tools()}

        assert ENTRY_TOOLS <= names
        assert not names & CONFIRM_TOOLS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(CONFIRM_TOOLS))
    async def test_cannot_call_a_confirm_tool_by_name_or_by_hash(self, plain_client, name):
        approvals._PENDING["pid"] = {"type": "sms", "created_at": time.time()}
        arguments = {"pending_id": "pid", "decision": "approve"}

        for address in (name, hashed_backend_name(approvals.approvals_app.name, name)):
            with pytest.raises(ToolError, match="Unknown tool"):
                await plain_client.call_tool(address, arguments)

        assert "pid" in approvals._PENDING

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", list(CARDS))
    async def test_is_refused_before_anything_is_queued(self, plain_client, upstream, kind):
        with pytest.raises(ToolError, match="this client cannot show one"):
            await plain_client.call_tool(*CARDS[kind])

        assert approvals._PENDING == {}
        assert upstream["task_methods"] == []


# ----------------------------------------------------------- the confirm tools


class TestConfirmSendSms:
    def pending(self, **more) -> None:
        approvals._PENDING["pid"] = {
            "type": "sms",
            "to_phone": "+15551230001",
            "message": "hi",
            "contact_name": "Alice",
            "is_internal": True,
            "created_at": time.time(),
            **more,
        }

    @pytest.mark.asyncio
    async def test_reject_takes_the_request_without_sending(self):
        self.pending()
        with patch("sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()) as send:
            out = await approvals._confirm_send_sms(pending_id="pid", decision="reject")
        assert "Cancelled" in out
        assert "pid" not in approvals._PENDING
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unknown_request_raises_and_sends_nothing(self):
        with patch("sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()) as send:
            with pytest.raises(ToolError, match="unknown or already decided"):
                await approvals._confirm_send_sms(pending_id="nope", decision="approve")
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_request_of_another_kind_is_left_alone(self):
        self.pending(type="email")
        with pytest.raises(ToolError, match="unknown or already decided"):
            await approvals._confirm_send_sms(pending_id="pid", decision="approve")
        assert "pid" in approvals._PENDING

    @pytest.mark.asyncio
    async def test_approve_sends_exactly_once(self):
        self.pending(to_phone="+15551230002", message="hello", contact_name="Bob")
        fake = SmsResult(
            to_phone="+15551230002",
            contact_name="Bob",
            line_name="Sernia Team",
            parts_sent=1,
            message_chars=5,
        )
        with patch(
            "sernia_mcp.tools.approvals.send_sms_core",
            new=AsyncMock(return_value=fake),
        ) as send:
            out = await approvals._confirm_send_sms(pending_id="pid", decision="approve")
        assert send.await_count == 1
        send.assert_awaited_with("+15551230002", "hello")
        assert "SMS sent to Bob" in out
        assert "pid" not in approvals._PENDING

    @pytest.mark.asyncio
    async def test_an_expired_request_raises_and_sends_nothing(self):
        self.pending(created_at=0.0)
        with patch("sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()) as send:
            with pytest.raises(ToolError, match="expired"):
                await approvals._confirm_send_sms(pending_id="pid", decision="approve")
        send.assert_not_called()
        assert "pid" not in approvals._PENDING


class TestConfirmSendEmail:
    def pending(self, **more) -> None:
        approvals._PENDING["eid"] = {
            "type": "email",
            "to": ["ok@serniacapital.com"],
            "subject": "ping",
            "body": "b",
            "all_internal": True,
            "created_at": time.time(),
            **more,
        }

    @pytest.mark.asyncio
    async def test_reject_takes_the_request_without_sending(self):
        self.pending()
        with patch("sernia_mcp.tools.approvals.send_email_core", new=AsyncMock()) as send:
            out = await approvals._confirm_send_email(pending_id="eid", decision="reject")
        assert "Cancelled email" in out
        assert "eid" not in approvals._PENDING
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_sends_exactly_once(self):
        self.pending()
        fake = EmailSendResult(
            to=["ok@serniacapital.com"],
            subject="ping",
            from_address="pat@serniacapital.com",
            message_id="MID123",
        )
        with patch(
            "sernia_mcp.tools.approvals.send_email_core",
            new=AsyncMock(return_value=fake),
        ) as send:
            out = await approvals._confirm_send_email(pending_id="eid", decision="approve")
        assert send.await_count == 1
        assert "MID123" in out

    @pytest.mark.asyncio
    async def test_an_unknown_request_raises(self):
        with pytest.raises(ToolError, match="unknown or already decided"):
            await approvals._confirm_send_email(pending_id="nope", decision="approve")


# ------------------------------------------------------- what an entry tool queues


class TestEntryToolsQueueARequest:
    @pytest.mark.asyncio
    async def test_quo_send_sms(self, card_client, upstream):
        result = await card_client.call_tool(*CARDS["sms"])

        (request,) = approvals._PENDING.values()
        assert request["type"] == "sms"
        assert request["to_phone"] == "+15551230000"
        assert request["message"] == "hello"
        assert request["contact_name"] == "Alice"
        assert request["is_internal"] is True
        assert "created_at" in request
        card = result.structured_content
        assert card and "$prefab" in card and "view" in card

    @pytest.mark.asyncio
    async def test_a_group_text(self, card_client, upstream):
        await card_client.call_tool(*CARDS["group"])

        (request,) = approvals._PENDING.values()
        assert request["group"] is True
        assert request["to_phones"] == ["+15551230000", "+15551230001"]
        assert request["contact_name"] == "Alice, Bob"

    @pytest.mark.asyncio
    async def test_google_send_email(self, card_client, upstream):
        result = await card_client.call_tool(*CARDS["email"])

        (request,) = approvals._PENDING.values()
        assert request["type"] == "email"
        assert request["all_internal"] is True
        assert result.structured_content and "$prefab" in result.structured_content
