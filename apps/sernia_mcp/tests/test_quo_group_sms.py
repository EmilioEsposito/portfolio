"""Tests for group SMS support (Quo multi-recipient messages).

The Quo API's POST /v1/messages accepts up to GROUP_SMS_MAX_RECIPIENTS (10)
numbers in ``to``, landing the message in one shared group thread (verified
live 2026-08-13).

Covers:

  * ``resolve_group_sms_routing_core`` — line selection, mixed-group
    flagging, size gates, unknown-contact blocking, deterministic
    cross-unit tenant isolation, dedup.
  * ``send_group_sms_core`` — payload shape (all recipients in one ``to``),
    auto-split fan-out.
  * Approval flow — ``quo_send_sms`` with a list queues a group pending row
    and returns a PrefabApp; ``_confirm_send_sms`` routes to the group send.

All sends are mocked — no real SMS leaves these tests.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sernia_mcp.config import (
    GROUP_SMS_MAX_RECIPIENTS,
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
)
from sernia_mcp.core.errors import NotFoundError, ValidationError
from sernia_mcp.core.quo import send_sms as send_sms_mod
from sernia_mcp.core.quo.send_sms import (
    resolve_group_sms_routing_core,
    send_group_sms_core,
)
from sernia_mcp.core.types import GroupSmsRouting, SmsResult
from sernia_mcp.tools import approvals

INTERNAL_A = "+14125550001"
INTERNAL_B = "+14125550002"
EXTERNAL_A = "+14125550101"
TENANT_320_02_A = "+14125550201"
TENANT_320_02_B = "+14125550202"
TENANT_320_03 = "+14125550203"
UNKNOWN = "+19999999999"


def _contact(name: str, phone: str, company: str | None = None, unit: tuple | None = None) -> dict:
    c: dict = {
        "defaultFields": {
            "firstName": name,
            "lastName": "Test",
            "company": company,
            "phoneNumbers": [{"value": phone}],
        }
    }
    if unit:
        c["customFields"] = [
            {"name": "Property", "value": unit[0]},
            {"name": "Unit #", "value": unit[1]},
        ]
    return c


_CONTACTS = {
    INTERNAL_A: _contact("Emilio", INTERNAL_A, company=QUO_INTERNAL_COMPANY),
    INTERNAL_B: _contact("Anna", INTERNAL_B, company=QUO_INTERNAL_COMPANY),
    EXTERNAL_A: _contact("Sana", EXTERNAL_A, company="Test2"),
    TENANT_320_02_A: _contact("Aidan", TENANT_320_02_A, unit=("320", "02")),
    TENANT_320_02_B: _contact("Adeline", TENANT_320_02_B, unit=("320", "02")),
    TENANT_320_03: _contact("Hailey", TENANT_320_03, unit=("320", "03")),
}


class _FakeClientCM:
    """Async-context-manager stand-in for build_quo_client()."""

    def __init__(self, client=None):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def _mock_quo(monkeypatch):
    async def fake_find(phone: str, client) -> dict | None:
        return _CONTACTS.get(phone)

    monkeypatch.setattr(send_sms_mod, "find_contact_by_phone", fake_find)
    monkeypatch.setattr(send_sms_mod, "build_quo_client", lambda: _FakeClientCM())


def _capturing_client(captured: list[dict], status_code: int = 202) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(status_code, json={"data": {"id": "ACtest"}})

    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        transport=httpx.MockTransport(handler),
    )


# ------------------------------------------------- resolve_group_sms_routing_core


class TestResolveGroupRouting:
    @pytest.mark.asyncio
    async def test_all_internal_uses_ai_line(self):
        routing = await resolve_group_sms_routing_core([INTERNAL_A, INTERNAL_B])
        assert routing.all_internal is True
        assert routing.is_mixed is False
        assert routing.from_phone_id == QUO_SERNIA_AI_PHONE_ID
        assert routing.line_name == "Sernia AI"
        assert routing.recipient_names == ["Emilio Test", "Anna Test"]

    @pytest.mark.asyncio
    async def test_mixed_group_flagged_uses_shared_line(self):
        routing = await resolve_group_sms_routing_core([INTERNAL_A, EXTERNAL_A])
        assert routing.all_internal is False
        assert routing.is_mixed is True
        assert routing.from_phone_id == QUO_SHARED_EXTERNAL_PHONE_ID

    @pytest.mark.asyncio
    async def test_cross_unit_tenants_blocked(self):
        """Deterministic unit isolation — tenants from different units can
        never share a group thread; blocks before any approval card."""
        with pytest.raises(ValidationError, match="different units"):
            await resolve_group_sms_routing_core([TENANT_320_02_A, TENANT_320_03])

    @pytest.mark.asyncio
    async def test_same_unit_roommates_allowed(self):
        routing = await resolve_group_sms_routing_core([TENANT_320_02_A, TENANT_320_02_B])
        assert routing.from_phone_id == QUO_SHARED_EXTERNAL_PHONE_ID

    @pytest.mark.asyncio
    async def test_unknown_contact_blocked(self):
        with pytest.raises(NotFoundError, match="not a Quo contact"):
            await resolve_group_sms_routing_core([INTERNAL_A, UNKNOWN])

    @pytest.mark.asyncio
    async def test_size_gates(self):
        with pytest.raises(ValidationError, match="at least 2"):
            await resolve_group_sms_routing_core([INTERNAL_A, INTERNAL_A])
        too_many = [f"+1412555{i:04d}" for i in range(GROUP_SMS_MAX_RECIPIENTS + 1)]
        with pytest.raises(ValidationError, match="at most"):
            await resolve_group_sms_routing_core(too_many)

    @pytest.mark.asyncio
    async def test_dedup_preserves_order(self):
        routing = await resolve_group_sms_routing_core([INTERNAL_B, INTERNAL_A, INTERNAL_B])
        assert routing.phones == [INTERNAL_B, INTERNAL_A]


# ------------------------------------------------------------ send_group_sms_core


class TestSendGroupSms:
    @pytest.mark.asyncio
    async def test_all_recipients_in_one_payload(self, monkeypatch):
        captured: list[dict] = []
        monkeypatch.setattr(
            send_sms_mod,
            "build_quo_client",
            lambda: _FakeClientCM(_capturing_client(captured)),
        )
        result = await send_group_sms_core([INTERNAL_A, INTERNAL_B], "hello group")
        assert len(captured) == 1
        assert captured[0]["to"] == [INTERNAL_A, INTERNAL_B]
        assert captured[0]["from"] == QUO_SERNIA_AI_PHONE_ID
        assert result.parts_sent == 1
        assert result.contact_name == "Emilio Test, Anna Test"

    @pytest.mark.asyncio
    async def test_auto_split_sends_every_chunk_to_full_group(self, monkeypatch):
        captured: list[dict] = []
        monkeypatch.setattr(
            send_sms_mod,
            "build_quo_client",
            lambda: _FakeClientCM(_capturing_client(captured)),
        )
        long_msg = "A sentence for splitting purposes. " * 20  # > 500 chars
        result = await send_group_sms_core([INTERNAL_A, INTERNAL_B], long_msg)
        assert result.parts_sent >= 2
        for payload in captured:
            assert payload["to"] == [INTERNAL_A, INTERNAL_B]


# ----------------------------------------------------------------- approval flow


class TestGroupApprovalFlow:
    @pytest.fixture(autouse=True)
    def _clear_pending(self):
        approvals._PENDING.clear()
        yield
        approvals._PENDING.clear()

    @pytest.mark.asyncio
    async def test_group_send_queues_pending_row(self):
        fake_routing = GroupSmsRouting(
            phones=[INTERNAL_A, EXTERNAL_A],
            recipient_names=["Emilio Test", "Sana Test"],
            all_internal=False,
            is_mixed=True,
            from_phone_id=QUO_SHARED_EXTERNAL_PHONE_ID,
            line_name="Sernia Capital Team",
        )
        with patch(
            "sernia_mcp.tools.approvals.resolve_group_sms_routing_core",
            new=AsyncMock(return_value=fake_routing),
        ):
            await approvals.quo_send_sms(to_phone=[INTERNAL_A, EXTERNAL_A], message="group hello")
        assert len(approvals._PENDING) == 1
        rec = next(iter(approvals._PENDING.values()))
        assert rec["group"] is True
        assert rec["to_phones"] == [INTERNAL_A, EXTERNAL_A]
        assert rec["message"] == "group hello"

    @pytest.mark.asyncio
    async def test_confirm_approve_routes_to_group_send(self):
        approvals._PENDING["gid"] = {
            "type": "sms",
            "group": True,
            "to_phones": [INTERNAL_A, EXTERNAL_A],
            "message": "group hello",
            "contact_name": "Emilio Test, Sana Test",
            "created_at": time.time(),
        }
        fake = SmsResult(
            to_phone=f"{INTERNAL_A}, {EXTERNAL_A}",
            contact_name="Emilio Test, Sana Test",
            line_name="Sernia Capital Team",
            parts_sent=1,
            message_chars=11,
        )
        with (
            patch(
                "sernia_mcp.tools.approvals.send_group_sms_core",
                new=AsyncMock(return_value=fake),
            ) as group_send,
            patch("sernia_mcp.tools.approvals.send_sms_core", new=AsyncMock()) as single_send,
        ):
            out = await approvals._confirm_send_sms(pending_id="gid", decision="approve")
        group_send.assert_awaited_once_with([INTERNAL_A, EXTERNAL_A], "group hello")
        single_send.assert_not_called()
        assert "Group SMS (one shared thread) sent to" in out

    @pytest.mark.asyncio
    async def test_confirm_reject_group_does_not_send(self):
        approvals._PENDING["gid"] = {
            "type": "sms",
            "group": True,
            "to_phones": [INTERNAL_A, EXTERNAL_A],
            "message": "group hello",
            "contact_name": "Emilio Test, Sana Test",
            "created_at": time.time(),
        }
        with patch("sernia_mcp.tools.approvals.send_group_sms_core", new=AsyncMock()) as group_send:
            out = await approvals._confirm_send_sms(pending_id="gid", decision="reject")
        group_send.assert_not_called()
        assert "Cancelled" in out

    @pytest.mark.asyncio
    async def test_single_element_list_treated_as_1to1(self):
        """A 1-element list unwraps to the single-recipient path."""
        from sernia_mcp.core.types import SmsRouting

        fake_routing = SmsRouting(
            contact_id="c1",
            contact_name="Emilio Test",
            is_internal=True,
            from_phone_id=QUO_SERNIA_AI_PHONE_ID,
            line_name="Sernia AI",
        )
        with patch(
            "sernia_mcp.tools.approvals.resolve_sms_routing_core",
            new=AsyncMock(return_value=fake_routing),
        ):
            await approvals.quo_send_sms(to_phone=[INTERNAL_A], message="hi")
        rec = next(iter(approvals._PENDING.values()))
        assert "group" not in rec
        assert rec["to_phone"] == INTERNAL_A
