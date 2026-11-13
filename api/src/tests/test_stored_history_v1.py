"""Conversations saved by pydantic-ai 1.x still work under the installed version.

Production stored every conversation as ``ModelMessagesTypeAdapter`` JSON on
1.107.6 before the move to 2.x. ``stored_history/pydantic_ai_1_107_6.json`` is
that JSON, written by ``stored_history/generate_v1.py`` under 1.107.6 (made-up
data). These tests read it the way the app does and act on what was pending
when it was saved: a general-agent tool call and a lead reply, each waiting
for a person. Uses the test database for the lead reply; no Gmail, no model.
"""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    RetryFeedbackPart,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import delete

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_agent_conversation,
    get_conversation_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.config import AGENT_NAME, TRIGGER_BOT_ID
from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sanitize_tool_calls
from api.src.sernia_ai.zillow_leads import handle

STORED = json.loads(
    (Path(__file__).parent / "stored_history" / "pydantic_ai_1_107_6.json").read_text()
)


def load(name: str) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(STORED[name])


def test_the_fixture_was_written_by_1x():
    assert STORED["pydantic_ai_version"] == "1.107.6"
    kinds = {part["part_kind"] for m in STORED["general_pending_approval"] for part in m["parts"]}
    # The part kinds 2.x renamed or split, as 1.x wrote them.
    assert {"retry-prompt", "builtin-tool-call", "builtin-tool-return"} <= kinds


class TestGeneralConversation:
    def test_it_loads_with_each_old_part_as_what_it_meant(self):
        messages = load("general_pending_approval")
        parts = [part for message in messages for part in message.parts]

        # A retry sent back for a tool call is that call's return.
        (retried,) = [p for p in parts if isinstance(p, ToolReturnPart) and p.outcome == "retried"]
        assert (retried.tool_name, retried.tool_call_id) == ("flaky", "call_flaky")
        # A retry that answered no tool call answers none now.
        assert len([p for p in parts if isinstance(p, RetryFeedbackPart)]) == 1
        # Anthropic's server-side search, "builtin" in 1.x.
        assert [p.tool_name for p in parts if isinstance(p, NativeToolCallPart)] == ["web_search"]

    def test_saving_it_again_keeps_it_loadable(self):
        messages = load("general_pending_approval")
        saved = ModelMessagesTypeAdapter.dump_python(messages, mode="json")

        assert ModelMessagesTypeAdapter.validate_python(saved) == messages

    def test_the_pending_approval_is_still_pending(self):
        (card,) = extract_pending_approval_from_messages(load("general_pending_approval"))

        assert card == {
            "tool_call_id": "call_sms",
            "tool_name": "send_sms",
            "args": {"to": "+15550001111", "message": "Hi"},
        }

    def test_history_repair_keeps_every_answered_call(self):
        messages = load("general_pending_approval")

        repaired = _sanitize_tool_calls(messages)

        # Only the call still waiting for approval is unanswered, and the SMS
        # trigger drops that on purpose. The retried call stays with its retry.
        assert len(repaired) == len(messages)
        answered = {
            part.tool_call_id
            for message in repaired
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        assert answered == {"call_lookup", "call_flaky", "call_flaky2"}

    @pytest.mark.asyncio
    async def test_approving_it_runs_the_tool_once_with_the_stored_arguments(self):
        sent: list[tuple[str, str]] = []

        def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            (result,) = [
                part
                for part in messages[-1].parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "call_sms"
            ]
            return ModelResponse(parts=[TextPart(f"Done: {result.content}")])

        agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

        @agent.tool_plain(requires_approval=True)
        def send_sms(to: str, message: str) -> str:
            sent.append((to, message))
            return "sent"

        result = await agent.run(
            message_history=load("general_pending_approval"),
            deferred_tool_results=DeferredToolResults(approvals={"call_sms": True}),
        )

        assert sent == [("+15550001111", "Hi")]
        assert result.output == "Done: sent"
        assert extract_pending_approval_from_messages(result.all_messages()) == []


@pytest_asyncio.fixture
async def lead_conversation(monkeypatch):
    """A lead reply waiting for approval, in the row exactly as 1.x stored it."""
    conversation_id = f"test-lead-v1-{uuid.uuid4().hex}"
    route = {
        "to": "abc123@convo.zillow.com",
        "subject": "Re: 12 Example St inquiry",
        "reply_to_message_id": "m0",
    }
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    async with AsyncSessionFactory() as session:
        session.add(
            AgentConversation(
                id=conversation_id,
                agent_name=AGENT_NAME,
                clerk_user_id=TRIGGER_BOT_ID,
                messages=STORED["lead_pending_reply"],
                metadata_={"handler": handle.HANDLER, "lead_thread_id": "T", "reply_route": route},
            )
        )
        await session.commit()
    yield conversation_id, route
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AgentConversation).where(AgentConversation.id == conversation_id)
        )
        await session.commit()


class TestLeadConversation:
    @pytest.mark.asyncio
    async def test_a_reply_drafted_before_the_upgrade_can_be_approved_after_it(
        self, lead_conversation
    ):
        conversation_id, route = lead_conversation
        async with AsyncSessionFactory() as session:
            row = await get_agent_conversation(session, conversation_id, retries=1)
            messages = await get_conversation_messages(conversation_id, None, session=session)
        (card,) = extract_pending_approval_from_messages(messages)
        assert card["tool_name"] == handle.SEND_REPLY
        assert handle.is_lead_conversation(row.metadata_)

        async with AsyncSessionFactory() as session:
            result = await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                approver="Pat",
            )

        # Sent once, as drafted, to the route stored with the conversation.
        handle.send_reply.assert_awaited_once_with(route, "Hello,\n\nSernia Capital Team")
        assert result["status"] == "completed" and result["pending"] == []
        async with AsyncSessionFactory() as session:
            messages = await get_conversation_messages(conversation_id, None, session=session)
        assert extract_pending_approval_from_messages(messages) == []

        # A second approval finds nothing pending and sends nothing more.
        with pytest.raises(ValueError, match="Nothing is waiting"):
            async with AsyncSessionFactory() as session:
                await handle.apply_approval(
                    session,
                    conversation_id=conversation_id,
                    metadata=row.metadata_,
                    decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                    approver="Pat",
                )
        handle.send_reply.assert_awaited_once()
