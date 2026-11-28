"""A person hands a lead conversation to the general agent in a new chat.

What the new chat is told, what happens to the card left waiting, and that
nothing starts until a person sends a message. Uses the test database; no
Gmail and no real model."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic_ai import Agent
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete
from starlette.requests import Request

from api.src.agent_conversations import routes as conversation_routes
from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    save_agent_conversation,
)
from api.src.agent_conversations.push import service as push_service
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai import chat_runs, lead_hand_off, routes, runs
from api.src.sernia_ai.lead_policy import SKILL_NAME
from api.src.tests.test_zillow_lead_handle import RELAY, message, outcome, stored, thread
from api.src.zillow_leads import handle, handoff, listing_data

USER = SimpleNamespace(id="system:hand-off-test", first_name="Pat", last_name="Doe")
USER.email_addresses = []


@pytest_asyncio.fixture
async def conversation_id(monkeypatch):
    """A fresh lead conversation ID, with Gmail, the model and push stubbed out."""
    cid = f"test-lead-{uuid4().hex}"
    monkeypatch.setattr(listing_data, "load", AsyncMock(return_value=listing_data.Loaded()))
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(
        handle, "send_reply", AsyncMock(return_value=handle.Delivery("Email sent."))
    )
    yield cid
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id == cid))
        await session.commit()


async def lead_conversation(conversation_id, monkeypatch, result=None):
    """A lead conversation as the queue saves it, with one card waiting."""
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=result or outcome("escalate")))
    await handle.handle_thread("T", conversation_id=conversation_id, metadata={})
    row, messages = await stored(conversation_id)
    (card,) = extract_pending_approval_from_messages(messages)
    return row, card


async def context(conversation_id):
    async with AsyncSessionFactory() as session:
        return await handoff.lead_context(session, conversation_id)


class TestLeadContext:
    @pytest.mark.asyncio
    async def test_an_escalation_is_told_with_its_question_its_route_and_its_case(
        self, conversation_id, monkeypatch
    ):
        await lead_conversation(
            conversation_id,
            monkeypatch,
            outcome("escalate", team_notes=["Check the pet rule for 12-C."]),
        )

        lead = await context(conversation_id)

        assert lead.conversation_id == conversation_id and lead.thread_id == "T"
        assert "Gmail thread ID: T" in lead.text
        assert f"- To: {RELAY}" in lead.text
        assert "- Subject: Re: 12 Example St inquiry" in lead.text
        assert "- Gmail message ID to reply to: m0" in lead.text
        assert "1. Asked the team (still waiting for a person):" in lead.text
        assert "   Question: Is 12-C listed?" in lead.text
        assert "   Why: Because." in lead.text
        assert "   - Check the pet rule for 12-C." in lead.text
        # The case is the lead agent's own, fenced so its headings stay inside it.
        case = lead.text.split("<case>\n")[1].split("\n</case>")[0]
        assert case.startswith("Current time:") and "Is it available?" in case

    @pytest.mark.asyncio
    async def test_a_draft_is_told_with_how_it_ended_and_the_words_actually_sent(
        self, conversation_id, monkeypatch
    ):
        row, card = await lead_conversation(conversation_id, monkeypatch, outcome())
        waiting = (await context(conversation_id)).text
        assert "1. Drafted a reply to the lead (still waiting for a person):" in waiting
        assert "   > Hello," in waiting and "   > Sernia Capital Team" in waiting

        async with AsyncSessionFactory() as session:
            await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[
                    ApprovalDecision(
                        card["tool_call_id"], approved=True, override_args={"body": "Hi instead."}
                    )
                ],
                approver="Pat",
            )
        sent = (await context(conversation_id)).text

        assert "1. Drafted a reply to the lead (result: Email sent.):" in sent
        assert "2. Pat edited the draft; these words were sent:" in sent
        assert "   > Hi instead." in sent
        assert "Noted:" not in sent  # the result is not told a second time

    @pytest.mark.asyncio
    async def test_a_thread_with_no_zillow_address_says_so(self, conversation_id, monkeypatch):
        monkeypatch.setattr(
            handle, "load_lead_thread", AsyncMock(return_value=thread(message(0, reply_to=None)))
        )
        monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome()))
        await handle.handle_thread("T", conversation_id=conversation_id, metadata={})

        text = (await context(conversation_id)).text

        assert "This thread has no Zillow relay address to reply to." in text
        assert "- To:" not in text

    @pytest.mark.asyncio
    async def test_only_a_lead_conversation_can_be_handed_off(self, conversation_id):
        async with AsyncSessionFactory() as session:
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=routes.AGENT_NAME,
                messages=[],
                metadata={"trigger_source": "web_chat"},
            )

        assert await context(conversation_id) is None
        assert await context("no-such-conversation") is None


class TestClose:
    @pytest.mark.asyncio
    async def test_an_escalation_stops_waiting_and_records_where_it_went(
        self, conversation_id, monkeypatch
    ):
        await lead_conversation(conversation_id, monkeypatch)

        await handoff.close(
            conversation_id,
            reason="Handed to Sernia AI by Pat.",
            by="Pat",
            to_conversation_id="new-chat",
        )

        row, messages = await stored(conversation_id)
        assert extract_pending_approval_from_messages(messages) == []
        assert "Handed to Sernia AI by Pat." in json.dumps(row.messages)
        assert row.metadata_[handoff.HANDED_TO]["to"] == "new-chat"
        (record,) = row.metadata_["approval"]
        assert (record["tool_name"], record["approved"]) == (handle.ASK_TEAM, False)

    @pytest.mark.asyncio
    async def test_a_waiting_draft_is_not_sent(self, conversation_id, monkeypatch):
        await lead_conversation(conversation_id, monkeypatch, outcome())

        await handoff.close(
            conversation_id,
            reason="Handed to Sernia AI by Pat.",
            by="Pat",
            to_conversation_id="new-chat",
        )

        handle.send_reply.assert_not_called()
        row, messages = await stored(conversation_id)
        assert extract_pending_approval_from_messages(messages) == []
        assert "Not sent. Handed to Sernia AI by Pat." in json.dumps(row.messages)

    @pytest.mark.asyncio
    async def test_a_conversation_already_decided_only_records_the_hand_off(
        self, conversation_id, monkeypatch
    ):
        row, card = await lead_conversation(conversation_id, monkeypatch)
        async with AsyncSessionFactory() as session:
            await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                approver="Sam",
            )

        await handoff.close(conversation_id, reason="x", by="Pat", to_conversation_id="new-chat")

        row, _ = await stored(conversation_id)
        assert "Marked handled by Sam." in json.dumps(row.messages)
        assert len(row.metadata_["approval"]) == 1
        assert row.metadata_[handoff.HANDED_TO]["by"] == "Pat"


class TestCards:
    @pytest.mark.asyncio
    async def test_marking_an_escalation_handled_sends_nothing_and_names_who(
        self, conversation_id, monkeypatch
    ):
        row, card = await lead_conversation(conversation_id, monkeypatch)

        async with AsyncSessionFactory() as session:
            result = await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                approver="Pat",
            )

        handle.send_reply.assert_not_called()
        assert result["output"] == "Marked handled by Pat." and result["pending"] == []

    @pytest.mark.asyncio
    async def test_an_escalation_pushes_its_question_not_a_request_to_approve(
        self, conversation_id, monkeypatch
    ):
        await lead_conversation(conversation_id, monkeypatch)
        await asyncio.sleep(0)  # the push is a background task

        pushed = handle.notify_pending_approval.call_args.kwargs
        assert pushed["title"] == "Lead needs a person"
        assert pushed["body"] == "Is 12-C listed?"

    def test_a_long_question_is_cut_to_fit_a_push_and_a_draft_keeps_the_approval_push(self):
        long = {"tool_name": handle.ASK_TEAM, "args": {"question": "Why? " * 100}}
        body = handle.push_text(long)["body"]
        assert len(body) <= handle.PUSH_BODY_LIMIT and body.endswith("…")
        assert handle.push_text({"tool_name": handle.SEND_REPLY, "args": {"to": RELAY}}) == {}

    @pytest.mark.asyncio
    async def test_the_push_uses_the_words_it_is_given(self, monkeypatch):
        sent = AsyncMock()
        monkeypatch.setattr(push_service, "notify_all_sernia_users", sent)

        await push_service.notify_pending_approval(
            "c1", handle.ASK_TEAM, {"question": "q"}, title="Lead needs a person", body="q"
        )
        await push_service.notify_pending_approval("c2", "send_email", {"to": "a@example.com"})

        first, second = (call.kwargs for call in sent.await_args_list)
        assert (first["title"], first["body"]) == ("Lead needs a person", "q")
        assert second["title"] == "Approval Needed: Send Email"
        assert second["body"] == "to: a@example.com"


def test_the_block_names_the_policy_the_route_and_the_lead_conversation():
    lead = handoff.LeadContext(
        conversation_id="zillow-T-1", thread_id="T", text="Gmail thread ID: T"
    )

    block = lead_hand_off.block(lead, by="Pat")

    lines = block.splitlines()
    assert lines[0] == lead_hand_off.START and lines[-1] == lead_hand_off.END
    assert f"`{SKILL_NAME}`" in block and "`google_send_email`" in block
    assert "Pat's request is the text above this block" in block
    # A team note is told to the person in the chat, never texted to staff.
    assert "tell Pat here in this chat" in block and "Do not text or email" in block
    assert "/team/chat?id=zillow-T-1" in block
    assert block.count("Gmail thread ID: T") == 1


def chat_request(conversation_id, text, **extra):
    payload = json.dumps(
        {
            "id": conversation_id,
            "request_id": str(uuid4()),
            "trigger": "submit-message",
            "messages": [
                {"id": str(uuid4()), "role": "user", "parts": [{"type": "text", "text": text}]}
            ],
            **extra,
        }
    ).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)


@pytest_asyncio.fixture
async def chat(monkeypatch):
    """The chat endpoint with a model that answers at once and records what it read."""
    new_id = f"test-hand-off-{uuid4()}"
    read: list[str] = []

    async def stream(history, info):
        read.extend(p.content for m in history for p in m.parts if isinstance(p, UserPromptPart))
        yield "On it."

    async def config():
        return {}

    def discard(coro, **kwargs):
        coro.close()

    monkeypatch.setattr(routes, "sernia_agent", Agent(FunctionModel(stream_function=stream)))
    monkeypatch.setattr(chat_runs, "resolve_active_run_kwargs", config)
    monkeypatch.setattr(runs, "create_logged_task", discard)
    yield SimpleNamespace(id=new_id, read=read)
    await chat_runs.shutdown_chat_runs()
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id == new_id))
        await session.commit()


async def send(conversation_id, text, **extra):
    async with AsyncSessionFactory() as session:
        return await routes.chat_sernia(
            chat_request(conversation_id, text, **extra), user=USER, session=session
        )


async def page(conversation_id):
    async with AsyncSessionFactory() as session:
        return await conversation_routes.get_conversation_messages_endpoint(
            conversation_id, user=USER, session=session
        )


class TestChat:
    @pytest.mark.asyncio
    async def test_nothing_runs_until_a_person_sends_a_message(
        self, conversation_id, monkeypatch, chat
    ):
        await lead_conversation(conversation_id, monkeypatch)

        async with AsyncSessionFactory() as session:
            fetched = await routes.lead_hand_off_block(conversation_id, user=USER, session=session)

        # Fetching the block is a read: no run, and the card still waits.
        assert fetched["block"].startswith(lead_hand_off.START)
        assert "Pat Doe is handing you" in fetched["block"]
        assert chat.read == [] and not chat_runs._tasks
        assert (await page(conversation_id))["pending"]
        assert (await page(conversation_id))["lead_conversation"] is True

    @pytest.mark.asyncio
    async def test_a_message_from_a_lead_conversation_starts_a_new_chat_and_closes_the_card(
        self, conversation_id, monkeypatch, chat
    ):
        await lead_conversation(conversation_id, monkeypatch)
        async with AsyncSessionFactory() as session:
            block = (await routes.lead_hand_off_block(conversation_id, user=USER, session=session))[
                "block"
            ]
        first_message = f"Cats are fine there. Tell them.\n\n{block}"

        await send(chat.id, first_message, handoff_from=conversation_id)
        await asyncio.wait_for(asyncio.gather(*list(chat_runs._tasks)), timeout=10)

        # The general agent read the person's words and the lead context, as sent.
        assert chat.read == [first_message]
        new_chat = await page(chat.id)
        assert new_chat["hand_off_from"] == conversation_id
        assert new_chat["lead_conversation"] is False
        assert "On it." in json.dumps(new_chat["messages"])
        # The lead conversation is left as a record, closed, with a link forward.
        old = await page(conversation_id)
        assert old["pending"] == [] and old["hand_off_to"] == chat.id
        assert "Handed to Sernia AI by Pat Doe." in json.dumps(old["messages"])
        handle.send_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_hand_off_from_something_that_is_not_a_lead_conversation_is_refused(self, chat):
        with pytest.raises(HTTPException) as refused:
            await send(chat.id, "Take this lead.", handoff_from="no-such-conversation")

        assert refused.value.status_code == 404
        assert chat.read == []
        async with AsyncSessionFactory() as session:
            assert await session.get(AgentConversation, chat.id) is None
            with pytest.raises(HTTPException) as missing:
                await routes.lead_hand_off_block("no-such-conversation", user=USER, session=session)
        assert missing.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_later_message_in_the_new_chat_closes_nothing(
        self, conversation_id, monkeypatch, chat
    ):
        await send(chat.id, "An ordinary chat.")
        await asyncio.wait_for(asyncio.gather(*list(chat_runs._tasks)), timeout=10)
        await lead_conversation(conversation_id, monkeypatch)

        await send(chat.id, "And another thing.", handoff_from=conversation_id)
        await asyncio.wait_for(asyncio.gather(*list(chat_runs._tasks)), timeout=10)

        assert (await page(conversation_id))["pending"]
        assert (await page(chat.id))["hand_off_from"] is None
