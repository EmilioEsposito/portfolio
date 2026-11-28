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
from sqlalchemy import delete, func, select, update

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

    async def decide(
        self,
        decided,
        thread_id: str = THREAD,
        *,
        auto: bool = False,
        settings: LeadSettings | None = None,
    ) -> str:
        """Handle a thread whose agent returns ``decided``; answers the conversation id.

        ``auto`` turns approval off; ``settings`` gives the lead agent's settings whole."""
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
            AsyncMock(return_value=settings or LeadSettings(require_approval=not auto)),
        )
        await handle.handle_thread(thread_id, conversation_id=conversation_id, metadata={})
        return conversation_id

    async def decide_on(
        self, conversation_id: str, approved: bool = True, *, body: str | None = None
    ):
        row, messages = await stored(conversation_id)
        (card,) = extract_pending_approval_from_messages(messages)
        override = {"body": body} if body is not None else None
        async with AsyncSessionFactory() as session:
            return await handle.apply_approval(
                session,
                conversation_id=conversation_id,
                metadata=row.metadata_,
                decisions=[
                    ApprovalDecision(
                        card["tool_call_id"], approved=approved, override_args=override
                    )
                ],
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
    monkeypatch.setattr(
        handle, "send_reply", AsyncMock(return_value=handle.Delivery("Email sent."))
    )
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
    (card,) = await pending_cards(conversation_id)
    assert card["args"]["team_notes"] == []  # no booking note with the switch off

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
async def test_a_place_whose_reply_was_never_sent_is_not_in_the_next_case(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))
    with pytest.raises(HTTPException):
        await lead.decide_on(await lead.decide(outcome()))
    monkeypatch.setattr(
        handle, "send_reply", AsyncMock(return_value=handle.Delivery("Email sent."))
    )

    await lead.decide(outcome("no_reply", status="none"))

    (case,) = handle.respond.call_args.args
    assert case.own_showings == []


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
        "details_added": False,
        "texted": False,
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
    await store_settings(lead_agent_uses_open_times=True, send_mode="test")

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
    await store_settings(lead_agent_uses_open_times=True, send_mode="test")
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
    # The link's page now says they are booked (test_zillow_lead_moves.py).
    assert read.status_code == 200 and read.json()["details_added"] is True
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


# ---------------------------------------------------------------------------
# The send card says what sending does; edited words change no booking
# ---------------------------------------------------------------------------

BOOK_NOTE = "Sending this books unit 320-03 for Wed, Apr 14 at 4:00 PM."
MOVE_NOTE = (
    "Sending this moves their showing of 320-03 from Wed, Apr 14 at 4:00 PM "
    "to Wed, Apr 14 at 5:00 PM."
)
CANCEL_NOTE = "Sending this cancels their showing of 320-03 on Wed, Apr 14 at 4:00 PM."
NOT_BOOKED = (
    "The reply was edited, so the showing was not booked automatically. "
    "Add it on the Booking settings tab if it still applies."
)
NOT_MOVED = (
    "The reply was edited, so the showing was not moved automatically. "
    "Change it on the Booking settings tab if it still applies."
)
NOT_CANCELLED = (
    "The reply was edited, so the showing was not cancelled automatically. "
    "Cancel it on the Booking settings tab if it still applies."
)
EDITED = "Sorry, Wednesday no longer works. Could you do Thursday?\n\nSernia Capital Team"


async def card_notes(conversation_id: str) -> list[str]:
    (card,) = await pending_cards(conversation_id)
    return card["args"]["team_notes"]


@pytest.mark.asyncio
async def test_an_unchanged_approval_of_a_confirmation_books_as_its_card_said(lead):
    conversation_id = await lead.decide(outcome())
    assert await card_notes(conversation_id) == [BOOK_NOTE]

    result = await lead.decide_on(conversation_id)

    assert "Showing booked with this reply" in result["output"]
    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


@pytest.mark.asyncio
async def test_an_unchanged_approval_of_a_move_moves_as_its_card_said(lead):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    assert await card_notes(conversation_id) == [MOVE_NOTE]

    await lead.decide_on(conversation_id)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["cancelled", "booked"]


@pytest.mark.asyncio
async def test_an_unchanged_approval_of_a_cancellation_cancels_as_its_card_said(lead):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome(status="cancelled"))
    assert await card_notes(conversation_id) == [CANCEL_NOTE]

    await lead.decide_on(conversation_id)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["cancelled"]


@pytest.mark.asyncio
async def test_a_repeated_confirmation_has_no_note(lead):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome())
    assert await card_notes(conversation_id) == []


@pytest.mark.asyncio
async def test_trailing_whitespace_is_not_an_edit(lead):
    conversation_id = await lead.decide(outcome())

    await lead.decide_on(conversation_id, body=outcome().reply_body + "\n\n  ")

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


@pytest.mark.asyncio
async def test_an_edited_confirmation_books_nothing_and_says_so(lead):
    conversation_id = await lead.decide(outcome())

    result = await lead.decide_on(conversation_id, body=EDITED)

    assert result["status"] == "completed"
    handle.send_reply.assert_awaited_once()
    assert handle.send_reply.call_args.args[1] == EDITED
    assert result["output"] == f"Email sent. {NOT_BOOKED}"
    assert await rows() == ([], [], [])
    row, messages = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "not_applied_edited"
    assert row.metadata_["showing_booking"]["not_applied"] == "book"
    assert NOT_BOOKED in messages[-1].parts[0].content
    assert row.metadata_["approval"][0]["edited"] is True


@pytest.mark.asyncio
async def test_an_edited_move_moves_nothing_and_says_so(lead):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))

    result = await lead.decide_on(conversation_id, body=EDITED)

    assert result["output"].endswith(NOT_MOVED)
    showings, attendees, _ = await rows()
    assert [(s.starts_at, s.status) for s in showings] == [(at(16), "scheduled")]
    assert [a.status for a in attendees] == ["booked"]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["not_applied"] == "move"


@pytest.mark.asyncio
async def test_an_edited_cancellation_cancels_nothing_and_says_so(lead):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome(status="cancelled"))

    result = await lead.decide_on(conversation_id, body="Your showing stays booked.\n\nSernia")

    assert result["output"].endswith(NOT_CANCELLED)
    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["not_applied"] == "cancel"


@pytest.mark.asyncio
async def test_an_edited_reply_cancels_a_place_its_draft_booked_and_never_sent(lead):
    conversation_id = await lead.decide(outcome())
    # As if a process booked for the draft and stopped before sending.
    await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )

    result = await lead.decide_on(conversation_id, body=EDITED)

    assert result["output"].endswith(NOT_BOOKED)
    _, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert {n.status for n in notices} == {"cancelled"}
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["cancelled_unsent"] == [attendees[0].id]


@pytest.mark.asyncio
async def test_switch_off_an_edited_reply_behaves_as_before(lead):
    await switch_off()
    conversation_id = await lead.decide(outcome())

    result = await lead.decide_on(conversation_id, body=EDITED)

    assert result["output"] == "Email sent."
    row, _ = await stored(conversation_id)
    assert "showing_booking" not in row.metadata_


# ---------------------------------------------------------------------------
# A place recovered after an interrupted send
# ---------------------------------------------------------------------------


async def stopped_after_booking(conversation_id: str, lead: Lead) -> None:
    """As if a process booked for this conversation's reply and stopped before sending."""
    await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )


@pytest.mark.asyncio
async def test_a_recovered_place_is_kept_when_the_reply_is_then_sent(lead):
    conversation_id = await lead.decide(outcome())
    await stopped_after_booking(conversation_id, lead)

    await lead.decide_on(conversation_id)

    _, attendees, notices = await rows()
    assert [a.status for a in attendees] == ["booked"]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "booked"
    assert all(n.not_before <= lead.world.now for n in notices)


@pytest.mark.asyncio
async def test_a_recovered_place_is_cancelled_when_gmail_then_refuses(lead, monkeypatch):
    conversation_id = await lead.decide(outcome())
    await stopped_after_booking(conversation_id, lead)
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    with pytest.raises(HTTPException):
        await lead.decide_on(conversation_id)

    _, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert {n.status for n in notices} == {"cancelled"}


@pytest.mark.asyncio
async def test_a_place_booked_through_the_link_survives_a_refused_send(lead, monkeypatch):
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/bookings", json=booking(lead_token=token)
        )
    assert answer.status_code == 201
    conversation_id = await lead.decide(outcome())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    with pytest.raises(HTTPException):
        await lead.decide_on(conversation_id)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


@pytest.mark.asyncio
async def test_a_place_an_earlier_sent_reply_booked_survives_a_refused_send(lead, monkeypatch):
    await booked_and_told(lead)
    conversation_id = await lead.decide(outcome())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    with pytest.raises(HTTPException):
        await lead.decide_on(conversation_id)

    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


# ---------------------------------------------------------------------------
# A card whose statement went stale
# ---------------------------------------------------------------------------


async def booked_through_link(lead: Lead, start) -> None:
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/bookings", json=booking(start, lead_token=token)
        )
    assert answer.status_code == 201, answer.text


@pytest.mark.asyncio
async def test_a_card_is_held_when_sending_it_would_now_do_something_else(lead):
    conversation_id = await lead.decide(outcome())
    assert await card_notes(conversation_id) == [BOOK_NOTE]
    # Meanwhile the lead booked 5 PM through their link: sending would now move it.
    await booked_through_link(lead, at(17))
    moved_back = (
        "Sending this moves their showing of 320-03 from Wed, Apr 14 at 5:00 PM "
        "to Wed, Apr 14 at 4:00 PM."
    )

    result = await lead.decide_on(conversation_id)

    handle.send_reply.assert_not_called()
    assert result["status"] == "pending_approval"
    assert result["output"] == f"Not sent. {lead_bookings.PLAN_CHANGED} {moved_back}"
    assert await card_notes(conversation_id) == [
        BOOK_NOTE,
        lead_bookings.PLAN_CHANGED,
        moved_back,
    ]
    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]

    # Approving the card as it now reads applies what it now says.
    await lead.decide_on(conversation_id)
    handle.send_reply.assert_awaited_once()
    _, attendees, _ = await rows()
    assert [(a.source, a.status) for a in attendees] == [
        ("page", "cancelled"),
        ("lead_agent", "booked"),
    ]


@pytest.mark.asyncio
async def test_a_card_whose_time_the_lead_already_booked_sends_and_changes_nothing(lead):
    conversation_id = await lead.decide(outcome())
    await booked_through_link(lead, at(16))

    result = await lead.decide_on(conversation_id)

    assert result["status"] == "completed"
    handle.send_reply.assert_awaited_once()
    _, attendees, _ = await rows()
    assert [(a.source, a.status) for a in attendees] == [("page", "booked")]


# ---------------------------------------------------------------------------
# The automatic send holds a reply that changes a booking, until its kind is ticked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "reason", "note"),
    [
        ("book", "This reply books a showing, so a person sends it.", BOOK_NOTE),
        ("move", "This reply moves a showing, so a person sends it.", MOVE_NOTE),
        ("cancel", "This reply cancels a showing, so a person sends it.", CANCEL_NOTE),
    ],
)
async def test_an_automatic_reply_that_changes_a_booking_waits_for_a_person(
    lead, kind, reason, note
):
    if kind != "book":
        await booked_and_told(lead)
    _, before, notices_before = await rows()
    decided = {
        "book": outcome(),
        "move": outcome(status="rescheduled", start=at(17)),
        "cancel": outcome(status="cancelled"),
    }[kind]

    conversation_id = await lead.decide(decided, auto=True)

    sends = 0 if kind == "book" else 1  # the first reply, approved by hand
    assert handle.send_reply.await_count == sends
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert card["args"]["team_notes"] == [note, reason]
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"] == {"status": "needs_review", "reasons": [reason]}
    assert "showing_booking" not in row.metadata_
    _, attendees, notices = await rows()
    assert [(a.id, a.status) for a in attendees] == [(a.id, a.status) for a in before]
    assert len(notices) == len(notices_before)
    async with AsyncSessionFactory() as session:
        claimed = await session.scalar(
            select(AutoApprovedSend.conversation_id).where(
                AutoApprovedSend.conversation_id == conversation_id
            )
        )
    assert claimed is None  # nothing was reserved


@pytest.mark.asyncio
async def test_approving_a_held_automatic_reply_books_and_sends(lead):
    conversation_id = await lead.decide(outcome(), auto=True)
    handle.send_reply.assert_not_called()

    result = await lead.decide_on(conversation_id)

    assert result["status"] == "completed"
    handle.send_reply.assert_awaited_once()
    _, attendees, notices = await rows()
    assert [(a.source, a.status) for a in attendees] == [("lead_agent", "booked")]
    assert sorted(n.kind for n in notices) == ["host_event", "resident_new_showing"]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "booked"


@pytest.mark.asyncio
async def test_a_held_automatic_reply_stays_held_when_tried_again(lead):
    conversation_id = await lead.decide(outcome(), auto=True)
    row, _ = await stored(conversation_id)
    from api.src.zillow_leads.case import case_from_record

    await auto_reply.maybe_send_reply(
        conversation_id, case_from_record(row.metadata_["case"]), outcome()
    )

    handle.send_reply.assert_not_called()
    assert await rows() == ([], [], [])
    assert [c["tool_name"] for c in await pending_cards(conversation_id)] == [handle.SEND_REPLY]


# ---------------------------------------------------------------------------
# A ticked kind is booked and sent with no person
# ---------------------------------------------------------------------------

# Replies that state the booked time as the policy has them state it.
AT_FOUR = "You are confirmed for Wednesday, April 14 at 4:00 PM.\n\nSernia Capital Team"
AT_FIVE = "Your showing is now Wednesday, April 14 at 5:00 PM.\n\nSernia Capital Team"
# Approval off, with every kind of booking reply ticked.
SEND_ALL = LeadSettings(
    require_approval=False,
    auto_send_new_booking=True,
    auto_send_reschedule=True,
    auto_send_cancellation=True,
)


def ticked(**kinds: bool) -> LeadSettings:
    """Approval off, with only the named kinds ticked."""
    return LeadSettings(require_approval=False, **kinds)


async def claims(conversation_id: str) -> int:
    async with AsyncSessionFactory() as session:
        found = await session.scalars(
            select(AutoApprovedSend).where(AutoApprovedSend.conversation_id == conversation_id)
        )
        return len(list(found))


@pytest.mark.asyncio
async def test_a_ticked_new_booking_is_booked_and_sent_with_no_person(lead):
    conversation_id = await lead.decide(
        outcome(reply_body=AT_FOUR), settings=ticked(auto_send_new_booking=True)
    )

    handle.send_reply.assert_awaited_once()
    assert await pending_cards(conversation_id) == []
    showings, attendees, notices = await rows()
    assert [(s.starts_at, s.status) for s in showings] == [(at(16), "scheduled")]
    (jordan,) = attendees
    assert (jordan.source, jordan.status, jordan.texts_accepted) == ("lead_agent", "booked", False)
    assert jordan.lead_reply_sent_at == lead.world.now
    # The host's event and the residents' text no longer wait for the send.
    assert sorted(n.kind for n in notices) == ["host_event", "resident_new_showing"]
    assert all(n.not_before <= lead.world.now for n in notices)
    row, messages = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert row.metadata_["showing_booking"]["action"] == "booked"
    assert row.metadata_["showing_booking"]["sent"] == "sent"
    assert (
        "Showing booked with this reply: 320-03, Wednesday, April 14 at 4:00 PM."
        in messages[-1].parts[0].content
    )


@pytest.mark.asyncio
async def test_a_ticked_reschedule_moves_the_showing_with_no_person(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(
        outcome(status="rescheduled", start=at(17), reply_body=AT_FIVE),
        settings=ticked(auto_send_reschedule=True),
    )

    assert handle.send_reply.await_count == 2  # the first by hand, this one automatically
    assert await pending_cards(conversation_id) == []
    showings, attendees, _ = await rows()
    assert [(s.starts_at, s.status) for s in showings] == [
        (at(16), "cancelled"),
        (at(17), "scheduled"),
    ]
    assert [(a.status, a.cancel_reason) for a in attendees] == [
        ("cancelled", "prospect"),
        ("booked", None),
    ]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "moved"


@pytest.mark.asyncio
async def test_a_ticked_cancellation_cancels_the_showing_with_no_person(lead):
    await booked_and_told(lead)

    conversation_id = await lead.decide(
        outcome(status="cancelled"), settings=ticked(auto_send_cancellation=True)
    )

    assert handle.send_reply.await_count == 2
    assert await pending_cards(conversation_id) == []
    showings, attendees, _ = await rows()
    assert [s.status for s in showings] == ["cancelled"]
    assert [(a.status, a.cancel_reason) for a in attendees] == [("cancelled", "prospect")]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "cancel"
    assert row.metadata_["showing_booking"]["cancelled"] == [attendees[0].id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "others", "reason"),
    [
        (
            "book",
            {"auto_send_reschedule": True, "auto_send_cancellation": True},
            "This reply books a showing, so a person sends it.",
        ),
        (
            "move",
            {"auto_send_new_booking": True, "auto_send_cancellation": True},
            "This reply moves a showing, so a person sends it.",
        ),
        (
            "cancel",
            {"auto_send_new_booking": True, "auto_send_reschedule": True},
            "This reply cancels a showing, so a person sends it.",
        ),
    ],
)
async def test_each_kind_waits_for_a_person_until_its_own_box_is_ticked(lead, kind, others, reason):
    if kind != "book":
        await booked_and_told(lead)
    _, before, _ = await rows()
    decided = {
        "book": outcome(reply_body=AT_FOUR),
        "move": outcome(status="rescheduled", start=at(17), reply_body=AT_FIVE),
        "cancel": outcome(status="cancelled"),
    }[kind]

    conversation_id = await lead.decide(decided, settings=ticked(**others))

    assert handle.send_reply.await_count == (0 if kind == "book" else 1)
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert card["args"]["team_notes"][-1] == reason
    _, attendees, _ = await rows()
    assert [(a.id, a.status) for a in attendees] == [(a.id, a.status) for a in before]
    assert await claims(conversation_id) == 0


@pytest.mark.asyncio
async def test_with_approval_on_a_ticked_kind_still_waits(lead):
    conversation_id = await lead.decide(
        outcome(reply_body=AT_FOUR), settings=SEND_ALL.model_copy(update={"require_approval": True})
    )

    handle.send_reply.assert_not_called()
    (card,) = await pending_cards(conversation_id)
    assert card["args"]["team_notes"] == [BOOK_NOTE]  # no hold reason: approval is simply on
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "body", "reason"),
    [
        (
            "book",
            "See you Wednesday at 4.\n\nSernia Capital Team",
            "This reply books a showing but does not give the time as "
            "Wednesday, April 14 at 4:00 PM, so a person sends it.",
        ),
        (
            "move",
            AT_FOUR,  # the old time, not the one code would book
            "This reply moves a showing but does not give the time as "
            "Wednesday, April 14 at 5:00 PM, so a person sends it.",
        ),
    ],
)
async def test_a_ticked_reply_that_does_not_state_the_booked_time_waits(lead, kind, body, reason):
    if kind == "move":
        await booked_and_told(lead)
    _, before, _ = await rows()
    start = at(16) if kind == "book" else at(17)
    status = "confirmed" if kind == "book" else "rescheduled"

    conversation_id = await lead.decide(
        outcome(status=status, start=start, reply_body=body), settings=SEND_ALL
    )

    assert handle.send_reply.await_count == (0 if kind == "book" else 1)
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert card["args"]["team_notes"][-1] == reason
    _, attendees, _ = await rows()
    assert [(a.id, a.status) for a in attendees] == [(a.id, a.status) for a in before]
    assert await claims(conversation_id) == 0


@pytest.mark.asyncio
async def test_a_refused_time_leaves_a_ticked_reply_with_a_person(lead):
    lead.world.calendar.add_busy("jane.doe@example.com", at(15), at(17))

    conversation_id = await lead.decide(outcome(reply_body=AT_FOUR), settings=SEND_ALL)

    handle.send_reply.assert_not_called()
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY  # still a draft a person can send
    assert "the showing could not be booked" in card["args"]["team_notes"][-1]
    assert await rows() == ([], [], [])
    assert await claims(conversation_id) == 0  # nothing was reserved
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "needs_review"
    assert row.metadata_["showing_booking"]["refusal"] == "time_gone"

    # Once the time is free again, a person approves the same card.
    lead.world.calendar = type(lead.world.calendar)()
    open_times.forget_free_busy()
    assert (await lead.decide_on(conversation_id))["status"] == "completed"
    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["booked"]


def books_five_meanwhile(lead: Lead, conversation_id_of: list[str]):
    """A stand-in for the Gmail headers lookup, which runs between the plan and
    the booking: while it runs, the lead books 5 PM for the thread, as through
    their own link, and is told."""

    async def lookup(*_args, **_kwargs):
        five = outcome(start=at(17))
        other = await lead_bookings.prepare_reply(
            conversation_id="test-lead-earlier",
            thread_id=THREAD,
            lead=five.lead,
            tour=five.tour,
            listed_units=[UNIT],
            now=lead.world.now,
        )
        await lead_bookings.after_send(other, "sent", now=lead.world.now)
        return {"in_reply_to": f"<{conversation_id_of[0]}@zillow.example>", "thread_id": "T"}

    return lookup


async def decide_while_the_lead_books_five(lead: Lead, monkeypatch, settings: LeadSettings) -> str:
    """Handle a reply confirming 4 PM whose plan is a new booking, with the
    thread's showings changing before the booking is made."""
    decided = outcome(reply_body=AT_FOUR)
    conversation_id = f"test-lead-{uuid.uuid4().hex}"
    lead.conversations.append(conversation_id)
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=decided))
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=lead_thread(THREAD)))
    monkeypatch.setattr(
        handle, "_get_threading_headers", books_five_meanwhile(lead, [conversation_id])
    )
    monkeypatch.setattr(auto_reply, "effective_settings", AsyncMock(return_value=settings))
    await handle.handle_thread(THREAD, conversation_id=conversation_id, metadata={})
    return conversation_id


@pytest.mark.asyncio
async def test_a_booking_that_became_a_move_waits_when_moves_are_not_ticked(lead, monkeypatch):
    conversation_id = await decide_while_the_lead_books_five(
        lead, monkeypatch, ticked(auto_send_new_booking=True)
    )

    handle.send_reply.assert_not_called()
    assert await claims(conversation_id) == 0
    showings, attendees, _ = await rows()
    # The lead's own 5 PM stands; the 4 PM this attempt booked is undone.
    assert sorted((s.starts_at, s.status) for s in showings) == [
        (at(16), "cancelled"),
        (at(17), "scheduled"),
    ]
    assert sorted((a.status, a.cancel_reason or "") for a in attendees) == [
        ("booked", ""),
        ("cancelled", "reply_not_sent"),
    ]
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.SEND_REPLY
    assert card["args"]["team_notes"] == [
        BOOK_NOTE,
        lead_bookings.PLAN_CHANGED,
        "Sending this moves their showing of 320-03 from Wed, Apr 14 at 5:00 PM "
        "to Wed, Apr 14 at 4:00 PM.",
        "This reply moves a showing, so a person sends it.",
    ]

    # A person approving the card now moves the showing, as the card says.
    assert (await lead.decide_on(conversation_id))["status"] == "completed"
    showings, _, _ = await rows()
    assert sorted((s.starts_at, s.status) for s in showings if s.status == "scheduled") == [
        (at(16), "scheduled")
    ]


@pytest.mark.asyncio
async def test_a_booking_that_became_a_move_goes_when_moves_are_ticked_too(lead, monkeypatch):
    conversation_id = await decide_while_the_lead_books_five(
        lead, monkeypatch, ticked(auto_send_new_booking=True, auto_send_reschedule=True)
    )

    handle.send_reply.assert_awaited_once()
    assert await pending_cards(conversation_id) == []
    showings, _, _ = await rows()
    assert sorted((s.starts_at, s.status) for s in showings) == [
        (at(16), "scheduled"),
        (at(17), "cancelled"),
    ]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "moved"


@pytest.mark.asyncio
async def test_gmail_refusing_a_ticked_booking_reply_cancels_the_booking(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    conversation_id = await lead.decide(outcome(reply_body=AT_FOUR), settings=SEND_ALL)

    showings, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert showings[0].status == "cancelled"
    assert {n.status for n in notices} == {"cancelled"}  # nothing reached the host or residents
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.ASK_TEAM  # inspect only: it cannot send again
    assert "This reply books unit 320-03 for Wed, Apr 14 at 4:00 PM." in card["args"]["question"]
    assert card["args"]["team_notes"] == [
        "The reply was not sent, so the showing booked for it was cancelled."
    ]
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "failed"


@pytest.mark.asyncio
async def test_an_unknown_send_keeps_a_ticked_booking_marked_for_the_team(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))

    conversation_id = await lead.decide(outcome(reply_body=AT_FOUR), settings=SEND_ALL)

    _, attendees, _ = await rows()
    assert attendees[0].status == "booked"
    assert attendees[0].attention_reason == lead_bookings.REPLY_UNCERTAIN
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "uncertain"
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.ASK_TEAM  # still waiting for the team to look
    assert card["args"]["team_notes"] == [
        "The reply may not have gone out. The showing stays booked, marked on the Showings section."
    ]


@pytest.mark.asyncio
async def test_a_send_already_reserved_cancels_what_this_attempt_booked(lead, monkeypatch):
    monkeypatch.setattr(auto_reply, "claim_auto_approved_send", AsyncMock(return_value=False))

    conversation_id = await lead.decide(outcome(reply_body=AT_FOUR), settings=SEND_ALL)

    handle.send_reply.assert_not_called()
    _, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert {n.status for n in notices} == {"cancelled"}
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "needs_review"
    assert row.metadata_["showing_booking"]["sent"] == "failed"
    (card,) = await pending_cards(conversation_id)
    assert card["tool_name"] == handle.ASK_TEAM
    assert "This reply books" not in card["args"]["question"]  # settled at once, nothing to check


@pytest.mark.asyncio
async def test_a_send_already_reserved_keeps_a_place_an_earlier_attempt_booked(lead, monkeypatch):
    """The earlier attempt may have told the lead, so its place stands, marked."""
    from api.src.zillow_leads.case import case_from_record

    decided = outcome(reply_body=AT_FOUR)
    conversation_id = await lead.decide(decided)  # approval on: the draft waits
    await stopped_after_booking(conversation_id, lead)
    monkeypatch.setattr(auto_reply, "effective_settings", AsyncMock(return_value=SEND_ALL))
    monkeypatch.setattr(auto_reply, "claim_auto_approved_send", AsyncMock(return_value=False))
    row, _ = await stored(conversation_id)

    await auto_reply.maybe_send_reply(
        conversation_id, case_from_record(row.metadata_["case"]), decided
    )

    handle.send_reply.assert_not_called()
    _, attendees, _ = await rows()
    assert attendees[0].status == "booked"
    assert attendees[0].attention_reason == lead_bookings.REPLY_UNCERTAIN
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["sent"] == "uncertain"


@pytest.mark.asyncio
async def test_a_failure_to_reserve_the_send_undoes_the_booking_and_keeps_the_draft(
    lead, monkeypatch
):
    monkeypatch.setattr(
        auto_reply,
        "claim_auto_approved_send",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    conversation_id = await lead.decide(outcome(reply_body=AT_FOUR), settings=SEND_ALL)

    handle.send_reply.assert_not_called()
    _, attendees, notices = await rows()
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "reply_not_sent")
    assert {n.status for n in notices} == {"cancelled"}
    assert [c["tool_name"] for c in await pending_cards(conversation_id)] == [handle.SEND_REPLY]

    # A person can still send the draft, which books the time again.
    assert (await lead.decide_on(conversation_id))["status"] == "completed"
    _, attendees, _ = await rows()
    assert [a.status for a in attendees] == ["cancelled", "booked"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decided",
    [
        outcome(status="none", tour={"start": None}),
        outcome(tour={"virtual": True}),
        outcome(status="proposed"),
    ],
    ids=["no tour", "virtual", "proposed"],
)
async def test_an_automatic_reply_that_changes_nothing_is_sent(lead, decided):
    conversation_id = await lead.decide(decided, auto=True)

    handle.send_reply.assert_awaited_once()
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_an_automatic_reply_confirming_a_time_already_booked_is_sent(lead):
    await booked_and_told(lead)
    _, before, notices_before = await rows()

    conversation_id = await lead.decide(outcome(), auto=True)

    assert handle.send_reply.await_count == 2  # the first by hand, this one automatically
    row, _ = await stored(conversation_id)
    assert row.metadata_["auto_delivery"]["status"] == "sent"
    assert "showing_booking" not in row.metadata_
    _, attendees, notices = await rows()
    assert [a.status for a in attendees] == ["booked"] and len(notices) == len(notices_before)


# ---------------------------------------------------------------------------
# Never sent, sent, uncertain: three states of a place, each kept or not
# ---------------------------------------------------------------------------


async def sent_then_not_saved(lead: Lead, monkeypatch) -> str:
    """Gmail sends the approved reply, then saving the approval fails: the card stays."""
    conversation_id = await lead.decide(outcome())
    monkeypatch.setattr(
        handle, "save_agent_conversation", AsyncMock(side_effect=RuntimeError("db gone"))
    )
    with pytest.raises(RuntimeError):
        await lead.decide_on(conversation_id)
    from api.src.agent_conversations import models as conversation_models

    monkeypatch.setattr(
        handle, "save_agent_conversation", conversation_models.save_agent_conversation
    )
    handle.send_reply.assert_awaited_once()
    assert [c["tool_name"] for c in await pending_cards(conversation_id)] == [handle.SEND_REPLY]
    return conversation_id


async def the_place() -> ShowingAttendee:
    _, attendees, _ = await rows()
    (place,) = attendees
    return place


@pytest.mark.asyncio
async def test_a_sent_place_is_marked_sent(lead):
    await lead.decide_on(await lead.decide(outcome()))

    place = await the_place()
    assert place.lead_reply_sent_at == lead.world.now
    assert place.attention_reason is None


@pytest.mark.asyncio
async def test_an_uncertain_place_is_kept_marked_and_not_marked_sent(lead, monkeypatch):
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe")))
    with pytest.raises(TimeoutError):
        await lead.decide_on(await lead.decide(outcome()))

    place = await the_place()
    assert place.status == "booked"
    assert place.attention_reason == lead_bookings.REPLY_UNCERTAIN
    assert place.lead_reply_sent_at is None


@pytest.mark.asyncio
async def test_a_never_sent_place_is_not_marked_sent_and_is_cleaned_up(lead):
    conversation_id = await lead.decide(outcome())
    await stopped_after_booking(conversation_id, lead)
    assert (await the_place()).lead_reply_sent_at is None

    await lead.decide_on(conversation_id, approved=False)

    assert (await the_place()).cancel_reason == "reply_not_sent"


@pytest.mark.asyncio
async def test_sent_then_the_leftover_card_rejected_keeps_the_place(lead, monkeypatch):
    conversation_id = await sent_then_not_saved(lead, monkeypatch)

    await lead.decide_on(conversation_id, approved=False)

    place = await the_place()
    assert place.status == "booked" and place.lead_reply_sent_at is not None


@pytest.mark.asyncio
async def test_sent_then_the_leftover_card_edited_keeps_the_place(lead, monkeypatch):
    conversation_id = await sent_then_not_saved(lead, monkeypatch)

    await lead.decide_on(conversation_id, body=EDITED)

    assert (await the_place()).status == "booked"


@pytest.mark.asyncio
async def test_sent_then_approved_again_and_refused_keeps_the_place(lead, monkeypatch):
    conversation_id = await sent_then_not_saved(lead, monkeypatch)
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    with pytest.raises(HTTPException):
        await lead.decide_on(conversation_id)

    place = await the_place()
    assert place.status == "booked" and place.lead_reply_sent_at is not None
    _, _, notices = await rows()
    assert "cancelled" not in {n.status for n in notices}


# ---------------------------------------------------------------------------
# The host's event links back to the inquiry
# ---------------------------------------------------------------------------


async def host_description(lead: Lead) -> str:
    """The host's event as rendered when it was delivered (captured, in capture mode)."""
    _, _, notices = await rows()
    (event,) = by_kind(notices)["host_event"]
    assert event.status == "captured"
    return event.payload["description"]


@pytest.mark.asyncio
async def test_someone_booking_through_a_leads_link_shows_whose_inquiry_it_was(lead):
    """Jordan asked on Zillow; Alex, booking for them through Jordan's link, is shown
    as booked from Jordan's inquiry, with the team's page for it."""
    await lead.decide(outcome())
    await booked_through_link(lead, at(17))

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))

    assert (
        "- Alex Parker, 412-555-0117, alex@example.com\n"
        "  Booked online from Jordan Lee's Zillow inquiry\n"
        f"  Inquiry: http://localhost:5173/team/leads/threads/{THREAD}\n"
    ) in await host_description(lead)


@pytest.mark.asyncio
async def test_the_latest_name_the_lead_agent_recorded_counts(lead):
    await lead.decide(outcome(lead={"name": "Jordan"}))
    await lead.decide(outcome(lead={"name": "Sam Rivera"}))
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(AgentConversation)
            .where(AgentConversation.id == lead.conversations[0])
            .values(updated_at=func.now() + timedelta(minutes=5))
        )
        await session.commit()
    await booked_through_link(lead, at(17))

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))

    assert "Booked online from Jordan's Zillow inquiry" in await host_description(lead)


@pytest.mark.asyncio
async def test_a_thread_with_no_recorded_name_still_links_its_inquiry(lead):
    """No conversation about the thread, and one whose outcome is not what the lead
    agent writes: the event is written, with the link and no name."""
    stray = f"test-lead-{uuid.uuid4().hex}"
    lead.conversations.append(stray)
    async with AsyncSessionFactory() as session:
        session.add(
            AgentConversation(
                id=stray,
                agent_name="zillow_lead",
                messages=[],
                metadata_={
                    "handler": "zillow_lead_agent",
                    "lead_thread_id": THREAD,
                    "outcome": "?",
                },
            )
        )
        await session.commit()
    await booked_through_link(lead, at(17))

    await lead.world.send_pending(lead.world.now + timedelta(minutes=1))

    description = await host_description(lead)
    assert "  Booked online from a Zillow inquiry\n" in description
    assert f"  Inquiry: http://localhost:5173/team/leads/threads/{THREAD}\n" in description


@pytest.mark.asyncio
async def test_a_lead_name_read_that_fails_answers_no_names_and_keeps_the_session(
    lead, monkeypatch
):
    from api.src.showing_bookings import host_event

    async with AsyncSessionFactory() as session:
        real = session.execute

        async def failing(statement, *args, **kwargs):
            if "agent_conversations" in str(statement):
                raise RuntimeError("database trouble")
            return await real(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", failing)
        assert await host_event.lead_names(session, {THREAD}) == {}
        # The outbox's own transaction is untouched and carries on.
        assert (await real(select(func.count()).select_from(ShowingAttendee))).scalar() == 0
