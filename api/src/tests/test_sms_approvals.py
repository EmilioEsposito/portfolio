"""Real SMS trigger, deferred tool, stored history and web/SMS contention. Providers are mocked."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete

from api.src.agent_conversations.models import AgentConversation, save_agent_conversation
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai import conversation_handler, runs
from api.src.sernia_ai import sms_approvals as approvals
from api.src.sernia_ai.tools import clickup_tools
from api.src.sernia_ai.triggers import ai_sms_event_trigger as trigger

PHONE = "+14125550165"
AI = "+14125550166"
CALL = {"tool_name": "clickup_delete_task", "args": {"task_id": "demo123"}, "tool_call_id": "c1"}


def event(text, at):
    return {
        "event_id": str(uuid4()),
        "conversation_id": "CNtest",
        "from_number": PHONE,
        "to_number": AI,
        "message_text": text,
        "event_data": {"data": {"object": {"id": str(uuid4()), "createdAt": at.isoformat()}}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Y", "yEs"])
async def test_direct_sms_pause_and_resume(monkeypatch, text):
    """From signed-webhook payload through model, notification, exact reply and execution."""
    conv_id = "ai_sms_from_" + PHONE[1:]
    calls = []

    def model(messages, info):
        if any(
            getattr(part, "tool_name", None) == "clickup_delete_task"
            and part.part_kind == "tool-return"
            for msg in messages
            for part in msg.parts
        ):
            return ModelResponse(parts=[TextPart("Task deleted.")])
        return ModelResponse(parts=[ToolCallPart(**CALL)])

    agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

    @agent.tool_plain(requires_approval=True)
    def clickup_delete_task(task_id: str) -> str:
        calls.append(task_id)
        return "Deleted"

    for module in [runs, conversation_handler]:
        monkeypatch.setattr(module, "sernia_agent", agent)
        monkeypatch.setattr(module, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(runs, "create_logged_task", lambda coro, **kwargs: coro.close())
    send = AsyncMock()
    monkeypatch.setattr(runs, "send_sms_reply", send)
    monkeypatch.setattr(trigger, "_fetch_sms_thread", AsyncMock(return_value=[]))
    monkeypatch.setattr(trigger, "is_sernia_ai_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        trigger,
        "_verify_internal_contact",
        AsyncMock(return_value={"defaultFields": {"firstName": "Operator"}}),
    )
    monkeypatch.setattr(
        "api.src.open_phone.service.get_ai_phone_number", AsyncMock(return_value=AI)
    )
    monkeypatch.setattr(trigger, "_is_ai_sms_rate_limited", lambda _: False)
    monkeypatch.setattr(
        approvals,
        "get_contact_by_slug",
        AsyncMock(return_value=SimpleNamespace(phone_number=PHONE)),
    )
    read = AsyncMock()
    monkeypatch.setattr(approvals, "_fetch_group_messages", read)
    now = datetime.now(UTC)
    try:
        await trigger.handle_ai_sms_event(event("Delete the task", now))
        prompt = send.await_args.args[1]
        assert "Delete ClickUp task demo123?" in prompt and "Reply with just Y or Yes" in prompt
        assert calls == []
        for correction in ["yes please", "No", "Y!", " yes ", "Cancel that"]:
            await trigger.handle_ai_sms_event(event(correction, now))
            assert send.await_args.args == (PHONE, prompt)
        read.assert_not_awaited()
        previous = {
            "id": "ACprompt",
            "direction": "outgoing",
            "from": AI,
            "text": prompt,
            "createdAt": now.isoformat(),
        }
        inbound = event(text, now + timedelta(seconds=1))
        # Another alert in this thread, or a reply queued before the prompt, cannot approve.
        read.return_value = [
            dict(previous, text="Can you check on this?"),
            inbound["event_data"]["data"]["object"],
        ]
        await trigger.handle_ai_sms_event(inbound)
        stale = event(text, now - timedelta(seconds=1))
        read.return_value = [previous, stale["event_data"]["data"]["object"]]
        await trigger.handle_ai_sms_event(stale)
        assert calls == []
        read.side_effect = [[previous], [previous, inbound["event_data"]["data"]["object"]]]
        await trigger.handle_ai_sms_event(inbound)
        assert calls == ["demo123"]
        assert send.await_args.args == (PHONE, "Task deleted.")
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(AgentConversation).where(AgentConversation.id == conv_id))
            await session.commit()


@pytest.mark.asyncio
async def test_web_and_sms_cannot_both_claim():
    cid = f"test-sms-{uuid4()}"
    state = approvals.new_sms_approval([CALL])
    async with AsyncSessionFactory() as session:
        await save_agent_conversation(
            session,
            cid,
            "sernia",
            [ModelResponse(parts=[ToolCallPart(**CALL)])],
            modality="sms",
            metadata={approvals.STATE_KEY: state},
        )

    async def claim(ref):
        async with AsyncSessionFactory() as session:
            await approvals.claim_sms_conversation_decision(session, cid, ["c1"], expected_ref=ref)

    try:
        with pytest.raises(HTTPException, match="409"):
            await claim("obsolete")
        results = await asyncio.gather(claim(state["ref"]), claim(None), return_exceptions=True)
        assert results.count(None) == 1
        assert [e.status_code for e in results if isinstance(e, HTTPException)] == [409]
        async with AsyncSessionFactory() as session:
            with pytest.raises(HTTPException, match="409"):
                await approvals.claim_sms_conversation_decision(session, cid, ["c1"], deny_all=True)
            row = await session.get(AgentConversation, cid)
            expired = datetime.now(UTC) - timedelta(seconds=1)
            row.metadata_ = {
                approvals.STATE_KEY: {
                    **row.metadata_[approvals.STATE_KEY],
                    "expires_at": expired.isoformat(),
                }
            }
            await session.commit()
            with pytest.raises(HTTPException, match="409"):
                await approvals.claim_sms_conversation_decision(session, cid, ["c1"])
            await approvals.claim_sms_conversation_decision(session, cid, ["c1"], deny_all=True)
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(AgentConversation).where(AgentConversation.id == cid))
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recipients,pending,state",
    [
        ([PHONE, AI], [CALL], {"ref": "a"}),
        ([AI], [CALL], {"ref": "a"}),
        ([PHONE], [CALL, CALL], {"ref": "a"}),
        ([PHONE], [CALL], {}),
        ([PHONE], [dict(CALL, args={"body": "x" * 500})], {"ref": "a"}),
        ([PHONE], [CALL], {"ref": "a", "web_review": True}),
    ],
)
async def test_review_link_when_sms_cannot_authorize(monkeypatch, recipients, pending, state):
    monkeypatch.setattr(
        approvals,
        "get_contact_by_slug",
        AsyncMock(return_value=SimpleNamespace(phone_number=PHONE)),
    )
    prompt, eligible = await approvals.approval_prompt("test", recipients, pending, state)
    assert not eligible and "sernia-chat?id=test" in prompt and "Reply" not in prompt


@pytest.mark.asyncio
async def test_clickup_delete_accepts_no_content(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(204))
    monkeypatch.setattr(clickup_tools, "_clickup_request", request)
    assert await clickup_tools.delete_task(None, "demo123") == "Task demo123 deleted."
    request.assert_awaited_once_with("DELETE", "/task/demo123")
