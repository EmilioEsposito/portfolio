"""A lead's email goes out only from production.

A preview's database is a copy of production's, so its lead threads are real
people's. Outside production an approved reply is captured: kept on its
conversation, reported as captured, and Gmail is never called; its booking
settles as capture mode settles a captured notice. Production still sends.

The rule is the booking guard's (``guard.may_reach``), so the two never
disagree. Uses the test database; Gmail is a mock that fails the test if it is
reached where it must not be. Names, numbers and threads are made up.
"""

# The ``world`` and ``lead`` fixtures are imported, so each test's parameter of
# the same name is that fixture, not a redefinition.
# ruff: noqa: F811

import itertools
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import guard
from api.src.showing_bookings.settings import ShowingBookingSettings
from api.src.tests.showing_public import rows
from api.src.tests.test_zillow_lead_bookings import (  # noqa: F401  (fixtures)
    lead,
    world,
)
from api.src.tests.test_zillow_lead_bookings import (
    outcome as booking_outcome,
)
from api.src.tests.test_zillow_lead_bookings import (
    stored as booking_stored,
)
from api.src.tests.test_zillow_lead_handle import RELAY, message, outcome, stored, thread
from api.src.zillow_leads import handle, listing_data

PREVIEW = "portfolio-pr-123"
ROUTE = {"to": RELAY, "subject": "Re: 12 Example St inquiry", "reply_to_message_id": "m0"}
# The real send, kept before any fixture replaces it with a stand-in.
SEND_REPLY = handle.send_reply


def gmail(monkeypatch, *, reachable: bool) -> AsyncMock:
    """Gmail's send, mocked. Unreachable: any call to Gmail fails the test."""
    refuse = AssertionError("Gmail was reached outside production")
    if reachable:
        send = AsyncMock(return_value={"id": "gmail-1"})
        headers = AsyncMock(return_value={"thread_id": "T", "in_reply_to": "<m0@x>"})
        credentials = lambda **kwargs: "credentials"  # noqa: E731
    else:
        send = AsyncMock(side_effect=refuse)
        headers = AsyncMock(side_effect=refuse)

        def credentials(**kwargs):
            raise refuse

    monkeypatch.setattr(handle, "_send_email", send)
    monkeypatch.setattr(handle, "_get_threading_headers", headers)
    monkeypatch.setattr(handle, "get_delegated_credentials", credentials)
    return send


def environment(monkeypatch, name: str | None) -> None:
    if name is None:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    else:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", name)
    monkeypatch.delenv("SHOWING_TEST_EMAILS", raising=False)
    monkeypatch.delenv("SHOWING_TEST_PHONES", raising=False)


# ---------------------------------------------------------------------------
# One rule, shared with the booking guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("where", "addresses", "test_list", "reaches"),
    [
        ("production", [RELAY], frozenset(), True),
        (PREVIEW, [RELAY], frozenset(), False),
        ("", [RELAY], frozenset(), False),
        (PREVIEW, [RELAY], frozenset({RELAY}), True),
        (PREVIEW, [RELAY, "+14125550100"], frozenset({RELAY}), False),
        (PREVIEW, [], frozenset(), False),
    ],
)
def test_a_message_reaches_anyone_only_from_production(where, addresses, test_list, reaches):
    assert guard.may_reach(addresses, environment=where, test_recipients=test_list) is reaches


def test_the_booking_guard_never_sends_where_a_lead_email_would_be_captured():
    """Whatever the settings row says, ``decide`` sends only where ``may_reach`` allows."""
    test_list = frozenset({"+14125550100", "test@example.com"})
    for mode, all_live, approved, where, addresses in itertools.product(
        ("capture", "test", "live"),
        (False, True),
        (False, True),
        ("production", PREVIEW, ""),
        (["+14125550100"], ["+14125550199"], [RELAY], ["test@example.com", RELAY]),
    ):
        decision = guard.decide(
            settings=ShowingBookingSettings(send_mode=mode, live_all_units=all_live),
            environment=where,
            unit="12-A",
            addresses=addresses,
            test_recipients=test_list,
            approved=approved,
        )
        if decision.action == "send":
            assert guard.may_reach(addresses, environment=where, test_recipients=test_list)


# ---------------------------------------------------------------------------
# The send itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("where", "label"), [(PREVIEW, "preview"), (None, "local")])
async def test_outside_production_a_reply_is_captured_and_gmail_is_never_called(
    monkeypatch, where, label
):
    environment(monkeypatch, where)
    send = gmail(monkeypatch, reachable=False)

    delivery = await SEND_REPLY(ROUTE, "Hello")

    assert delivery == handle.Delivery(
        f"Captured, not sent ({label}): no email went to {RELAY}.", captured=True
    )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_outside_production_the_gmail_call_itself_refuses(monkeypatch):
    environment(monkeypatch, PREVIEW)
    send = gmail(monkeypatch, reachable=False)

    with pytest.raises(handle.NotProduction):
        await handle.send_thread_email(ROUTE, "Hello", threading={"thread_id": "T"})

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_outside_production_an_address_on_the_test_list_still_goes(monkeypatch):
    """As the booking guard sends to the test list, so does a lead reply."""
    environment(monkeypatch, PREVIEW)
    monkeypatch.setenv("SHOWING_TEST_EMAILS", RELAY)
    send = gmail(monkeypatch, reachable=True)

    delivery = await SEND_REPLY(ROUTE, "Hello")

    assert not delivery.captured
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_in_production_a_reply_is_sent(monkeypatch):
    environment(monkeypatch, "production")
    send = gmail(monkeypatch, reachable=True)

    delivery = await SEND_REPLY(ROUTE, "Hello")

    assert delivery == handle.Delivery(f"Email sent to {RELAY} (message ID: gmail-1).")
    send.assert_awaited_once()
    assert send.await_args.kwargs["to"] == RELAY


# ---------------------------------------------------------------------------
# Approving a reply
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conversation_id(monkeypatch):
    """A conversation with the model and push stubbed; ``send_reply`` is the real one."""
    cid = f"test-lead-{uuid.uuid4().hex}"
    monkeypatch.setattr(listing_data, "load", AsyncMock(return_value=listing_data.Loaded()))
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome()))
    yield cid
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id == cid))
        await session.commit()


async def approve(conversation_id: str, *, body: str | None = None) -> dict:
    await handle.handle_thread("T", conversation_id=conversation_id, metadata={})
    row, messages = await stored(conversation_id)
    (card,) = extract_pending_approval_from_messages(messages)
    override = {"body": body} if body is not None else None
    async with AsyncSessionFactory() as session:
        return await handle.apply_approval(
            session,
            conversation_id=conversation_id,
            metadata=row.metadata_,
            decisions=[
                ApprovalDecision(card["tool_call_id"], approved=True, override_args=override)
            ],
            approver="Pat",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, "Edited words."])
async def test_a_preview_approval_sends_nothing_and_says_it_was_captured(
    conversation_id, monkeypatch, body
):
    environment(monkeypatch, PREVIEW)
    send = gmail(monkeypatch, reachable=False)

    result = await approve(conversation_id, body=body)

    send.assert_not_awaited()
    line = f"Captured, not sent (preview): no email went to {RELAY}."
    assert result["status"] == "completed" and result["pending"] == []
    assert result["output"] == line
    row, messages = await stored(conversation_id)
    assert extract_pending_approval_from_messages(messages) == []
    assert line in str(messages[-1].parts[0].content)
    (record,) = row.metadata_["approval"]
    assert record["captured"] is True and record["approved"] is True
    assert record["sent_body"] == (body or "Hello,\n\nSernia Capital Team")


@pytest.mark.asyncio
async def test_a_production_approval_still_sends(conversation_id, monkeypatch):
    environment(monkeypatch, "production")
    send = gmail(monkeypatch, reachable=True)

    result = await approve(conversation_id)

    send.assert_awaited_once()
    assert send.await_args.kwargs["to"] == RELAY
    assert send.await_args.kwargs["message_text"] == "Hello,\n\nSernia Capital Team"
    assert result["output"] == f"Email sent to {RELAY} (message ID: gmail-1)."
    row, _ = await stored(conversation_id)
    (record,) = row.metadata_["approval"]
    assert "captured" not in record


# ---------------------------------------------------------------------------
# The booking a captured reply confirms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_captured_confirmation_books_as_capture_mode_does(lead, monkeypatch):
    """The booking stands, as for a sent reply, and its held notices are captured in
    turn: nothing reaches the lead, the host or the residents."""
    monkeypatch.setattr(handle, "send_reply", SEND_REPLY)
    send = gmail(monkeypatch, reachable=False)
    conversation_id = await lead.decide(booking_outcome())

    result = await lead.decide_on(conversation_id)

    send.assert_not_awaited()
    assert result["output"].startswith("Captured, not sent (local):")
    assert "Showing booked with this reply" in result["output"]
    _, (jordan,), _ = await rows()
    assert jordan.status == "booked" and jordan.lead_reply_sent_at is not None
    row, _ = await booking_stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "sent"
    assert row.metadata_["approval"][0]["captured"] is True

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))

    _, _, notices = await rows()
    assert sorted((n.kind, n.status) for n in notices) == [
        ("host_event", "captured"),
        ("resident_new_showing", "captured"),
    ]
    assert lead.world.texts.sent == []
