"""The pending-approvals list (GET /workflow/pending) against real rows.

It finds what waits for approval in the database, by the chat's own rule: a
tool call in the conversation's last model response that no later request
returns.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import delete

from api.src.agent_conversations.models import (
    AgentConversation,
    get_conversation_with_pending,
    list_pending_conversations,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory

ASK = ModelRequest(parts=[UserPromptPart("Synthetic request")])


def call(tool_call_id: str, args: dict | str = "{}") -> ToolCallPart:
    return ToolCallPart("send_external_sms", args, tool_call_id=tool_call_id)


def answer(tool_call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart("send_external_sms", "sent", tool_call_id=tool_call_id)]
    )


@pytest_asyncio.fixture
async def agent():
    """A one-off agent name, so the list reads only this test's rows."""
    name = f"pending-test-{uuid4()}"
    yield name
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.agent_name == name))
        await session.commit()


async def save(agent: str, cid: str, messages: list, metadata: dict | None = None) -> None:
    async with AsyncSessionFactory() as session:
        await save_agent_conversation(session, cid, agent, messages, metadata=metadata)
        await session.commit()


@pytest.mark.asyncio
async def test_lists_only_calls_waiting_in_the_last_response(agent):
    waiting = [
        ASK,
        ModelResponse(parts=[TextPart("Drafted."), call("a1", {"message": "Hi"}), call("a2")]),
    ]
    # Some calls answered, one still waiting: only that one is listed.
    partly = [
        ASK,
        ModelResponse(parts=[call("b1"), call("b2", '{"message": "Bye"}')]),
        answer("b1"),
    ]
    answered = [
        ASK,
        ModelResponse(parts=[call("c1")]),
        answer("c1"),
        ModelResponse(parts=[TextPart("Sent.")]),
    ]
    # An unanswered call further back was interrupted; the chat repairs it.
    interrupted = [
        ASK,
        ModelResponse(parts=[call("d1")]),
        ASK,
        ModelResponse(parts=[TextPart("Done.")]),
    ]
    running = [ASK, ModelResponse(parts=[call("e1")])]
    await save(agent, "waiting", waiting)
    await save(agent, "partly", partly)
    await save(agent, "answered", answered)
    await save(agent, "interrupted", interrupted)
    await save(agent, "running", running, {"web_chat_run": {"id": "r", "status": "running"}})
    await save(agent, "empty", [])

    listed = {item["conversation_id"]: item for item in await list_pending_conversations(agent)}

    assert set(listed) == {"waiting", "partly"}
    assert all(item["has_pending"] for item in listed.values())
    assert [(p["tool_call_id"], p["args"]) for p in listed["waiting"]["pending"]] == [
        ("a1", {"message": "Hi"}),
        ("a2", {}),
    ]
    assert listed["partly"]["pending"] == [
        {"tool_call_id": "b2", "tool_name": "send_external_sms", "args": {"message": "Bye"}}
    ]
    # The same answer the chat's approval card gets for each conversation.
    for cid in ("waiting", "partly", "answered", "interrupted"):
        chat = await get_conversation_with_pending(cid)
        assert [p["tool_call_id"] for p in chat["pending"]] == [
            p["tool_call_id"] for p in listed.get(cid, {}).get("pending", [])
        ], cid
