"""Showings agreed in a lead's email thread: booked, moved and cancelled from the
lead agent's decisions, and completed by the lead through their own link.

Everything here runs through ``handle.handle_thread``, ``handle.apply_approval``,
``auto_reply.maybe_send_reply`` and the public routes, against the test
database, with the booking stand-ins of ``showing_public.py`` (an in-memory
calendar, a recording Quo, made-up residents) and the model, Gmail and push
mocked. The clock is the stand-in world's. Names, numbers and threads are made up.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httplib2
import pytest
import pytest_asyncio
from fastapi import HTTPException
from googleapiclient.errors import HttpError
from pydantic import TypeAdapter
from sqlalchemy import delete, select

from api.src.agent_conversations.delivery import AutoApprovedSend
from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_agent_conversation,
    get_conversation_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import lead_bookings, open_times
from api.src.showing_bookings.models import ShowingAttendee, ShowingNotice
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
)
from api.src.zillow_leads import auto_reply, handle, lead_links
from api.src.zillow_leads import listing_data as listing_data_module
from api.src.zillow_leads.agent import LeadOutcome
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.models import ZillowLeadLink
from api.src.zillow_leads.settings import LeadSettings
from api.src.zillow_leads.thread import LeadMessage, LeadThread

PUBLIC = "/api/showing-bookings/public"
THREAD = "thread-jordan-0101"
OTHER_THREAD = "thread-casey-0102"
JORDAN_MOBILE = "+14125550123"
RESIDENT = "+14125550171"
_OUTCOME = TypeAdapter(LeadOutcome)


def outcome(action: str = "reply", *, status: str = "confirmed", start=None, **fields):
    """A decision about unit 320-03; confirmed for Wednesday at 4 PM unless told otherwise."""
    lead = {"name": "Jordan Lee", "phone": "412-555-0123", "unit": UNIT}
    lead.update(fields.pop("lead", {}))
    tour = {"status": status, "start": (start or at(16)).isoformat(), "virtual": False}
    tour.update(fields.pop("tour", {}))
    base = {"lead": lead, "tour": tour, "action": action, "reason": "Test."}
    if action == "reply":
        base["reply_body"] = "See you then.\n\nSernia Capital Team"
    if action == "escalate":
        base["question"] = "Is this right?"
    return _OUTCOME.validate_python({**base, **fields})


def lead_thread(thread_id: str, minutes: int = 10) -> LeadThread:
    return LeadThread(
        mailbox="all@serniacapital.com",
        thread_id=thread_id,
        messages=[
            LeadMessage(
                message_id=f"{thread_id}-m{minutes}",
                rfc_message_id=f"<{thread_id}-m{minutes}@example.com>",
                sent_at=PublicWorld().now - timedelta(minutes=minutes),
                from_address="noreply@mail.zillow.com",
                reply_to=f"{thread_id}@convo.zillow.com",
                from_sernia=False,
                subject="320 Example St inquiry",
                body="Wednesday at 4 works for me.",
            )
        ],
    )


def gmail_refused() -> HTTPException:
    """What ``send_email`` raises when Gmail answers 400: a 500 wrapping the HttpError."""
    try:
        try:
            raise HttpError(httplib2.Response({"status": 400}), b"invalid to header")
        except HttpError as error:
            raise HTTPException(status_code=500, detail=f"Failed to send email: {error}")
    except HTTPException as wrapped:
        return wrapped


class Lead:
    """Runs decisions through the lead agent's code with the world's clock."""

    def __init__(self, world: PublicWorld, monkeypatch) -> None:
        self.world = world
        self.monkeypatch = monkeypatch
        self.conversations: list[str] = []
        self.originals: list[str] = []

    async def decide(self, decided, thread_id: str = THREAD, *, auto: bool = False) -> str:
        """Handle a thread whose agent returns ``decided``; answers the conversation id."""
        mp = self.monkeypatch
        conversation_id = f"test-lead-{uuid.uuid4().hex}"
        self.conversations.append(conversation_id)
        original = f"<{conversation_id}@zillow.example>"
        self.originals.append(original)
        mp.setattr(handle, "respond", AsyncMock(return_value=decided))
        mp.setattr(handle, "load_lead_thread", AsyncMock(return_value=lead_thread(thread_id)))
        mp.setattr(
            handle,
            "_get_threading_headers",
            AsyncMock(return_value={"in_reply_to": original, "thread_id": "T"}),
        )
        mp.setattr(
            auto_reply,
            "effective_settings",
            AsyncMock(return_value=LeadSettings(require_approval=not auto)),
        )
        await handle.handle_thread(thread_id, conversation_id=conversation_id, metadata={})
        return conversation_id

    async def decide_on(self, conversation_id: str, approved: bool = True):
        row, messages = await stored(conversation_id)
        (card,) = extract_pending_approval_from_messages(messages)
        async with AsyncSessionFactory() as session:
            return await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=approved)],
                approver="Test",
            )


async def stored(conversation_id: str):
    async with AsyncSessionFactory() as session:
        row = await get_agent_conversation(session, conversation_id, retries=1)
        messages = await get_conversation_messages(conversation_id, None, session=session)
    return row, messages


async def pending_cards(conversation_id: str) -> list[dict]:
    _, messages = await stored(conversation_id)
    return extract_pending_approval_from_messages(messages)


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SHOWING_PUBLIC_BASE_URL", raising=False)
    open_times.forget_free_busy()
    await clear_showing_data()
    await _forget_links()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    state.residents.add("Morgan Ellis", RESIDENT, unit=UNIT)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await _forget_links()


async def _forget_links() -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(ZillowLeadLink).where(ZillowLeadLink.thread_id.like("thread-%"))
        )
        await session.commit()


@pytest_asyncio.fixture
async def lead(world, monkeypatch):
    """The lead agent's code around the world, with the booking switch on."""
    await store_settings(lead_agent_uses_open_times=True)
    clock = SimpleNamespace(now=lambda tz=None: world.now)
    monkeypatch.setattr(handle, "datetime", clock)
    monkeypatch.setattr(auto_reply, "datetime", clock)
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    monkeypatch.setattr(auto_reply, "is_sernia_ai_enabled", AsyncMock(return_value=True))
    loaded = listing_data_module.Loaded(data=as_listing_data([world.listing], world.rules))
    monkeypatch.setattr(listing_data_module, "load", AsyncMock(return_value=loaded))
    state = Lead(world, monkeypatch)
    yield state
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AgentConversation).where(AgentConversation.id.in_(state.conversations))
        )
        await session.execute(
            delete(AutoApprovedSend).where(
                AutoApprovedSend.conversation_id.in_(state.conversations)
            )
        )
        await session.commit()


async def switch_off() -> None:
    await store_settings(lead_agent_uses_open_times=False)


def booked(attendees):
    return [a for a in attendees if a.status == "booked"]


# ---------------------------------------------------------------------------
# With the switch off, nothing changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_off_an_approved_confirmation_books_nothing(lead):
    await switch_off()
    conversation_id = await lead.decide(outcome())

    result = await lead.decide_on(conversation_id)

    assert result["status"] == "completed"
    handle.send_reply.assert_awaited_once()
    showings, attendees, notices = await rows()
    assert (showings, attendees, notices) == ([], [], [])
    row, _ = await stored(conversation_id)
    assert "showing_booking" not in row.metadata_


@pytest.mark.asyncio
async def test_switch_off_an_automatic_confirmation_books_nothing(lead):
    await switch_off()
    conversation_id = await lead.decide(outcome(), auto=True)

    handle.send_reply.assert_awaited_once()
    assert await rows() == ([], [], [])
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert "showing_booking" not in row.metadata_


@pytest.mark.asyncio
async def test_switch_off_a_cancellation_cancels_nothing(lead):
    await lead.decide_on(await lead.decide(outcome()))
    await switch_off()

    await lead.decide(outcome("no_reply", status="cancelled"))
    await lead.decide_on(await lead.decide(outcome(status="cancelled")))
    await lead.decide_on(await lead.decide(outcome()), approved=False)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


# ---------------------------------------------------------------------------
# Booking a confirmed tour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_approved_confirmation_books_a_provisional_place(lead):
    conversation_id = await lead.decide(outcome())
    assert await rows() == ([], [], [])  # nothing until the reply is approved

    result = await lead.decide_on(conversation_id)

    handle.send_reply.assert_awaited_once()
    assert (
        "Showing booked with this reply: 320-03, Wednesday, April 14 at 4:00 PM."
        in (result["output"])
    )
    showings, attendees, notices = await rows()
    (showing,) = showings
    assert (showing.unit, showing.starts_at, showing.status) == (UNIT, at(16), "scheduled")
    (jordan,) = attendees
    assert jordan.source == "lead_agent"
    assert jordan.name == "Jordan Lee"
    assert jordan.zillow_thread_id == THREAD
    assert jordan.lead_conversation_id == conversation_id
    # The thread's number is saved for the host and never texted.
    assert jordan.mobile == JORDAN_MOBILE and not jordan.texts_accepted
    assert jordan.email is None and jordan.texts_consent_at is None
    # The host's event and the residents' text, due now; nothing to the lead.
    assert sorted((n.kind, n.status) for n in notices) == [
        ("host_event", "pending"),
        ("resident_new_showing", "pending"),
    ]
    assert all(n.not_before <= lead.world.now for n in notices)
    assert all(n.recipient != JORDAN_MOBILE for n in notices)
    row, _ = await stored(conversation_id)
    record = row.metadata_["showing_booking"]
    assert record["action"] == "booked" and record["sent"] == "sent"
    assert (record["showing_id"], record["attendee_id"]) == (showing.id, jordan.id)


@pytest.mark.asyncio
async def test_an_automatic_confirmation_books_before_it_sends(lead, monkeypatch):
    seen = {}

    async def send(*args, **kwargs):
        # Booked and held before Gmail is called: nothing can go out yet.
        _, attendees, notices = await rows()
        seen["attendees"] = len(attendees)
        seen["held_past_now"] = all(n.not_before > lead.world.now for n in notices)
        return "Email sent."

    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=send))
    conversation_id = await lead.decide(outcome(), auto=True)

    assert seen == {"attendees": 1, "held_past_now": True}
    _, attendees, notices = await rows()
    assert [a.source for a in attendees] == ["lead_agent"]
    assert sorted(n.kind for n in notices) == ["host_event", "resident_new_showing"]
    assert all(n.not_before <= lead.world.now for n in notices)  # released after the send
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert row.metadata_["showing_booking"]["action"] == "booked"


@pytest.mark.asyncio
async def test_the_residents_and_host_notices_go_out_with_the_job_and_nothing_to_the_lead(lead):
    await lead.decide_on(await lead.decide(outcome()))

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))

    _, _, notices = await rows()
    assert sorted((n.kind, n.status) for n in notices) == [
        ("host_event", "captured"),
        ("resident_new_showing", "captured"),
    ]
    assert lead.world.texts.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decided",
    [
        outcome(tour={"virtual": True}),
        outcome(tour={"start": None}),
        outcome(lead={"unit": "999-99"}),
        outcome(lead={"unit": None}),
        outcome(status="proposed"),
        outcome(status="none"),
    ],
    ids=["virtual", "no start", "unlisted unit", "no unit", "proposed", "none"],
)
async def test_what_a_confirmation_needs_or_nothing_is_booked(lead, decided):
    await lead.decide_on(await lead.decide(decided))

    handle.send_reply.assert_awaited_once()
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_rejected_reply_books_nothing(lead):
    conversation_id = await lead.decide(outcome())

    await lead.decide_on(conversation_id, approved=False)

    handle.send_reply.assert_not_called()
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_approving_twice_books_once(lead):
    conversation_id = await lead.decide(outcome())
    row, messages = await stored(conversation_id)
    (card,) = extract_pending_approval_from_messages(messages)
    await lead.decide_on(conversation_id)

    with pytest.raises(ValueError):
        async with AsyncSessionFactory() as session:
            await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[ApprovalDecision(card["tool_call_id"], approved=True)],
                approver="Test",
            )

    handle.send_reply.assert_awaited_once()
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_a_retried_automatic_send_books_once(lead):
    conversation_id = await lead.decide(outcome(), auto=True)
    row, _ = await stored(conversation_id)
    from api.src.zillow_leads.case import case_from_record

    await auto_reply.maybe_send_reply(
        conversation_id, case_from_record(row.metadata_["case"]), outcome()
    )

    handle.send_reply.assert_awaited_once()
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_a_thread_already_booked_through_its_link_books_nothing_new(lead):
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/bookings", json=booking(lead_token=token)
        )
    assert answer.status_code == 201
    _, before, notices_before = await rows()

    conversation_id = await lead.decide(outcome())
    await lead.decide_on(conversation_id)

    showings, attendees, notices = await rows()
    assert len(showings) == 1 and [a.id for a in attendees] == [before[0].id]
    assert len(notices) == len(notices_before)
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "already_booked"


# ---------------------------------------------------------------------------
# A refused time holds the reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_time_holds_an_approved_reply_with_the_reason(lead):
    lead.world.calendar.add_busy("jane.doe@example.com", at(15), at(17))
    conversation_id = await lead.decide(outcome())

    result = await lead.decide_on(conversation_id)

    handle.send_reply.assert_not_called()
    assert result["status"] == "pending_approval"
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert "the showing could not be booked" in card["args"]["team_notes"][-1]
    assert "no longer open" in card["args"]["team_notes"][-1]
    assert await rows() == ([], [], [])
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["refusal"] == "time_gone"
    assert "approval" not in row.metadata_

    # Once the time is free again, a person can approve the same card.
    lead.world.calendar = type(lead.world.calendar)()
    open_times.forget_free_busy()
    result = await lead.decide_on(conversation_id)
    assert result["status"] == "completed"
    handle.send_reply.assert_awaited_once()
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_a_refused_time_holds_an_automatic_reply(lead):
    lead.world.calendar.add_busy("jane.doe@example.com", at(15), at(17))

    conversation_id = await lead.decide(outcome(), auto=True)

    handle.send_reply.assert_not_called()
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert "the showing could not be booked" in card["args"]["team_notes"][-1]
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "needs_review"
    assert await rows() == ([], [], [])


# ---------------------------------------------------------------------------
# When the send fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_send_cancels_the_booking_before_anything_goes(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))
    conversation_id = await lead.decide(outcome())

    with pytest.raises(HTTPException):
        await lead.decide_on(conversation_id)

    showings, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert showings[0].status == "cancelled"
    assert {n.status for n in notices} == {"cancelled"}
    await lead.world.send_pending(lead.world.now + timedelta(hours=1))
    _, _, notices = await rows()
    assert {n.status for n in notices} == {"cancelled"}  # nothing went out, nothing to undo
    # The card is still there for a person, as before.
    assert [c["tool_name"] for c in await pending_cards(conversation_id)] == [handle.SEND_REPLY]


@pytest.mark.asyncio
async def test_a_refused_automatic_send_cancels_the_booking(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    conversation_id = await lead.decide(outcome(), auto=True)

    _, attendees, notices = await rows()
    assert attendees[0].cancel_reason == "reply_not_sent"
    assert {n.status for n in notices} == {"cancelled"}
    row, messages = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "failed"
    assert "Gmail refused the reply" in messages[-1].parts[0].content


@pytest.mark.asyncio
async def test_an_uncertain_send_keeps_the_booking_and_says_so(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))

    conversation_id = await lead.decide(outcome(), auto=True)

    _, attendees, _ = await rows()
    (jordan,) = attendees
    assert jordan.status == "booked"
    assert jordan.attention_reason == lead_bookings.REPLY_UNCERTAIN
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.ASK_TEAM
    assert "Showing booked with this reply: 320-03" in card["args"]["team_notes"][-1]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "uncertain"


@pytest.mark.asyncio
async def test_an_uncertain_approved_send_keeps_the_booking_and_rejecting_it_later_does_too(
    lead, monkeypatch
):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))
    conversation_id = await lead.decide(outcome())

    with pytest.raises(TimeoutError):
        await lead.decide_on(conversation_id)
    await lead.decide_on(conversation_id, approved=False)

    _, attendees, _ = await rows()
    assert attendees[0].status == "booked"
    assert attendees[0].attention_reason == lead_bookings.REPLY_UNCERTAIN


@pytest.mark.asyncio
async def test_a_booking_left_by_a_stopped_process_goes_when_the_reply_is_rejected(lead):
    conversation_id = await lead.decide(outcome())
    # As if a process booked for this reply and stopped before sending.
    await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )

    await lead.decide_on(conversation_id, approved=False)

    _, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert {n.status for n in notices} == {"cancelled"}


@pytest.mark.parametrize(
    ("error", "result"),
    [
        (gmail_refused(), "failed"),
        (HTTPException(status_code=500, detail="Failed to send email: timed out"), "uncertain"),
        (TimeoutError("read timed out"), "uncertain"),
    ],
)
def test_which_send_failures_are_certain(error, result):
    assert handle.send_result(error) == result


def test_a_gmail_timeout_or_server_error_is_uncertain():
    for status in (408, 500, 503):
        try:
            try:
                raise HttpError(httplib2.Response({"status": status}), b"")
            except HttpError as error:
                raise HTTPException(status_code=500, detail=str(error))
        except HTTPException as wrapped:
            assert handle.send_result(wrapped) == "uncertain"


# ---------------------------------------------------------------------------
# Later decisions: moves and cancellations
# ---------------------------------------------------------------------------


async def booked_and_told(lead: Lead) -> None:
    """Jordan booked for Wednesday at 4, with the residents and the host told."""
    await lead.decide_on(await lead.decide(outcome()))
    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_a_reschedule_books_the_new_time_then_cancels_the_old(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    await lead.decide_on(conversation_id)

    showings, attendees, notices = await rows()
    assert [(s.starts_at, s.status) for s in showings] == [
        (at(16), "cancelled"),
        (at(17), "scheduled"),
    ]
    assert [(a.status, a.cancel_reason) for a in attendees] == [
        ("cancelled", "prospect"),
        ("booked", None),
    ]
    assert attendees[1].mobile == JORDAN_MOBILE
    kinds = by_kind(notices)
    assert [n.showing_id for n in kinds["resident_cancelled"]] == [showings[0].id]
    assert [n.showing_id for n in kinds["host_event_cancel"]] == [showings[0].id]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "moved"
    assert row.metadata_["showing_booking"]["cancelled"] == [attendees[0].id]


@pytest.mark.asyncio
async def test_a_confirmation_of_another_time_at_the_same_unit_is_a_move(lead):
    await booked_and_told(lead)

    await lead.decide_on(await lead.decide(outcome(start=at(17))))

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["cancelled", "booked"]


@pytest.mark.asyncio
async def test_a_refused_new_time_keeps_the_old_showing(lead):
    await booked_and_told(lead)
    lead.world.calendar.add_busy("jane.doe@example.com", at(17), at(18))
    open_times.forget_free_busy()

    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    result = await lead.decide_on(conversation_id)

    assert result["status"] == "pending_approval"
    handle.send_reply.assert_awaited_once()  # the first reply only
    showings, attendees, _ = await rows()
    assert [(s.starts_at, s.status) for s in showings] == [(at(16), "scheduled")]
    assert [a.status for a in attendees] == ["booked"]


@pytest.mark.asyncio
async def test_a_confirmation_repeated_in_a_later_reply_changes_nothing(lead):
    await booked_and_told(lead)
    _, _, before = await rows()

    await lead.decide_on(await lead.decide(outcome()))

    showings, attendees, notices = await rows()
    assert len(showings) == 1 and len(attendees) == 1 and len(notices) == len(before)


@pytest.mark.asyncio
async def test_a_sent_cancellation_cancels_and_tells_the_residents_and_host(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(outcome(status="cancelled", tour={"start": None}))
    await lead.decide_on(conversation_id)

    showings, attendees, notices = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "prospect")
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "prospect")
    kinds = by_kind(notices)
    assert kinds["resident_cancelled"][0].recipient == RESIDENT
    assert len(kinds["host_event_cancel"]) == 1
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "cancel"


@pytest.mark.asyncio
async def test_a_rejected_cancellation_cancels_nothing(lead):
    await booked_and_told(lead)

    await lead.decide_on(await lead.decide(outcome(status="cancelled")), approved=False)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


@pytest.mark.asyncio
async def test_no_reply_recording_a_cancellation_cancels_at_once(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(outcome("no_reply", status="cancelled"))

    showings, attendees, notices = await rows()
    assert showings[0].status == "cancelled" and attendees[0].cancel_reason == "prospect"
    assert len(by_kind(notices)["resident_cancelled"]) == 1
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "cancel"


@pytest.mark.asyncio
async def test_an_escalation_recording_a_cancellation_cancels_nothing_and_says_what_is_booked(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(outcome("escalate", status="cancelled"))

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]
    (card,) = await pending_cards(conversation_id)
    assert card["args"]["team_notes"] == [
        "Still booked for 320-03: Wednesday, April 14 at 4:00 PM. "
        "Cancel it on the Booking settings tab if the lead cancelled."
    ]


@pytest.mark.asyncio
async def test_a_cancellation_leaves_other_threads_alone(lead):
    await booked_and_told(lead)
    await lead.decide_on(
        await lead.decide(outcome(lead={"name": "Casey Park", "phone": None}), OTHER_THREAD)
    )

    await lead.decide(outcome("no_reply", status="cancelled"))

    _, attendees, _ = await rows()
    assert [(a.zillow_thread_id, a.status) for a in attendees] == [
        (THREAD, "cancelled"),
        (OTHER_THREAD, "booked"),
    ]
    assert attendees[1].name == "Casey Park" and attendees[1].mobile is None


@pytest.mark.asyncio
async def test_a_thread_with_no_name_is_booked_under_a_placeholder(lead):
    await lead.decide_on(await lead.decide(outcome(lead={"name": None, "phone": "not a number"})))

    _, attendees, _ = await rows()
    assert (attendees[0].name, attendees[0].mobile) == (lead_bookings.PLACEHOLDER_NAME, None)


# ---------------------------------------------------------------------------
# The lead adds their details through their own link
# ---------------------------------------------------------------------------


def details(token: str, **changes) -> dict:
    body = booking(lead_token=token, name="Jordan Lee", mobile="412-555-0117", email=ALEX_EMAIL)
    body.pop("start")
    body.update(changes)
    return body


async def provisional(lead: Lead) -> str:
    """Jordan booked by email; answers the thread's link token."""
    await booked_and_told(lead)
    return await lead_links.token_for_thread(THREAD)


@pytest.mark.asyncio
async def test_the_link_reads_the_provisional_place_and_nothing_else(lead):
    token = await provisional(lead)

    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": token}
        )

    assert answer.status_code == 200
    assert answer.json() == {
        "unit": UNIT,
        "address": "320 Example St",
        "starts_at": "2027-04-14T20:00:00Z",
        "ends_at": "2027-04-14T20:30:00Z",
        "timezone": "America/New_York",
    }
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_completing_the_details_sends_the_confirmation_and_the_invite(lead):
    token = await provisional(lead)

    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert result["kind"] == "details" and result["confirmation"] == "captured"
    assert result["booking"]["status"] == "booked"
    assert result["booking"]["starts_at"] == "2027-04-14T20:00:00Z"
    showings, attendees, notices = await rows()
    assert len(showings) == 1
    (jordan,) = attendees
    assert (jordan.mobile, jordan.email, jordan.texts_accepted) == (ALEX_MOBILE, ALEX_EMAIL, True)
    assert (
        jordan.texts_consent_at == lead.world.now and jordan.cancel_token == result["cancel_token"]
    )
    assert [c["key"] for c in jordan.requirement_confirmations] == ["lease_start", "pets"]
    assert jordan.source == "lead_agent" and jordan.zillow_thread_id == THREAD
    kinds = by_kind(notices)
    # The host's event and the residents' text went with the reply; not again.
    assert len(kinds["host_event"]) == 1 and len(kinds["resident_new_showing"]) == 1
    assert kinds["prospect_confirmation"][0].recipient == ALEX_MOBILE
    # The invite and the host's update wait for the confirmation, as for a page booking.
    assert [n.status for n in kinds["prospect_invite"]] == ["held"]
    assert [n.status for n in kinds["host_event_update"]] == ["held"]

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))
    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.status for n in kinds["prospect_invite"]] == ["captured"]
    assert kinds["prospect_invite"][0].payload["attendees"] == [ALEX_EMAIL]


@pytest.mark.asyncio
async def test_completed_details_are_live_texted_to_a_test_number(lead, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", ALEX_MOBILE)
    token = await provisional(lead)
    await store_settings(lead_agent_uses_open_times=True, send_mode="live", test_sends_enabled=True)

    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )

    assert answer.json()["confirmation"] == "sent"
    (text,) = lead.world.texts.sent
    assert text.to == ALEX_MOBILE and "showing is booked" in text.body


@pytest.mark.asyncio
async def test_a_refused_number_undoes_the_details_and_keeps_the_place(lead, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", ALEX_MOBILE)
    token = await provisional(lead)
    await store_settings(lead_agent_uses_open_times=True, send_mode="live", test_sends_enabled=True)
    lead.world.texts.refuse(ALEX_MOBILE, "invalid_number")

    async with lead.world.client() as client:
        refused = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "number_refused"
        _, attendees, notices = await rows()
        (jordan,) = attendees
        assert (jordan.status, jordan.mobile, jordan.email) == ("booked", JORDAN_MOBILE, None)
        assert not jordan.texts_accepted and jordan.texts_consent_at is None
        assert {n.status for n in by_kind(notices)["prospect_invite"]} == {"cancelled"}

        # Another number goes through.
        again = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details",
            json=details(token, mobile="412-555-0118", email="robin@example.com"),
        )
    assert again.status_code == 201, again.text


@pytest.mark.asyncio
async def test_completed_details_cannot_be_completed_twice(lead):
    token = await provisional(lead)
    async with lead.world.client() as client:
        first = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )
        second = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details",
            json=details(token, mobile="412-555-0118"),
        )
        read = await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": token})

    assert first.status_code == 201
    assert (second.status_code, second.json()["detail"]["code"]) == (409, "already_completed")
    assert (read.status_code, read.json()["detail"]["code"]) == (404, "no_booking")
    _, attendees, notices = await rows()
    assert attendees[0].mobile == ALEX_MOBILE
    assert len(by_kind(notices)["prospect_confirmation"]) == 1


@pytest.mark.asyncio
async def test_a_token_for_another_thread_or_unit_completes_nothing(lead):
    await provisional(lead)
    other = await lead_links.token_for_thread(OTHER_THREAD)
    lead.world.other_listings = [
        lead.world.listing.model_copy(update={"unit": "320-05", "address": "320 Example St"})
    ]
    mine = await lead_links.token_for_thread(THREAD)

    async with lead.world.client() as client:
        answers = [
            await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": other}),
            await client.post(f"{PUBLIC}/units/320-05/lead-booking", json={"lead_token": mine}),
            await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": "x" * 22}),
            await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={}),
            await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(other)),
            await client.post(f"{PUBLIC}/units/320-05/lead-booking/details", json=details(mine)),
        ]

    assert [a.status_code for a in answers] == [404, 404, 404, 404, 404, 404]
    assert {a.json()["detail"]["code"] for a in answers} == {"no_booking"}
    _, attendees, notices = await rows()
    assert attendees[0].email is None and "prospect_confirmation" not in by_kind(notices)


@pytest.mark.asyncio
async def test_completing_checks_the_ticks_and_the_bot_check(lead, monkeypatch):
    token = await provisional(lead)
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "made-up-secret")
    async with lead.world.client() as client:
        missing = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details",
            json=details(token, confirmations={"lease_start": True, "pets": False, "texts": True}),
        )
        bot = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details",
            json=details(token, turnstile_token="turnstile-fails"),
        )

    assert (missing.status_code, list(missing.json()["detail"]["fields"])) == (
        422,
        ["confirmations.pets"],
    )
    assert (bot.status_code, bot.json()["detail"]["code"]) == (403, "turnstile_failed")
    _, attendees, _ = await rows()
    assert attendees[0].texts_consent_at is None


@pytest.mark.asyncio
async def test_completing_applies_the_page_limits(lead):
    token = await provisional(lead)
    await store_settings(
        lead_agent_uses_open_times=True,
        public_limits={
            "upcoming_per_mobile": 2,
            "upcoming_per_email": 2,
            "bookings_per_ip_per_day": 6,
            "bookings_per_day": 1,
        },
    )
    async with lead.world.client() as client:
        # Someone else's page booking reaches the day's cap of one.
        other = booking(name="Casey Park", mobile="412-555-0119", email="casey@example.com")
        assert (await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=other)).status_code == 201
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )

    assert (answer.status_code, answer.json()["detail"]["code"]) == (429, "limit_site")
    _, attendees, _ = await rows()
    assert attendees[0].texts_consent_at is None


@pytest.mark.asyncio
async def test_the_switch_off_hides_the_provisional_place(lead):
    token = await provisional(lead)
    await switch_off()

    async with lead.world.client() as client:
        read = await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": token})
        complete = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )

    assert (read.status_code, complete.status_code) == (404, 404)


@pytest.mark.asyncio
async def test_public_booking_off_hides_the_routes(lead):
    token = await provisional(lead)
    await store_settings(lead_agent_uses_open_times=True, public_booking_enabled=False)

    async with lead.world.client() as client:
        read = await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": token})

    assert read.status_code == 404 and read.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_the_answers_show_nothing_about_the_host_or_anyone_else(lead):
    token = await provisional(lead)
    # Someone else in the same showing, booked on the page.
    async with lead.world.client() as client:
        await client.post(
            f"{PUBLIC}/units/{UNIT}/bookings",
            json=booking(name="Casey Park", mobile="412-555-0119", email="casey@example.com"),
        )
        read = await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking", json={"lead_token": token})
        done = await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token))

    for answer in (read, done):
        text = answer.text
        for private in ("Jane", "jane.doe", "Casey", "0119", "casey@", RESIDENT, "Morgan"):
            assert private not in text, (private, text)


@pytest.mark.asyncio
async def test_the_team_tab_shows_a_place_booked_by_email_and_whether_details_arrived(lead):
    token = await provisional(lead)
    app = lead.world.team_app()
    from api.src.showing_bookings import routes

    app.dependency_overrides[routes.clock] = lambda: lead.world.now

    async with lead.world.client(app) as client:
        before = (await client.get("/api/showing-bookings/showings")).json()
    async with lead.world.client() as client:
        await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token))
    async with lead.world.client(app) as client:
        after = (await client.get("/api/showing-bookings/showings")).json()

    (person,) = before["booked"][0]["people"]
    assert person["source"] == "lead_agent" and person["details_pending"] is True
    assert person["lead_conversation_id"].startswith("test-lead-")
    (person,) = after["booked"][0]["people"]
    assert person["details_pending"] is False


@pytest.mark.asyncio
async def test_a_completed_place_is_not_cancelled_by_rejecting_its_reply(lead):
    conversation_id = await lead.decide(outcome())
    await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        await client.post(f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token))

    await lead.decide_on(conversation_id, approved=False)

    _, attendees, _ = await rows()
    assert attendees[0].status == "booked"


@pytest.mark.asyncio
async def test_the_attendee_column_matches_its_migration():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from api.src.database.database import Base, engine

    def only_attendees(obj, name, type_, reflected, compare_to):
        table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
        return table == "showing_attendees"

    def differences(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": only_attendees}
        )
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == []


@pytest.mark.asyncio
async def test_notices_are_held_until_the_send_is_known(lead):
    await lead_bookings.prepare_reply(
        conversation_id="test-lead-held",
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )

    report_before = await lead.world.send_pending(lead.world.now + timedelta(minutes=1))
    _, _, notices = await rows()
    assert {n.status for n in notices} == {"pending"}
    assert all(n.not_before == lead.world.now + lead_bookings.HOLD for n in notices)
    assert report_before is not None
    async with AsyncSessionFactory() as session:
        assert (
            await session.scalar(select(ShowingAttendee.lead_conversation_id)) == "test-lead-held"
        )
        assert (
            await session.scalar(select(ShowingNotice.id).where(ShowingNotice.status == "sent"))
            is None
        )
