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


@pytest.mark.asyncio
async def test_group_trigger_loads_api_history_and_sets_reply_owner(monkeypatch):
    from unittest.mock import MagicMock

    import httpx

    contact = {"defaultFields": {"company": "Sernia Capital LLC", "firstName": "Test"}}
    monkeypatch.setattr(trigger, "_verify_internal_contact", AsyncMock(return_value=contact))
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(trigger, "AsyncSessionFactory", session_factory)
    monkeypatch.setattr(trigger, "get_conversation_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(trigger, "save_agent_conversation", AsyncMock())
    monkeypatch.setattr(trigger, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(trigger, "extract_pending_approvals", lambda _: [])
    result = MagicMock(output="One group answer")
    result.all_messages.return_value = []
    agent = MagicMock(run=AsyncMock(return_value=result))
    monkeypatch.setattr(trigger, "sernia_agent", agent)
    reply = AsyncMock()
    monkeypatch.setattr(trigger, "_send_sms_reply", reply)
    monkeypatch.setattr(trigger, "create_logged_task", lambda coro, **kwargs: coro.close())
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params.get_list("participants") == sorted([PHONE, OTHER])
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "ACold",
                        "conversationId": "CNgroup",
                        "text": "Earlier group message",
                        "from": OTHER,
                        "to": [PHONE],
                        "createdAt": "2026-09-10T15:00:00Z",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        quo_tools,
        "_build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
        ),
    )
    fallback = AsyncMock()
    monkeypatch.setattr(quo_tools, "_fetch_group_thread_from_events_table", fallback)
    await trigger._handle_group_sms(
        {"from_number": PHONE, "message_text": "Reply once", "conversation_id": "CNgroup"},
        sender_contact=contact,
        sender_name="Test",
        other_participants=[OTHER],
        ai_phone="+14125559999",
    )
    assert len(requests) == 1
    fallback.assert_not_called()
    assert agent.run.call_args.kwargs["deps"].sms_reply_recipients == sorted([PHONE, OTHER])
    assert "Earlier group message" in str(agent.run.call_args.kwargs["message_history"])
    reply.assert_awaited_once_with(sorted([PHONE, OTHER]), "One group answer")


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_to", [PHONE, [PHONE, OTHER]])
async def test_approval_resumption_restores_sms_reply_owner(monkeypatch, reply_to):
    from unittest.mock import MagicMock

    from api.src.sernia_ai import routes

    user = MagicMock(id="user-test", first_name="Test", last_name="User", email_addresses=[])
    metadata = {"trigger_phone": PHONE}
    if isinstance(reply_to, list):
        metadata["trigger_group_participants"] = reply_to
    conv = SimpleNamespace(modality="sms", metadata_=metadata)
    result = MagicMock(output="Resumed answer")
    resume = AsyncMock(return_value=result)
    monkeypatch.setattr(routes, "get_agent_conversation", AsyncMock(return_value=conv))
    monkeypatch.setattr(routes, "resume_with_approvals", resume)
    monkeypatch.setattr(routes, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "persist_agent_run_result", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "extract_pending_approvals", lambda _: [])
    monkeypatch.setattr(routes, "extract_tool_results", lambda _: {})
    monkeypatch.setattr(routes, "create_logged_task", lambda coro, **kwargs: coro.close())
    await routes.approve_conversation(
        "sms-test", routes.ApprovalRequest(decisions=[]), user, AsyncMock()
    )
    assert resume.call_args.kwargs["deps"].sms_reply_recipients == (
        [reply_to] if isinstance(reply_to, str) else reply_to
    )
    assert resume.call_args.kwargs["deps"].modality == "sms"
