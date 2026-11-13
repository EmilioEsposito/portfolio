"""The code around the lead agent: where a reply may go, what a person is shown,
and what an approval sends. Uses the test database; no Gmail and no model."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from pydantic import TypeAdapter
from sqlalchemy import delete

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_agent_conversation,
    get_conversation_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.zillow_leads import handle, unit_data
from api.src.sernia_ai.zillow_leads.agent import LeadOutcome
from api.src.sernia_ai.zillow_leads.case import BookedTour, Listing, OperatingData, TourSlot
from api.src.sernia_ai.zillow_leads.thread import LeadMessage, LeadThread
from api.src.sernia_ai.zillow_leads.tours import Tours

RELAY = "abc123@convo.zillow.com"
_OUTCOME = TypeAdapter(LeadOutcome)
START = datetime(2026, 5, 4, 13, 0, tzinfo=UTC)


def message(minutes: int, *, sernia: bool = False, reply_to: str | None = RELAY, **fields):
    defaults = {
        "message_id": f"m{minutes}",
        "rfc_message_id": f"<m{minutes}@x>",
        "sent_at": START + timedelta(minutes=minutes),
        "from_address": "all@serniacapital.com" if sernia else "noreply@mail.zillow.com",
        "reply_to": None if sernia else reply_to,
        "from_sernia": sernia,
        "subject": "12 Example St inquiry",
        "body": "Is it available?",
    }
    return LeadMessage(**{**defaults, **fields})


def thread(*messages: LeadMessage) -> LeadThread:
    return LeadThread(mailbox="all@serniacapital.com", thread_id="T", messages=list(messages))


def outcome(action: str = "reply", **fields):
    base: dict = {"lead": {}, "tour": {}, "action": action, "reason": "Because."}
    if action == "reply":
        base["reply_body"] = "Hello,\n\nSernia Capital Team"
    if action == "escalate":
        base["question"] = "Is 12-C listed?"
    return _OUTCOME.validate_python({**base, **fields})


class TestReplyRoute:
    def test_a_reply_goes_to_the_zillow_relay_of_the_latest_lead_message(self):
        t = thread(
            message(0, reply_to="old@convo.zillow.com"),
            message(10, sernia=True),
            message(20, reply_to=RELAY, subject="Re: 12 Example St inquiry"),
        )

        route = handle.reply_route(t)

        assert route == handle.ReplyRoute(
            to=RELAY, subject="Re: 12 Example St inquiry", reply_to_message_id="m20"
        )
        assert handle.reply_route(thread(message(0))).subject == "Re: 12 Example St inquiry"

    @pytest.mark.parametrize(
        "address",
        [
            "someone@gmail.com",  # a lead's own mailbox, not the relay
            "all@serniacapital.com",  # one of ours
            "no-reply@zillow.com",
            "attacker@notzillow.com",  # ends with the name, is not the domain
            "",
        ],
    )
    def test_any_other_address_is_refused(self, address):
        t = thread(message(0, reply_to=None, from_address=address))
        assert handle.reply_route(t) is None

    def test_a_thread_with_no_lead_message_has_nowhere_to_reply(self):
        assert handle.reply_route(thread(message(0, sernia=True))) is None


class TestConversationFor:
    route = handle.ReplyRoute(to=RELAY, subject="Re: Hi", reply_to_message_id="m0")

    def pending(self, result):
        return extract_pending_approval_from_messages(
            handle.conversation_for("the case", result, self.route)
        )

    def test_a_reply_waits_for_a_person_with_its_recipient_shown(self):
        (card,) = self.pending(outcome())
        assert card["tool_name"] == handle.SEND_REPLY
        assert card["args"]["to"] == RELAY and card["args"]["subject"] == "Re: Hi"
        assert card["args"]["body"] == "Hello,\n\nSernia Capital Team"

    def test_an_escalation_waits_for_a_person_with_its_question(self):
        (card,) = self.pending(outcome("escalate"))
        assert card["tool_name"] == handle.ASK_TEAM
        assert card["args"]["question"] == "Is 12-C listed?"

    def test_no_reply_leaves_nothing_to_decide(self):
        assert self.pending(outcome("no_reply")) == []

    def test_a_reply_with_nowhere_to_go_becomes_an_escalation(self):
        messages = handle.conversation_for("the case", outcome(), None)
        (card,) = extract_pending_approval_from_messages(messages)
        assert card["tool_name"] == handle.ASK_TEAM
        assert "no Zillow address" in card["args"]["question"]


@pytest_asyncio.fixture
async def conversation_id(monkeypatch):
    """A fresh conversation ID, with Gmail, the model and push stubbed out."""
    cid = f"test-lead-{uuid.uuid4().hex}"
    monkeypatch.setattr(unit_data, "load", AsyncMock(return_value=unit_data.Loaded()))
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    yield cid
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id == cid))
        await session.commit()


async def stored(conversation_id: str):
    async with AsyncSessionFactory() as session:
        row = await get_agent_conversation(session, conversation_id, retries=1)
        messages = await get_conversation_messages(conversation_id, None, session=session)
    return row, messages


class TestHandleThread:
    @pytest.mark.asyncio
    async def test_a_reply_is_saved_for_approval_and_nothing_is_sent(
        self, conversation_id, monkeypatch
    ):
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
        monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome()))

        handled = await handle.handle_thread(
            "T", conversation_id=conversation_id, metadata={"trigger_source": "zillow_email_event"}
        )

        assert handled == handle.Handled(persisted=True, action="reply", pending=True)
        handle.send_reply.assert_not_called()
        row, messages = await stored(conversation_id)
        assert handle.is_lead_conversation(row.metadata_)
        assert row.metadata_["trigger_source"] == "zillow_email_event"
        assert row.metadata_["reply_route"] == {
            "to": RELAY,
            "subject": "Re: 12 Example St inquiry",
            "reply_to_message_id": "m0",
        }
        assert row.metadata_["outcome"]["action"] == "reply"
        (card,) = extract_pending_approval_from_messages(messages)
        assert card["tool_name"] == handle.SEND_REPLY
        handle.notify_pending_approval.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_thread_someone_already_answered_is_left_alone(
        self, conversation_id, monkeypatch
    ):
        answered = thread(message(0), message(5, sernia=True))
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=answered))
        monkeypatch.setattr(handle, "respond", AsyncMock())

        handled = await handle.handle_thread("T", conversation_id=conversation_id, metadata={})

        assert handled == handle.Handled(persisted=True, action="skipped")
        handle.respond.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_missing_thread_is_reported_so_the_queue_retries(
        self, conversation_id, monkeypatch
    ):
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=None))
        assert await handle.handle_thread("T", conversation_id=conversation_id, metadata={}) is None


class TestUnitData:
    """What the case is built from, and what unusable data does. Unit data is invented."""

    listing = Listing(unit="12-A", address="12 Example St #A", rent=1500, facts="Dishwasher")

    async def run(self, conversation_id, monkeypatch, loaded, result=None):
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
        monkeypatch.setattr(unit_data, "load", AsyncMock(return_value=loaded))
        monkeypatch.setattr(handle, "respond", AsyncMock(return_value=result or outcome()))
        return await handle.handle_thread("T", conversation_id=conversation_id, metadata={})

    @pytest.mark.asyncio
    async def test_the_case_is_built_from_the_data_on_file(self, conversation_id, monkeypatch):
        loaded = unit_data.Loaded(data=OperatingData(listings=[self.listing]))

        await self.run(conversation_id, monkeypatch, loaded)

        (case,) = handle.respond.call_args.args
        assert case.listings == [self.listing]
        # Until the booking system exists, nothing knows any tour times.
        assert case.offerable_slots == [] and case.booked_tours == []

    @pytest.mark.asyncio
    async def test_tour_times_and_booked_tours_come_from_the_tour_source(
        self, conversation_id, monkeypatch
    ):
        tomorrow = (datetime.now(UTC) + timedelta(days=2)).replace(
            hour=16, minute=0, second=0, microsecond=0
        )  # noon or 11 AM Eastern: inside the showing window
        source = SimpleNamespace(
            tours=AsyncMock(
                return_value=Tours(
                    open_times=[TourSlot(start=tomorrow, agent="Alex Agent")],
                    booked=[BookedTour(unit="12-A", start=tomorrow)],
                )
            )
        )
        monkeypatch.setattr(handle, "TOUR_SOURCE", source)
        loaded = unit_data.Loaded(data=OperatingData(listings=[self.listing]))

        await self.run(conversation_id, monkeypatch, loaded)

        (case,) = handle.respond.call_args.args
        assert [slot.start for slot in case.offerable_slots] == [tomorrow]
        assert case.booked_tours == [BookedTour(unit="12-A", start=tomorrow)]
        assert source.tours.call_args.args[0] == loaded.data

    @pytest.mark.asyncio
    async def test_a_tour_source_that_fails_does_not_stop_the_lead(
        self, conversation_id, monkeypatch
    ):
        source = SimpleNamespace(tours=AsyncMock(side_effect=RuntimeError("calendar not found")))
        monkeypatch.setattr(handle, "TOUR_SOURCE", source)
        loaded = unit_data.Loaded(data=OperatingData(listings=[self.listing]))

        handled = await self.run(conversation_id, monkeypatch, loaded)

        assert handled.action == "reply"
        (case,) = handle.respond.call_args.args
        assert case.offerable_slots == []

    @pytest.mark.asyncio
    async def test_data_that_fails_its_checks_escalates_without_asking_the_agent(
        self, conversation_id, monkeypatch
    ):
        loaded = unit_data.Loaded(problem="unit 12-A: rent must be more than 0")

        handled = await self.run(conversation_id, monkeypatch, loaded)

        assert handled == handle.Handled(persisted=True, action="escalate", pending=True)
        handle.respond.assert_not_called()
        handle.send_reply.assert_not_called()
        _, messages = await stored(conversation_id)
        (card,) = extract_pending_approval_from_messages(messages)
        assert card["tool_name"] == handle.ASK_TEAM
        assert "unit 12-A: rent must be more than 0" in card["args"]["question"]
        assert "rental_units" in card["args"]["question"]

    @pytest.mark.asyncio
    async def test_with_no_units_the_agent_still_decides_and_sees_no_listings(
        self, conversation_id, monkeypatch
    ):
        handled = await self.run(
            conversation_id, monkeypatch, unit_data.Loaded(), outcome("escalate")
        )

        assert handled.action == "escalate"
        (case,) = handle.respond.call_args.args
        assert case.listings == []


class TestApplyApproval:
    async def pending_reply(self, conversation_id, monkeypatch, result=None):
        monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
        monkeypatch.setattr(handle, "respond", AsyncMock(return_value=result or outcome()))
        await handle.handle_thread("T", conversation_id=conversation_id, metadata={})
        row, messages = await stored(conversation_id)
        (card,) = extract_pending_approval_from_messages(messages)
        return row, card

    async def decide(self, conversation_id, row, decision):
        async with AsyncSessionFactory() as session:
            return await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[decision],
                approver="Pat",
            )

    @pytest.mark.asyncio
    async def test_an_approved_reply_is_sent_as_drafted_to_the_stored_route(
        self, conversation_id, monkeypatch
    ):
        row, card = await self.pending_reply(conversation_id, monkeypatch)

        result = await self.decide(
            conversation_id, row, ApprovalDecision(card["tool_call_id"], approved=True)
        )

        handle.send_reply.assert_awaited_once_with(
            row.metadata_["reply_route"], "Hello,\n\nSernia Capital Team"
        )
        assert result["status"] == "completed" and result["pending"] == []
        row, messages = await stored(conversation_id)
        assert extract_pending_approval_from_messages(messages) == []
        (record,) = row.metadata_["approval"]
        assert (record["approved"], record["edited"], record["by"]) == (True, False, "Pat")

    @pytest.mark.asyncio
    async def test_an_approver_can_change_the_words_but_not_the_recipient(
        self, conversation_id, monkeypatch
    ):
        row, card = await self.pending_reply(conversation_id, monkeypatch)

        await self.decide(
            conversation_id,
            row,
            ApprovalDecision(
                card["tool_call_id"],
                approved=True,
                override_args={"body": "Edited.", "to": "someone@else.com", "subject": "Hi"},
            ),
        )

        route, body = handle.send_reply.await_args.args
        assert body == "Edited."
        assert route["to"] == RELAY and route["subject"] == "Re: 12 Example St inquiry"
        row, _ = await stored(conversation_id)
        assert row.metadata_["approval"][0]["edited"] is True
        assert row.metadata_["approval"][0]["sent_body"] == "Edited."

    @pytest.mark.asyncio
    async def test_a_rejected_reply_is_not_sent(self, conversation_id, monkeypatch):
        row, card = await self.pending_reply(conversation_id, monkeypatch)

        result = await self.decide(
            conversation_id,
            row,
            ApprovalDecision(card["tool_call_id"], approved=False, denial_reason="Wrong unit."),
        )

        handle.send_reply.assert_not_called()
        assert "Wrong unit." in result["output"]
        _, messages = await stored(conversation_id)
        assert extract_pending_approval_from_messages(messages) == []

    @pytest.mark.asyncio
    async def test_a_reply_cannot_be_sent_twice(self, conversation_id, monkeypatch):
        row, card = await self.pending_reply(conversation_id, monkeypatch)
        approve = ApprovalDecision(card["tool_call_id"], approved=True)
        await self.decide(conversation_id, row, approve)

        with pytest.raises(ValueError, match="Nothing is waiting"):
            await self.decide(conversation_id, row, approve)

        assert handle.send_reply.await_count == 1

    @pytest.mark.asyncio
    async def test_two_approvals_at_once_send_one_reply(self, conversation_id, monkeypatch):
        row, card = await self.pending_reply(conversation_id, monkeypatch)
        approve = ApprovalDecision(card["tool_call_id"], approved=True)

        async def slow_send(route, body):
            await asyncio.sleep(0.3)  # long enough for the other request to arrive
            return "Email sent."

        handle.send_reply.side_effect = slow_send

        async def request():
            # As the endpoint does: load the conversation, then apply the decision.
            async with AsyncSessionFactory() as session:
                loaded = await get_agent_conversation(session, conversation_id)
                return await handle.apply_approval(
                    session,
                    conversation_id=conversation_id,
                    metadata=loaded.metadata_,
                    decisions=[approve],
                    approver="Pat",
                )

        results = await asyncio.gather(request(), request(), return_exceptions=True)

        assert handle.send_reply.await_count == 1
        refused = [item for item in results if isinstance(item, Exception)]
        assert len(refused) == 1 and "Nothing is waiting" in str(refused[0])
        row, _ = await stored(conversation_id)
        assert len(row.metadata_["approval"]) == 1

    @pytest.mark.asyncio
    async def test_the_same_decision_twice_in_one_request_sends_once(
        self, conversation_id, monkeypatch
    ):
        row, card = await self.pending_reply(conversation_id, monkeypatch)
        approve = ApprovalDecision(card["tool_call_id"], approved=True)

        async with AsyncSessionFactory() as session:
            await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[approve, approve],
                approver="Pat",
            )

        assert handle.send_reply.await_count == 1
        row, _ = await stored(conversation_id)
        assert len(row.metadata_["approval"]) == 1

    @pytest.mark.asyncio
    async def test_acknowledging_an_escalation_sends_nothing(self, conversation_id, monkeypatch):
        row, card = await self.pending_reply(conversation_id, monkeypatch, outcome("escalate"))
        assert card["tool_name"] == handle.ASK_TEAM

        result = await self.decide(
            conversation_id, row, ApprovalDecision(card["tool_call_id"], approved=True)
        )

        handle.send_reply.assert_not_called()
        assert result["output"] == "Acknowledged."


def test_only_lead_conversations_are_marked():
    assert handle.is_lead_conversation({"handler": handle.HANDLER})
    assert not handle.is_lead_conversation({"trigger_source": "zillow_email_event"})
    assert not handle.is_lead_conversation(None)


class TestRoutes:
    """The two places the general agent's endpoints must step aside."""

    def lead_conversation(self):
        return SimpleNamespace(
            metadata_={"handler": handle.HANDLER, "reply_route": {"to": RELAY}}, modality="web_chat"
        )

    def user(self):
        return SimpleNamespace(id="user-1", first_name="Pat", last_name="Doe", email_addresses=[])

    @pytest.mark.asyncio
    async def test_a_lead_conversation_cannot_be_continued_with_the_general_agent(
        self, monkeypatch
    ):
        from fastapi import HTTPException

        from api.src.sernia_ai import routes

        monkeypatch.setattr(
            routes, "get_agent_conversation", AsyncMock(return_value=self.lead_conversation())
        )
        history = AsyncMock(return_value=[])
        monkeypatch.setattr(routes, "get_conversation_messages", history)
        request = MagicMock()
        monkeypatch.setattr(
            routes,
            "read_chat_request",
            AsyncMock(return_value={"id": "c1", "messages": [{"role": "user"}]}),
        )

        with pytest.raises(HTTPException) as refused:
            await routes.chat_sernia(request=request, user=self.user(), session=MagicMock())

        assert refused.value.status_code == 409
        history.assert_not_called()  # the lead's words were never loaded for that agent

    @pytest.mark.asyncio
    async def test_approving_a_lead_conversation_runs_code_not_an_agent(self, monkeypatch):
        from api.src.sernia_ai import routes

        monkeypatch.setattr(
            routes, "get_agent_conversation", AsyncMock(return_value=self.lead_conversation())
        )
        applied = AsyncMock(return_value={"status": "completed"})
        resumed = AsyncMock()
        monkeypatch.setattr(routes, "apply_lead_approval", applied)
        monkeypatch.setattr(routes, "resume_with_approvals", resumed)
        body = routes.ApprovalRequest(decisions=[{"tool_call_id": "lead-1", "approved": True}])

        result = await routes.approve_conversation(
            "c1", body, user=self.user(), session=MagicMock()
        )

        assert result == {"status": "completed"}
        resumed.assert_not_called()
        kwargs = applied.await_args.kwargs
        assert kwargs["conversation_id"] == "c1"
        assert [d.tool_call_id for d in kwargs["decisions"]] == ["lead-1"]
