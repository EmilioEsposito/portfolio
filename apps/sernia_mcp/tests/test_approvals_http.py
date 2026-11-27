"""The approval cards through the real app: its auth middleware and stateless HTTP.

``test_approvals.py`` runs in memory, where one session remembers what a
client declared when it connected. Production is stateless: the server learns
that a client draws cards only from what each request carries, which is the
2026-07-28 protocol's per-request envelope. These tests send that envelope as
raw JSON-RPC, the way a real client does, so a break between "declared" and
"listed" shows up here and not first in production.

Auth is off (the suite's local mode) and every upstream is mocked.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from sernia_mcp.core.types import SmsResult, SmsRouting
from sernia_mcp.tools import approvals

PROTOCOL = "2026-07-28"
UI = "io.modelcontextprotocol/ui"
CARDS = {"extensions": {UI: {"mimeTypes": ["text/html;profile=mcp-app"]}}}
CONFIRM_TOOLS = {"_confirm_send_sms", "_confirm_send_email", "_confirm_delete_quo_task"}
SMS = {"name": "quo_send_sms", "arguments": {"to_phone": "+15551230000", "message": "hello"}}


@pytest.fixture(autouse=True)
def _clear_pending():
    approvals._PENDING.clear()
    yield
    approvals._PENDING.clear()


@pytest.fixture
def sent(monkeypatch) -> AsyncMock:
    monkeypatch.setattr(
        approvals,
        "resolve_sms_routing_core",
        AsyncMock(
            return_value=SmsRouting(
                contact_id="x",
                contact_name="Alice",
                is_internal=True,
                from_phone_id="y",
                line_name="Sernia AI",
            )
        ),
    )
    send = AsyncMock(
        return_value=SmsResult(
            to_phone="+15551230000",
            contact_name="Alice",
            line_name="Sernia AI",
            parts_sent=1,
            message_chars=5,
        )
    )
    monkeypatch.setattr(approvals, "send_sms_core", send)
    return send


@asynccontextmanager
async def real_app():
    """The production ASGI app, started and stopped inside the test's own task."""
    from sernia_mcp.app import app as application

    transport = httpx.ASGITransport(app=application)
    async with application.lifespan(application):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def rpc(http: httpx.AsyncClient, method: str, params: dict, *, declares_cards: bool) -> dict:
    """One request, carrying what the client declares, as the 2026-07-28 protocol sends it."""
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": CARDS if declares_cards else {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    response = await http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        body = next(
            json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")
        )
    else:
        body = response.json()
    assert "error" not in body, body
    return body["result"]


def _text(result: dict) -> str:
    return " ".join(part.get("text", "") for part in result.get("content", []))


def _button(result: dict, decision: str) -> dict:
    def walk(node):
        if isinstance(node, dict):
            if node.get("action") == "toolCall":
                yield node
            for child in node.values():
                yield from walk(child)
        elif isinstance(node, list):
            for child in node:
                yield from walk(child)

    return next(
        action
        for action in walk(result["structuredContent"])
        if action["arguments"]["decision"] == decision
    )


@pytest.mark.asyncio
async def test_the_confirm_tools_are_listed_only_for_a_client_that_declares_cards():
    async with real_app() as http:
        card = await rpc(http, "tools/list", {}, declares_cards=True)
        plain = await rpc(http, "tools/list", {}, declares_cards=False)

    listed = {tool["name"]: tool for tool in card["tools"]}
    assert CONFIRM_TOOLS <= set(listed)
    for name in CONFIRM_TOOLS:
        # What tells the client to keep the tool from its model.
        assert listed[name]["_meta"]["ui"]["visibility"] == ["app"]
    assert not CONFIRM_TOOLS & {tool["name"] for tool in plain["tools"]}
    assert "quo_send_sms" in {tool["name"] for tool in plain["tools"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "sends"), [("approve", 1), ("reject", 0)])
async def test_a_click_reaches_the_confirm_tool_and_decides_once(sent, decision, sends):
    async with real_app() as http:
        tools = (await rpc(http, "tools/list", {}, declares_cards=True))["tools"]
        card = await rpc(http, "tools/call", SMS, declares_cards=True)
        assert not card.get("isError"), _text(card)
        click = _button(card, decision)
        # A client forwards a click only to a tool it was given.
        assert click["tool"] in {tool["name"] for tool in tools}
        call = {"name": click["tool"], "arguments": click["arguments"]}

        first = await rpc(http, "tools/call", call, declares_cards=True)
        second = await rpc(http, "tools/call", call, declares_cards=True)

    assert not first.get("isError"), _text(first)
    assert ("sent" in _text(first)) if sends else _text(first).startswith("Cancelled")
    assert second["isError"] and "already decided" in _text(second)
    assert sent.await_count == sends
    assert approvals._PENDING == {}


@pytest.mark.asyncio
async def test_a_click_forwarded_without_the_declaration_still_arrives(sent):
    """The confirm call is not checked against the client again, so the click is not lost."""
    async with real_app() as http:
        card = await rpc(http, "tools/call", SMS, declares_cards=True)
        click = _button(card, "approve")

        said = await rpc(
            http,
            "tools/call",
            {"name": click["tool"], "arguments": click["arguments"]},
            declares_cards=False,
        )

    assert not said.get("isError"), _text(said)
    assert sent.await_count == 1


@pytest.mark.asyncio
async def test_a_plain_client_is_refused_and_nothing_is_queued(sent):
    async with real_app() as http:
        refused = await rpc(http, "tools/call", SMS, declares_cards=False)

    assert refused["isError"] and "this client cannot show one" in _text(refused)
    assert approvals._PENDING == {}
    sent.assert_not_awaited()
