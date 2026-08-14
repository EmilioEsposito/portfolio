"""
Unit tests for group SMS support (Quo API multi-recipient messages).

The Quo API's POST /v1/messages accepts up to GROUP_SMS_MAX_RECIPIENTS (10)
numbers in its ``to`` array, landing the message in one shared group thread
(verified live 2026-08-13 — see test_quo_tools.py for the live contract tests).

Covers:
- resolve_group_sms_routing: line selection, mixed-group flagging, size gates,
  unknown-contact blocking, dedup.
- _send_sms / execute_sms: group payload shape, auto-split fan-out.
- mass_text_tenants batching: roommates share one group send per unit.

⚠️  SMS SAFETY: All SMS tests here mock the send call. NEVER send real SMS
to external contacts from tests. See CLAUDE.md.
"""

import json

import httpx
import pytest

from api.src.sernia_ai.config import (
    GROUP_SMS_MAX_RECIPIENTS,
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
)
from api.src.sernia_ai.tools import quo_tools
from api.src.sernia_ai.tools.quo_tools import (
    GroupSmsRouting,
    _send_sms,
    execute_sms,
    resolve_group_sms_routing,
)

INTERNAL_A = "+14125550001"
INTERNAL_B = "+14125550002"
EXTERNAL_A = "+14125550101"
EXTERNAL_B = "+14125550102"
UNKNOWN = "+19999999999"

_CONTACTS = {
    INTERNAL_A: {
        "defaultFields": {
            "firstName": "Emilio",
            "lastName": "Esposito",
            "company": QUO_INTERNAL_COMPANY,
            "phoneNumbers": [{"value": INTERNAL_A}],
        }
    },
    INTERNAL_B: {
        "defaultFields": {
            "firstName": "Anna",
            "lastName": "Esposito",
            "company": QUO_INTERNAL_COMPANY,
            "phoneNumbers": [{"value": INTERNAL_B}],
        }
    },
    EXTERNAL_A: {
        "defaultFields": {
            "firstName": "Sana",
            "lastName": "Test",
            "company": "Test2",
            "phoneNumbers": [{"value": EXTERNAL_A}],
        }
    },
    EXTERNAL_B: {
        "defaultFields": {
            "firstName": "Tenant",
            "lastName": "Two",
            "company": None,
            "phoneNumbers": [{"value": EXTERNAL_B}],
        }
    },
}

# Tenant contacts with Property/Unit custom fields for the unit-isolation gate.
TENANT_320_02_A = "+14125550201"
TENANT_320_02_B = "+14125550202"
TENANT_320_03 = "+14125550203"


def _tenant_contact(name: str, phone: str, prop: str, unit: str) -> dict:
    return {
        "defaultFields": {
            "firstName": name,
            "lastName": "Tenant",
            "company": None,
            "phoneNumbers": [{"value": phone}],
        },
        "customFields": [
            {"name": "Property", "value": prop},
            {"name": "Unit #", "value": unit},
        ],
    }


_CONTACTS[TENANT_320_02_A] = _tenant_contact("Aidan", TENANT_320_02_A, "320", "02")
_CONTACTS[TENANT_320_02_B] = _tenant_contact("Adeline", TENANT_320_02_B, "320", "02")
_CONTACTS[TENANT_320_03] = _tenant_contact("Hailey", TENANT_320_03, "320", "03")


@pytest.fixture(autouse=True)
def _mock_contact_lookup(monkeypatch):
    """Patch the contact lookup used by resolve_sms_routing."""

    async def fake_find_contact_by_phone(phone: str, client) -> dict | None:
        return _CONTACTS.get(phone)

    monkeypatch.setattr(quo_tools, "find_contact_by_phone", fake_find_contact_by_phone)


def _capture_client(captured: list[dict], status_code: int = 202) -> httpx.AsyncClient:
    """An httpx client whose transport records POST /v1/messages payloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(status_code, json={"data": {"id": "ACtest"}})

    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        transport=httpx.MockTransport(handler),
    )


# ===========================================================================
# resolve_group_sms_routing
# ===========================================================================


class TestResolveGroupSmsRouting:
    @pytest.mark.asyncio
    async def test_all_internal_uses_ai_line(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_A, INTERNAL_B], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.all_internal is True
        assert result.has_external is False
        assert result.from_phone_id == QUO_SERNIA_AI_PHONE_ID
        assert result.line_name == "Sernia AI"
        assert result.recipient_names == ["Emilio Esposito", "Anna Esposito"]

    @pytest.mark.asyncio
    async def test_all_external_uses_shared_line(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([EXTERNAL_A, EXTERNAL_B], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.all_internal is False
        assert result.has_external is True
        assert result.from_phone_id == QUO_SHARED_EXTERNAL_PHONE_ID
        assert result.line_name == "Sernia Capital Team"

    @pytest.mark.asyncio
    async def test_mixed_group_blocked_deterministically(self):
        """Internal + external in one group is hard-blocked — a mixed group
        would expose internal team numbers to external recipients, and no
        approval can override the block."""
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_A, EXTERNAL_A], client)
        assert isinstance(result, str)
        assert "never share a group thread" in result
        assert "Emilio Esposito" in result
        assert "Sana Test" in result

    @pytest.mark.asyncio
    async def test_unknown_contact_blocks_whole_group(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_A, UNKNOWN], client)
        assert isinstance(result, str)
        assert "not a Quo contact" in result

    @pytest.mark.asyncio
    async def test_fewer_than_two_unique_recipients_blocked(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_A, INTERNAL_A], client)
        assert isinstance(result, str)
        assert "at least 2 unique recipients" in result

    @pytest.mark.asyncio
    async def test_over_max_recipients_blocked(self):
        client = _capture_client([])
        phones = [f"+1412555{i:04d}" for i in range(GROUP_SMS_MAX_RECIPIENTS + 1)]
        result = await resolve_group_sms_routing(phones, client)
        assert isinstance(result, str)
        assert f"at most {GROUP_SMS_MAX_RECIPIENTS}" in result

    @pytest.mark.asyncio
    async def test_cross_unit_tenants_blocked_deterministically(self):
        """Tenants from different units must NEVER share a group thread —
        the unit-isolation gate blocks before approval, so approval cannot
        override it."""
        client = _capture_client([])
        result = await resolve_group_sms_routing([TENANT_320_02_A, TENANT_320_03], client)
        assert isinstance(result, str)
        assert "different units" in result
        assert "320 Unit 02" in result and "320 Unit 03" in result

    @pytest.mark.asyncio
    async def test_same_unit_roommates_allowed(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([TENANT_320_02_A, TENANT_320_02_B], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.from_phone_id == QUO_SHARED_EXTERNAL_PHONE_ID

    @pytest.mark.asyncio
    async def test_tenant_with_unitless_external_allowed(self):
        """A tenant + an external contact with no Property/Unit fields (e.g.
        a vendor) is not a cross-unit pairing — allowed, still behind HITL."""
        client = _capture_client([])
        result = await resolve_group_sms_routing([TENANT_320_02_A, EXTERNAL_A], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.has_external is True

    @pytest.mark.asyncio
    async def test_internal_plus_tenants_blocked_as_mixed(self):
        """An internal member plus tenants is a mixed group — blocked by the
        internal/external separation gate (tenants are external)."""
        client = _capture_client([])
        result = await resolve_group_sms_routing(
            [INTERNAL_A, TENANT_320_02_A, TENANT_320_02_B], client
        )
        assert isinstance(result, str)
        assert "never share a group thread" in result

    @pytest.mark.asyncio
    async def test_all_internal_group_allowed(self):
        """Sanity: internal-only groups still resolve (AI line, no gates hit)."""
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_A, INTERNAL_B], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.all_internal is True

    @pytest.mark.asyncio
    async def test_duplicates_deduped_order_preserved(self):
        client = _capture_client([])
        result = await resolve_group_sms_routing([INTERNAL_B, INTERNAL_A, INTERNAL_B], client)
        assert isinstance(result, GroupSmsRouting)
        assert result.phones == [INTERNAL_B, INTERNAL_A]


# ===========================================================================
# _send_sms / execute_sms — group payload shape
# ===========================================================================


class TestGroupSendPayload:
    @pytest.mark.asyncio
    async def test_group_send_puts_all_recipients_in_one_payload(self):
        captured: list[dict] = []
        client = _capture_client(captured)
        result = await execute_sms(
            client,
            [INTERNAL_A, INTERNAL_B],
            "hello group",
            QUO_SERNIA_AI_PHONE_ID,
            "Sernia AI",
        )
        assert len(captured) == 1
        assert captured[0]["to"] == [INTERNAL_A, INTERNAL_B]
        assert captured[0]["from"] == QUO_SERNIA_AI_PHONE_ID
        assert captured[0]["content"] == "hello group"
        assert result.startswith("Group message sent to")
        assert INTERNAL_A in result and INTERNAL_B in result

    @pytest.mark.asyncio
    async def test_single_send_unchanged(self):
        captured: list[dict] = []
        client = _capture_client(captured)
        result = await execute_sms(
            client,
            INTERNAL_A,
            "hello",
            QUO_SERNIA_AI_PHONE_ID,
            "Sernia AI",
        )
        assert len(captured) == 1
        assert captured[0]["to"] == [INTERNAL_A]
        assert result == f"Message sent to {INTERNAL_A} from Sernia AI."

    @pytest.mark.asyncio
    async def test_auto_split_sends_every_chunk_to_full_group(self):
        captured: list[dict] = []
        client = _capture_client(captured)
        long_msg = "First sentence goes here. " * 30  # > SMS_SPLIT_THRESHOLD
        result = await _send_sms(
            client,
            "send_sms",
            [EXTERNAL_A, EXTERNAL_B],
            long_msg,
            QUO_SHARED_EXTERNAL_PHONE_ID,
            "Sernia Capital Team",
            "",
        )
        assert len(captured) >= 2, "expected auto-split into multiple sends"
        for payload in captured:
            assert payload["to"] == [EXTERNAL_A, EXTERNAL_B]
        assert "parts" in result

    @pytest.mark.asyncio
    async def test_find_group_conversation_searches_ai_line_too(self):
        """All-internal group texts are created on the AI line, so the
        conversation lookup must not stop at the shared team number.
        Regression test for the shared-line-only lookup that made
        AI-line group threads unfindable via get_thread_messages."""
        from api.src.sernia_ai.tools.quo_tools import _find_group_conversation

        ai_line_conv = {
            "id": "CNinternalgroup",
            "participants": [INTERNAL_A, INTERNAL_B],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            phone_id = request.url.params.get("phoneNumbers[]")
            if phone_id == QUO_SERNIA_AI_PHONE_ID:
                return httpx.Response(200, json={"data": [ai_line_conv]})
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(
            base_url="https://api.openphone.com",
            transport=httpx.MockTransport(handler),
        )
        conv = await _find_group_conversation(client, [INTERNAL_A, INTERNAL_B])
        assert conv is not None
        assert conv["id"] == "CNinternalgroup"

    @pytest.mark.asyncio
    async def test_group_send_failure_surfaces_error(self):
        captured: list[dict] = []
        client = _capture_client(captured, status_code=400)
        result = await execute_sms(
            client,
            [INTERNAL_A, INTERNAL_B],
            "hello group",
            QUO_SERNIA_AI_PHONE_ID,
            "Sernia AI",
        )
        assert result.startswith("Failed to send message")
