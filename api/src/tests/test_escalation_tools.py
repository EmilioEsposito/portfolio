"""
Unit tests for the emergency escalation tool (``emergency_trigger_escalation``)
and the extracted ``trigger_twilio_escalation`` core function.

No network, no DB: Twilio is exercised via ``mock=True`` or patched entirely,
and contact resolution is patched where the default lookup would hit the DB.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.open_phone.escalate import trigger_twilio_escalation
from api.src.sernia_ai.tools.escalation_tools import (
    MAX_ESCALATION_MESSAGE_CHARS,
    escalation_toolset,
    trigger_escalation,
)


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.deps.conversation_id = "conv-1"
    ctx.deps.user_identifier = "+14125550000"
    ctx.deps.modality = "sms"
    return ctx


# ---------------------------------------------------------------------------
# trigger_twilio_escalation (core function in api/src/open_phone/escalate.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_twilio_escalation_mock_counts_each_number():
    """With explicit numbers and mock=True, one execution per number, no network."""
    count = await trigger_twilio_escalation(
        "test emergency",
        escalate_to_numbers=["+14123703550", "+14125551234"],
        mock=True,
    )
    assert count == 2


@pytest.mark.asyncio
async def test_trigger_twilio_escalation_no_contacts_returns_zero():
    """When the default contact lookup yields nothing, no executions are attempted."""
    with patch(
        "api.src.open_phone.escalate.get_contact_by_slug",
        AsyncMock(return_value=None),
    ):
        count = await trigger_twilio_escalation("test emergency", mock=True)
    assert count == 0


@pytest.mark.asyncio
async def test_trigger_twilio_escalation_resolves_default_contacts():
    """With no explicit numbers, emilio + peppino are resolved from the DB."""
    contact = MagicMock()
    contact.phone_number = "+14123703550"
    with patch(
        "api.src.open_phone.escalate.get_contact_by_slug",
        AsyncMock(return_value=contact),
    ) as lookup:
        count = await trigger_twilio_escalation("test emergency", mock=True)
    assert count == 2  # one per slug (emilio, peppino)
    assert {c.args[0] for c in lookup.call_args_list} == {"emilio", "peppino"}


# ---------------------------------------------------------------------------
# trigger_escalation (agent tool in api/src/sernia_ai/tools/escalation_tools.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_triggers_and_reports_success():
    with patch(
        "api.src.sernia_ai.tools.escalation_tools.trigger_twilio_escalation",
        AsyncMock(return_value=2),
    ) as trigger:
        result = await trigger_escalation(
            _make_ctx(), "Anna (320-02) reports water pouring through her ceiling"
        )

    assert "Escalation triggered: 2 phone call(s)" in result
    # The agent's message is prefixed so recipients know the source.
    sent_message = trigger.call_args.args[0]
    assert sent_message.startswith("URGENT! Sernia AI escalation: ")
    assert "water pouring" in sent_message


@pytest.mark.asyncio
async def test_tool_reports_failure_when_no_calls_placed():
    with patch(
        "api.src.sernia_ai.tools.escalation_tools.trigger_twilio_escalation",
        AsyncMock(return_value=0),
    ):
        result = await trigger_escalation(_make_ctx(), "fire in the building")

    assert "Escalation FAILED" in result
    assert "send_sms" in result  # tells the model the fallback path


@pytest.mark.asyncio
async def test_tool_rejects_empty_message_without_calling_twilio():
    with patch(
        "api.src.sernia_ai.tools.escalation_tools.trigger_twilio_escalation",
        AsyncMock(),
    ) as trigger:
        result = await trigger_escalation(_make_ctx(), "   ")

    trigger.assert_not_called()
    assert "NOT triggered" in result


@pytest.mark.asyncio
async def test_tool_rejects_overlong_message_without_calling_twilio():
    with patch(
        "api.src.sernia_ai.tools.escalation_tools.trigger_twilio_escalation",
        AsyncMock(),
    ) as trigger:
        result = await trigger_escalation(_make_ctx(), "x" * (MAX_ESCALATION_MESSAGE_CHARS + 1))

    trigger.assert_not_called()
    assert "NOT triggered" in result
    assert "Shorten" in result


# ---------------------------------------------------------------------------
# Agent wiring
# ---------------------------------------------------------------------------


def test_toolset_exposes_trigger_escalation():
    assert "trigger_escalation" in escalation_toolset.tools


def test_agent_registers_emergency_toolset():
    """The agent carries the escalation toolset under the `emergency` prefix,
    so the model sees the tool as `emergency_trigger_escalation`."""
    from api.src.sernia_ai.agent import sernia_agent

    named = {getattr(ts, "name", None) for ts in sernia_agent.toolsets}
    assert "emergency" in named
