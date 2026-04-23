"""
Tests for the FastMCP Apps-based approval flow.

Covers:
  * Backend _confirm_send_sms / _confirm_send_email approve/reject/unknown-id paths
    (with send_*_core mocked — we test the wiring, not the upstream API).
  * Structural isolation: hidden @app.tool() tools are absent from tools/list AND
    not callable via tools/call over the protocol.
  * Non-Apps client rejection: entry-point raises ToolError before any state
    mutation (pending dict stays empty).

NOT covered (requires a real MCP Apps host):
  * PrefabApp rendering fidelity in Claude Desktop etc.
  * Button-click → CallTool → _confirm_* round-trip over the wire from an
    Apps-capable client.

Run:
    pytest api/src/tests/test_sernia_mcp_approvals.py -v
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from api.src.sernia_mcp.tools import approvals
from api.src.tool_core.types import EmailSendResult, SmsResult

pytestmark = pytest.mark.asyncio


# -------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _clear_pending():
    """Each test starts with an empty pending dict."""
    approvals._PENDING.clear()
    yield
    approvals._PENDING.clear()


@pytest_asyncio.fixture
async def mcp_client():
    """In-memory fastmcp server + client with the approvals provider mounted.

    Uses Client(FastMCP(...)) so the whole roundtrip runs in-process without
    HTTP — fast and deterministic for structural assertions.
    """
    server = FastMCP("test_sernia_mcp")
    server.add_provider(approvals.approvals_app)
    async with Client(server) as c:
        yield c


# ---------------------------------------------------------- backend confirm_*

class TestConfirmSendSms:
    async def test_reject_consumes_pending_without_sending(self):
        approvals._PENDING["pid"] = {
            "type": "sms",
            "to_phone": "+15551230001",
            "message": "hi",
            "contact_name": "Alice",
            "is_internal": True,
            "created_at": time.time(),
        }
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()
        ) as send:
            out = await approvals._confirm_send_sms(pending_id="pid", decision="reject")
        assert "Cancelled" in out
        assert "pid" not in approvals._PENDING
        send.assert_not_called()

    async def test_unknown_id_is_graceful_and_no_send(self):
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()
        ) as send:
            out = await approvals._confirm_send_sms(
                pending_id="nope", decision="approve"
            )
        assert "Unknown" in out
        send.assert_not_called()

    async def test_approve_fires_send_exactly_once(self):
        approvals._PENDING["pid"] = {
            "type": "sms",
            "to_phone": "+15551230002",
            "message": "hello",
            "contact_name": "Bob",
            "is_internal": False,
            "created_at": time.time(),
        }
        fake = SmsResult(
            to_phone="+15551230002",
            contact_name="Bob",
            line_name="Sernia Team",
            parts_sent=1,
            message_chars=5,
        )
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_sms_core",
            new=AsyncMock(return_value=fake),
        ) as send:
            out = await approvals._confirm_send_sms(
                pending_id="pid", decision="approve"
            )
        assert send.await_count == 1
        send.assert_awaited_with("+15551230002", "hello")
        assert "SMS sent to Bob" in out
        assert "pid" not in approvals._PENDING


class TestConfirmSendEmail:
    async def test_reject_consumes_pending_without_sending(self):
        approvals._PENDING["eid"] = {
            "type": "email",
            "to": ["a@b.com"],
            "subject": "hi",
            "body": "x",
            "all_internal": False,
            "created_at": time.time(),
        }
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_email_core", new=AsyncMock()
        ) as send:
            out = await approvals._confirm_send_email(
                pending_id="eid", decision="reject"
            )
        assert "Cancelled email" in out
        assert "eid" not in approvals._PENDING
        send.assert_not_called()

    async def test_approve_fires_send_exactly_once(self):
        approvals._PENDING["eid"] = {
            "type": "email",
            "to": ["ok@serniacapital.com"],
            "subject": "ping",
            "body": "b",
            "all_internal": True,
            "created_at": time.time(),
        }
        fake = EmailSendResult(
            to=["ok@serniacapital.com"],
            subject="ping",
            from_address="emilio@serniacapital.com",
            message_id="MID123",
        )
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_email_core",
            new=AsyncMock(return_value=fake),
        ) as send:
            out = await approvals._confirm_send_email(
                pending_id="eid", decision="approve"
            )
        assert send.await_count == 1
        assert "MID123" in out


# ----------------------------------------- structural isolation (over protocol)

class TestHiddenToolEnforcement:
    """The hidden @app.tool() tools must be structurally uncallable by the model.

    These assertions depend on FastMCP's semantics around
    visibility=["app"] — if those change, destructive-write enforcement
    regresses silently, so this is the load-bearing regression guard.
    """

    async def test_entry_points_visible_backend_tools_hidden(self, mcp_client):
        names = [t.name for t in await mcp_client.list_tools()]
        assert "quo_send_sms" in names
        assert "google_send_email" in names
        assert "_confirm_send_sms" not in names
        assert "_confirm_send_email" not in names

    async def test_confirm_send_sms_not_callable_directly(self, mcp_client):
        with pytest.raises(ToolError, match="Unknown tool"):
            await mcp_client.call_tool(
                "_confirm_send_sms",
                {"pending_id": "fake", "decision": "approve"},
            )

    async def test_confirm_send_email_not_callable_directly(self, mcp_client):
        with pytest.raises(ToolError, match="Unknown tool"):
            await mcp_client.call_tool(
                "_confirm_send_email",
                {"pending_id": "fake", "decision": "approve"},
            )


# ----------------------------------------------- non-Apps client is fail-closed

class TestEntryPointQueuesPrefab:
    """Entry-point tool queues the pending row and returns a PrefabApp.

    There is no capability check — any client can call the tool, but only
    clients that can render PrefabApps can reach the hidden _confirm_send_*
    tools to complete the send. Non-Apps clients get a PrefabApp payload
    they can't render, but no send happens.
    """

    async def test_quo_send_sms_queues_and_returns_prefab(
        self, mcp_client, monkeypatch
    ):
        from api.src.tool_core.types import SmsRouting

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

        result = await mcp_client.call_tool(
            "quo_send_sms", {"to_phone": "+15551230000", "message": "hello"}
        )

        assert len(approvals._PENDING) == 1
        rec = next(iter(approvals._PENDING.values()))
        assert rec["type"] == "sms"
        assert rec["to_phone"] == "+15551230000"
        assert rec["message"] == "hello"
        assert rec["contact_name"] == "Alice"
        assert rec["is_internal"] is True
        assert "created_at" in rec

        sc = result.structured_content
        assert sc and "$prefab" in sc and "view" in sc

    async def test_google_send_email_queues_and_returns_prefab(
        self, mcp_client
    ):
        result = await mcp_client.call_tool(
            "google_send_email",
            {"to": ["ok@serniacapital.com"], "subject": "s", "body": "b"},
        )
        assert len(approvals._PENDING) == 1
        rec = next(iter(approvals._PENDING.values()))
        assert rec["type"] == "email"
        assert rec["all_internal"] is True
        sc = result.structured_content
        assert sc and "$prefab" in sc


class TestPendingTTL:
    """Pending rows older than TTL must be rejected even if the confirm
    tool is invoked with a valid id."""

    async def test_expired_pending_rejected(self, monkeypatch):
        approvals._PENDING["old"] = {
            "type": "sms",
            "to_phone": "+15550001",
            "message": "stale",
            "contact_name": "Old",
            "is_internal": True,
            "created_at": 0.0,  # epoch — definitely > TTL old
        }
        with patch(
            "api.src.sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()
        ) as send:
            out = await approvals._confirm_send_sms(
                pending_id="old", decision="approve"
            )
        assert "expired" in out
        send.assert_not_called()
