"""An approved reply's booking links go out with the thread's own token.

``reply_links.with_token`` on its own, then through ``handle.apply_approval``
with Gmail stubbed: a draft or an edit that links a unit's booking page without
the thread's token, or with another thread's, is sent with this thread's. The
automatic send is not touched. Every name, link and token here is made up.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.tests.test_zillow_lead_handle import message, outcome, thread
from api.src.zillow_leads import handle, lead_links, listing_data, reply_links
from api.src.zillow_leads.models import ZillowLeadLink

BASE = "https://homes.example.com"
TOKEN = "A" * 22
OTHER = "B" * 22


@pytest.mark.parametrize(
    "body, expected, changed",
    [
        # The unit's plain page gets the token.
        (f"Book here: {BASE}/showings/12-3", f"Book here: {BASE}/showings/12-3?t={TOKEN}", 1),
        # Another thread's token is replaced; another parameter is kept.
        (
            f"{BASE}/showings/12-3?t={OTHER}&utm=x and {BASE}/showings/40-1",
            f"{BASE}/showings/12-3?utm=x&t={TOKEN} and {BASE}/showings/40-1?t={TOKEN}",
            2,
        ),
        # Already right: left exactly as written.
        (f"{BASE}/showings/12-3?t={TOKEN}", f"{BASE}/showings/12-3?t={TOKEN}", 0),
        # Written without the scheme, or with www., at the end of a sentence.
        (
            "Book at homes.example.com/showings/12-3. Thanks!",
            f"Book at {BASE}/showings/12-3?t={TOKEN}. Thanks!",
            1,
        ),
        (
            f"(https://www.homes.example.com/showings/12-3?t={OTHER}).",
            f"({BASE}/showings/12-3?t={TOKEN}).",
            1,
        ),
    ],
)
def test_every_link_to_a_units_page_carries_the_threads_token(body, expected, changed):
    assert reply_links.with_token(body, TOKEN, BASE) == (expected, changed)


@pytest.mark.parametrize(
    "body",
    [
        # The cancel page, the list of units, and other sites are not booking pages.
        f"Cancel here: {BASE}/showings/cancel/abcdef1234567890",
        f"All units: {BASE}/showings and {BASE}/showings/",
        "https://example.org/showings/12-3",
        "https://nothomes.example.com/showings/12-3",
        "https://homes.example.com.example.org/showings/12-3",
        "Call 412-555-0123.",
    ],
)
def test_other_links_are_left_alone(body):
    assert reply_links.with_token(body, TOKEN, BASE) == (body, 0)


@pytest_asyncio.fixture
async def lead(monkeypatch):
    """A lead conversation with a draft waiting, saved by the lead agent's own code."""
    monkeypatch.setenv("SHOWING_PUBLIC_BASE_URL", BASE)
    monkeypatch.setattr(listing_data, "load", AsyncMock(return_value=listing_data.Loaded()))
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    thread_id = f"t{uuid.uuid4().hex[:15]}"
    cid = f"test-links-{uuid.uuid4().hex}"
    made = []

    async def draft(body: str) -> str:
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
        monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome(reply_body=body)))
        await handle.handle_thread(thread_id, conversation_id=cid, metadata={})
        made.append(cid)
        return thread_id

    yield draft, cid
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id.in_(made)))
        await session.execute(delete(ZillowLeadLink).where(ZillowLeadLink.thread_id == thread_id))
        await session.commit()


async def approve(cid: str, body: str | None = None) -> dict:
    async with AsyncSessionFactory() as session:
        messages = await get_conversation_messages(cid, None, session=session)
        (card,) = extract_pending_approval_from_messages(messages)
        row = await session.get(AgentConversation, cid)
        return await handle.apply_approval(
            session,
            conversation_id=cid,
            metadata=row.metadata_,
            decisions=[
                ApprovalDecision(
                    tool_call_id=card["tool_call_id"],
                    approved=True,
                    override_args={"body": body} if body else None,
                )
            ],
            approver="Pat Doe",
        )


async def stored_approval(cid: str) -> dict:
    async with AsyncSessionFactory() as session:
        row = await session.get(AgentConversation, cid)
        (record,) = row.metadata_["approval"]
        return record


@pytest.mark.asyncio
async def test_an_unedited_draft_with_the_plain_page_is_sent_with_the_token(lead):
    draft, cid = lead
    thread_id = await draft(f"Hi,\n\nBook a time here: {BASE}/showings/12-3\n\nSernia Capital Team")

    result = await approve(cid)

    token = await lead_links.token_for_thread(thread_id)
    sent = handle.send_reply.await_args.args[1]
    assert sent == f"Hi,\n\nBook a time here: {BASE}/showings/12-3?t={token}\n\nSernia Capital Team"
    assert handle.LINKS_FIXED in result["output"]
    record = await stored_approval(cid)
    # What was sent is recorded; the person did not edit the draft.
    assert record["sent_body"] == sent and record["edited"] is False
    assert record["booking_links_fixed"] == 1


@pytest.mark.asyncio
async def test_an_edit_with_another_leads_link_is_sent_with_this_threads(lead):
    draft, cid = lead
    thread_id = await draft("Hi,\n\nThanks.\n\nSernia Capital Team")

    await approve(cid, body=f"Hi,\n\nBook here: {BASE}/showings/12-3?t={OTHER}\n\nSernia")

    token = await lead_links.token_for_thread(thread_id)
    assert token != OTHER
    assert handle.send_reply.await_args.args[1] == (
        f"Hi,\n\nBook here: {BASE}/showings/12-3?t={token}\n\nSernia"
    )
    record = await stored_approval(cid)
    assert record["edited"] is True and record["booking_links_fixed"] == 1


@pytest.mark.asyncio
async def test_a_reply_with_no_booking_link_is_sent_as_written_and_makes_no_token(lead):
    draft, cid = lead
    thread_id = await draft("Hi,\n\nYes, it is available.\n\nSernia Capital Team")

    result = await approve(cid)

    assert (
        handle.send_reply.await_args.args[1]
        == "Hi,\n\nYes, it is available.\n\nSernia Capital Team"
    )
    assert handle.LINKS_FIXED not in result["output"]
    assert "booking_links_fixed" not in await stored_approval(cid)
    async with AsyncSessionFactory() as session:
        assert (
            await session.scalar(
                select(ZillowLeadLink.token).where(ZillowLeadLink.thread_id == thread_id)
            )
        ) is None
