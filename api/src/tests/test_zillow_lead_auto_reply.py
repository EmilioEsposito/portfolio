"""Automatic lead replies with Gmail, inference, and notifications mocked."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from api.src.agent_conversations.delivery import AutoApprovedSend
from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import extract_pending_approval_from_messages
from api.src.database.database import AsyncSessionFactory
from api.src.tests import test_zillow_lead_handle as lead_tests
from api.src.tests.test_zillow_lead_handle import (
    START,
    message,
    outcome,
    stored,
    thread,
)
from api.src.zillow_leads import auto_reply, handle
from api.src.zillow_leads.case import ListingData, build_case
from api.src.zillow_leads.settings import LeadSettings

conversation_id = lead_tests.conversation_id


@pytest_asyncio.fixture
async def ready(conversation_id, monkeypatch):
    original = f"<{conversation_id}@zillow.example>"
    lead = thread(message(0, rfc_message_id=original))
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=lead))
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome()))
    monkeypatch.setattr(
        handle,
        "_get_threading_headers",
        AsyncMock(return_value={"in_reply_to": original, "thread_id": "T"}),
    )
    monkeypatch.setattr(
        auto_reply,
        "effective_settings",
        AsyncMock(return_value=LeadSettings(require_approval=False)),
    )
    monkeypatch.setattr(auto_reply, "is_sernia_ai_enabled", AsyncMock(return_value=True))
    yield conversation_id
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AutoApprovedSend).where(AutoApprovedSend.conversation_id == conversation_id)
        )
        await session.commit()


async def run(cid):
    return await handle.handle_thread("T", conversation_id=cid, metadata={})


@pytest.mark.asyncio
async def test_auto_mode_sends_once_and_records_the_reply(ready):
    handled = await run(ready)
    assert handled.persisted and not handled.pending
    handle.send_reply.assert_awaited_once()
    row, messages = await stored(ready)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert not extract_pending_approval_from_messages(messages)
    handle.notify_pending_approval.assert_not_called()


@pytest.mark.asyncio
async def test_approval_mode_keeps_a_send_card(ready, monkeypatch):
    monkeypatch.setattr(auto_reply, "effective_settings", AsyncMock(return_value=LeadSettings()))
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()
    _, messages = await stored(ready)
    assert extract_pending_approval_from_messages(messages)[0]["tool_name"] == handle.SEND_REPLY


@pytest.mark.asyncio
async def test_automation_kill_switch_prevents_automatic_delivery(ready, monkeypatch):
    monkeypatch.setattr(auto_reply, "is_sernia_ai_enabled", AsyncMock(return_value=False))
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["no_reply", "escalate"])
async def test_non_reply_outcomes_never_send(ready, monkeypatch, action):
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome(action)))
    await run(ready)
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_untrusted_recipient_goes_to_a_person(ready, monkeypatch):
    monkeypatch.setattr(
        handle,
        "load_lead_thread",
        AsyncMock(return_value=thread(message(0, reply_to="outsider@example.com"))),
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_link_stays_for_review(ready, monkeypatch):
    monkeypatch.setattr(
        handle,
        "respond",
        AsyncMock(return_value=outcome(reply_body="Go to https://attacker.example/collect")),
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()
    row, messages = await stored(ready)
    assert row.metadata_["auto_delivery"]["status"] == "needs_review"
    assert "link" in extract_pending_approval_from_messages(messages)[0]["args"]["team_notes"][0]


@pytest.mark.asyncio
async def test_a_changed_thread_stays_for_review(ready, monkeypatch):
    monkeypatch.setattr(
        handle,
        "load_lead_thread",
        AsyncMock(side_effect=[thread(message(0)), thread(message(0), message(5))]),
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_unresolved_threading_cannot_send_a_new_email(ready, monkeypatch):
    monkeypatch.setattr(handle, "_get_threading_headers", AsyncMock(return_value={}))
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_failed_delivery_leaves_an_inspect_only_task_and_is_not_retried(ready, monkeypatch):
    monkeypatch.setattr(
        handle, "send_reply", AsyncMock(side_effect=TimeoutError("Gmail may have accepted it"))
    )
    assert (await run(ready)).pending
    row, messages = await stored(ready)
    (card,) = extract_pending_approval_from_messages(messages)
    assert card["tool_name"] == handle.ASK_TEAM
    # A second attempt cannot auto-send the same original RFC Message-ID.
    await run(ready)
    assert handle.send_reply.await_count == 1
    row, messages = await stored(ready)
    (card,) = extract_pending_approval_from_messages(messages)
    async with AsyncSessionFactory() as session:
        await handle.apply_approval(
            session,
            conversation_id=ready,
            metadata=row.metadata_,
            decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
            approver="Test",
        )
    assert handle.send_reply.await_count == 1


@pytest.mark.asyncio
async def test_reservation_is_committed_and_no_send_card_exists_before_gmail(ready, monkeypatch):
    async def inspect_before_send(*args, **kwargs):
        async with AsyncSessionFactory() as session:
            claim = await session.scalar(
                select(AutoApprovedSend).where(AutoApprovedSend.conversation_id == ready)
            )
            assert claim is not None
        _, messages = await stored(ready)
        assert all(
            card["tool_name"] == handle.ASK_TEAM
            for card in extract_pending_approval_from_messages(messages)
        )
        return "Email sent."

    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=inspect_before_send))
    await run(ready)
    handle.send_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_followups_survive_an_automatic_reply(ready, monkeypatch):
    monkeypatch.setattr(
        handle, "respond", AsyncMock(return_value=outcome(team_notes=["Arrange resident notice."]))
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_awaited_once()
    _, messages = await stored(ready)
    (card,) = extract_pending_approval_from_messages(messages)
    assert card["tool_name"] == handle.ASK_TEAM
    assert card["args"]["team_notes"] == ["Arrange resident notice."]


@pytest.mark.asyncio
async def test_turning_auto_mode_on_does_not_send_an_existing_pending_draft(ready, monkeypatch):
    monkeypatch.setattr(auto_reply, "effective_settings", AsyncMock(return_value=LeadSettings()))
    await run(ready)
    monkeypatch.setattr(
        auto_reply,
        "effective_settings",
        AsyncMock(return_value=LeadSettings(require_approval=False)),
    )
    from api.src.zillow_leads.queue import _already_completed

    assert await _already_completed(ready)
    handle.send_reply.assert_not_called()


def test_unoffered_times_and_unknown_links_are_held():
    case = build_case(thread(message(0)), ListingData(), now=START)
    assert auto_reply.reply_problems(case, outcome(offered_slots=[START]))
    assert auto_reply.reply_problems(case, outcome(reply_body="www.attacker.example"))
    assert not auto_reply.reply_problems(case, outcome())


@pytest.mark.asyncio
async def test_a_concurrent_approval_cannot_send_during_auto_delivery(ready, monkeypatch):
    async def try_approval(*args, **kwargs):
        row, messages = await stored(ready)
        (card,) = extract_pending_approval_from_messages(messages)
        assert card["tool_name"] == handle.ASK_TEAM
        async with AsyncSessionFactory() as session:
            await handle.apply_approval(
                session,
                conversation_id=ready,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                approver="Test",
            )
        return "Email sent."

    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=try_approval))
    assert not (await run(ready)).pending
    handle.send_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_to_persist_the_delivery_task_prevents_sending(ready, monkeypatch):
    monkeypatch.setattr(
        auto_reply,
        "save_agent_conversation",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_failure_to_reserve_delivery_keeps_a_manual_draft(ready, monkeypatch):
    monkeypatch.setattr(
        auto_reply,
        "claim_auto_approved_send",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    assert (await run(ready)).pending
    handle.send_reply.assert_not_called()


@pytest.mark.asyncio
async def test_failure_to_record_success_cannot_leave_a_duplicate_send_card(ready, monkeypatch):
    save = auto_reply.save_agent_conversation
    calls = 0

    async def fail_second_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database unavailable after send")
        return await save(*args, **kwargs)

    monkeypatch.setattr(auto_reply, "save_agent_conversation", fail_second_save)
    assert (await run(ready)).pending
    handle.send_reply.assert_awaited_once()
    _, messages = await stored(ready)
    assert extract_pending_approval_from_messages(messages)[0]["tool_name"] == handle.ASK_TEAM
