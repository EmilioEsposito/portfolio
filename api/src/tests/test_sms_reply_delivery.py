"""Regression coverage for the two-send production incident; all providers mocked."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools import quo_tools
from api.src.sernia_ai.triggers import ai_sms_event_trigger as trigger

PHONE = "+14125550101"
OTHER = "+14125550102"


def deps(recipients):
    return SerniaDeps(
        db_session=AsyncMock(),
        conversation_id="sms-test",
        user_identifier="test",
        user_name="Test",
        user_email="test@serniacapital.com",
        modality="sms",
        workspace_path=Path("/tmp"),
        sms_reply_recipients=recipients,
    )


def sms_tool():
    return quo_tools.quo_toolset.toolsets[1].tools["send_sms"].function


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recipients,to", [([PHONE], PHONE), ([PHONE], [PHONE]), ([PHONE, OTHER], [OTHER, PHONE, PHONE])]
)
async def test_tool_then_final_reply_sends_once(monkeypatch, recipients, to):
    """Reproduce tool-call + identical final answer: only final answer reaches Quo."""
    send = AsyncMock()
    tool_send = AsyncMock()
    monkeypatch.setattr(trigger, "send_message", send)
    monkeypatch.setattr(quo_tools, "execute_sms", tool_send)
    ctx = SimpleNamespace(deps=deps(recipients), tool_call_approved=False)
    result = await sms_tool()(ctx, to, "Thanks, I marked it complete.")
    assert result.startswith("No SMS sent:")
    tool_send.assert_not_called()
    await trigger._send_sms_reply(recipients, "Thanks, I marked it complete.")
    send.assert_awaited_once()
    assert send.call_args.kwargs["to_phone_number"] == recipients


@pytest.mark.asyncio
async def test_other_recipient_still_requires_routing_and_approval(monkeypatch):
    routing = AsyncMock(return_value=SimpleNamespace(is_internal=False))
    monkeypatch.setattr(quo_tools, "resolve_sms_routing", routing)
    from pydantic_ai import ApprovalRequired

    with pytest.raises(ApprovalRequired):
        await sms_tool()(
            SimpleNamespace(deps=deps([PHONE]), tool_call_approved=False), OTHER, "Hello"
        )
    routing.assert_awaited_once()


@pytest.mark.asyncio
async def test_back_to_back_sms_runs_are_serialized(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def run(event):
        order.append(("start", event["n"]))
        if event["n"] == 1:
            entered.set()
            await release.wait()
        order.append(("end", event["n"]))

    monkeypatch.setattr(trigger, "_handle_ai_sms_event", run)
    first = asyncio.create_task(trigger.handle_ai_sms_event({"conversation_id": "CNtest", "n": 1}))
    await entered.wait()
    second = asyncio.create_task(trigger.handle_ai_sms_event({"conversation_id": "CNtest", "n": 2}))
    await asyncio.sleep(0)
    assert order == [("start", 1)]
    release.set()
    await asyncio.gather(first, second)
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]
