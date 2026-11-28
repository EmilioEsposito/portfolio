"""The lead inbox: one row per lead thread, and one page per thread.

Conversations are saved by the lead agent's own code (``handle.handle_thread``
and ``handle.apply_approval``, with Gmail and the model stubbed), so the inbox
reads exactly what production stores, including threads from before it existed.
Every name, address, number and thread here is made up.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
)
from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings.models import ShowingAttendee
from api.src.tests.showing_helpers import (
    add_agent,
    add_attendee,
    add_showing,
    clear_showing_data,
    store_settings,
)
from api.src.tests.test_zillow_lead_handle import message, outcome
from api.src.zillow_leads import handle, inbox, listing_data
from api.src.zillow_leads.case import Listing, ListingData
from api.src.zillow_leads.models import ZillowLeadLink
from api.src.zillow_leads.thread import FORM_HEADING, LeadThread, ThreadSummary

SUBJECT = "Casey is requesting information about 12 Example St #3, Pittsburgh, PA, 15224"
FIRST = (
    "I would like to schedule a tour.\n\n"
    f"{FORM_HEADING}\n- Name: Casey Doe\n- Move in: Jan 01, 2027\n- Pets: Yes\n"
    "- Number of Occupants: 2"
)
LISTING = Listing(unit="12-3", address="12 Example St Apt 3, Pittsburgh, PA 15224")
OTHER = Listing(unit="40-1", address="40 Sample Ave Apt 1, Pittsburgh, PA 15224")
USER = SimpleNamespace(id="test-user")


def lead_thread(thread_id: str, *messages) -> LeadThread:
    return LeadThread(mailbox="all@serniacapital.com", thread_id=thread_id, messages=list(messages))


@pytest_asyncio.fixture(autouse=True)
def gmail(monkeypatch):
    """Gmail as the inbox reads it: no thread summaries and no live thread unless a test says."""
    state = SimpleNamespace(summaries={}, live=None)

    async def summaries(thread_ids):
        return {
            thread_id: state.summaries[thread_id]
            for thread_id in thread_ids
            if thread_id in state.summaries
        }

    monkeypatch.setattr(inbox, "thread_summaries", AsyncMock(side_effect=summaries))
    monkeypatch.setattr(
        inbox, "load_lead_thread", AsyncMock(side_effect=lambda thread_id: state.live)
    )
    inbox._summary_cache.clear()
    yield state
    inbox._summary_cache.clear()


@pytest_asyncio.fixture
async def leads(monkeypatch):
    """Runs the lead agent's own saving code with Gmail, the model and push stubbed."""
    monkeypatch.setattr(
        listing_data,
        "load",
        AsyncMock(return_value=listing_data.Loaded(data=ListingData(listings=[LISTING, OTHER]))),
    )
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(
        handle, "send_reply", AsyncMock(return_value=handle.Delivery("Email sent."))
    )
    await clear_showing_data()
    made: list[str] = []
    threads: list[str] = []

    class Leads:
        def thread_id(self) -> str:
            thread = f"t{uuid.uuid4().hex[:15]}"
            threads.append(thread)
            return thread

        async def run(self, thread_id: str, result, *messages) -> str:
            cid = f"test-inbox-{uuid.uuid4().hex}"
            made.append(cid)
            monkeypatch.setattr(
                handle,
                "load_lead_thread",
                AsyncMock(return_value=lead_thread(thread_id, *messages)),
            )
            monkeypatch.setattr(handle, "respond", AsyncMock(return_value=result))
            await handle.handle_thread(thread_id, conversation_id=cid, metadata={})
            return cid

        async def decide(self, cid: str, approved: bool = True, **override) -> None:
            async with AsyncSessionFactory() as session:
                messages = await get_conversation_messages(cid, None, session=session)
                (card,) = extract_pending_approval_from_messages(messages)
                row = await session.get(AgentConversation, cid)
                await handle.apply_approval(
                    session,
                    conversation_id=cid,
                    metadata=row.metadata_,
                    decisions=[
                        ApprovalDecision(
                            tool_call_id=card["tool_call_id"],
                            approved=approved,
                            override_args=override or None,
                        )
                    ],
                    approver="Pat Doe",
                )

        async def age(self, cid: str, minutes: int) -> None:
            """Move a conversation's last change back, so the order of activity is known."""
            async with AsyncSessionFactory() as session:
                row = await session.get(AgentConversation, cid)
                row.updated_at = datetime.now(UTC) - timedelta(minutes=minutes)
                row.created_at = row.updated_at
                await session.commit()

    yield Leads()
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id.in_(made)))
        await session.execute(delete(ZillowLeadLink).where(ZillowLeadLink.thread_id.in_(threads)))
        await session.commit()
    await clear_showing_data()


def app(allowed: bool = True) -> FastAPI:
    application = FastAPI()
    application.include_router(inbox.router)

    async def caller():
        if not allowed:
            raise HTTPException(403, "A verified Sernia account is required.")
        return USER

    application.dependency_overrides[inbox.verify_serniacapital_user] = caller
    return application


async def get(path: str, allowed: bool = True) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(allowed)), base_url="http://test"
    ) as client:
        return await client.get(path)


async def casey(leads, *, booked: bool = True) -> tuple[str, str, str]:
    """Casey's thread: a reply approved and sent, then a new draft waiting, then a booking.

    The incident the inbox exists for: the booking arrives after the waiting
    draft was written.
    """
    thread_id = leads.thread_id()
    first = message(0, subject=SUBJECT, body=FIRST)
    one = await leads.run(thread_id, outcome(lead={"name": "Casey Doe", "unit": "12-3"}), first)
    await leads.decide(one)
    two = await leads.run(
        thread_id,
        outcome(lead={"name": "Casey Doe", "unit": "12-3", "phone": "412-555-0123"}),
        first,
        message(10, sernia=True, subject=f"Re: {SUBJECT}", body="Hi Casey,\n\nBook here."),
        message(20, subject=f"Re: {SUBJECT}", body="Thanks! Is Sunday possible?"),
    )
    if booked:
        showing = await add_showing(
            await add_agent(), unit="12-3", starts_at=datetime.now(UTC) + timedelta(days=2)
        )
        await add_attendee(showing, name="Jordan", thread=thread_id)
    return thread_id, one, two


# The list


@pytest.mark.asyncio
async def test_a_thread_reads_like_gmails_list_with_its_status_and_booking(leads):
    thread_id, _, _ = await casey(leads)

    response = await get("/zillow-leads/threads")

    assert response.status_code == 200
    (row,) = [row for row in response.json()["threads"] if row["thread_id"] == thread_id]
    assert row["first_name"] == "Casey"
    assert row["name"] == "Casey Doe"
    assert row["subject"] == SUBJECT
    assert row["unit"] == "12-3"
    assert row["address"] == LISTING.address
    # The inquiry, our reply, their answer.
    assert row["email_count"] == 3
    assert row["latest_from_sernia"] is False
    assert row["snippet"] == "Thanks! Is Sunday possible?"
    assert row["status"] == "draft_waiting"
    assert row["waiting_since"] is not None
    assert row["showing"]["state"] == "booked"
    assert row["showing"]["unit"] == "12-3"


@pytest.mark.asyncio
async def test_threads_are_ordered_by_latest_activity_and_limited(leads):
    older = leads.thread_id()
    cid = await leads.run(older, outcome("no_reply"), message(0, subject=SUBJECT))
    await leads.age(cid, 60)
    newer = leads.thread_id()
    await leads.run(newer, outcome("escalate"), message(0, subject=SUBJECT))

    body = (await get("/zillow-leads/threads")).json()
    ours = [row["thread_id"] for row in body["threads"] if row["thread_id"] in (older, newer)]
    assert ours == [newer, older]
    statuses = {row["thread_id"]: row["status"] for row in body["threads"]}
    assert statuses[newer] == "needs_team"
    assert statuses[older] == "no_reply_needed"

    limited = (await get("/zillow-leads/threads?limit=1")).json()
    assert len(limited["threads"]) == 1 and limited["has_more"] is True


@pytest.mark.asyncio
async def test_a_sent_reply_counts_as_an_email_and_the_thread_reads_replied(leads):
    thread_id = leads.thread_id()
    cid = await leads.run(thread_id, outcome(), message(0, subject=SUBJECT, body=FIRST))
    await leads.decide(cid, body="Hello Casey,\n\nEdited by hand.")

    (row,) = [
        row
        for row in (await get("/zillow-leads/threads")).json()["threads"]
        if row["thread_id"] == thread_id
    ]
    assert row["status"] == "replied"
    assert row["email_count"] == 2
    assert row["latest_from_sernia"] is True
    assert row["snippet"] == "Hello Casey, Edited by hand."
    assert row["showing"] is None


# The lead page


@pytest.mark.asyncio
async def test_the_lead_page_has_the_lead_the_emails_and_the_form(leads):
    thread_id, one, two = await casey(leads)

    response = await get(f"/zillow-leads/threads/{thread_id}")

    assert response.status_code == 200
    page = response.json()
    assert page["gmail_url"] == (
        f"https://mail.google.com/mail/u/?authuser=all%40serniacapital.com#all/{thread_id}"
    )
    assert page["phone"] == "412-555-0123"
    assert page["latest_conversation_id"] == two
    assert {fact["label"]: fact["value"] for fact in page["facts"]} == {
        "Move in": "Jan 01, 2027",
        "Pets": "Yes",
        "Number of Occupants": "2",
    }
    emails = page["emails"]
    assert [email["from_sernia"] for email in emails] == [False, True, False]
    # The lead's own words, with the form apart.
    assert emails[0]["text"] == "I would like to schedule a tour."
    assert emails[0]["form"][0] == {"label": "Name", "value": "Casey Doe"}
    assert emails[2]["text"] == "Thanks! Is Sunday possible?"


@pytest.mark.asyncio
async def test_the_lead_page_lists_what_waits_and_each_run(leads):
    thread_id, one, two = await casey(leads)

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    (card,) = page["waiting"]
    assert card["conversation_id"] == two and card["kind"] == "draft"
    assert card["tool_name"] == handle.SEND_REPLY
    assert card["args"]["body"] == "Hello,\n\nSernia Capital Team"
    assert card["chat_path"] == f"/team/chat?id={two}"
    runs = page["runs"]
    assert [run["conversation_id"] for run in runs] == [two, one]
    assert runs[0]["result"] == "waiting" and runs[0]["emails_seen"] == 3
    assert runs[1]["result"] == "sent"
    assert runs[1]["decided_by"] == "Pat Doe" and runs[1]["decided_at"]
    assert runs[1]["reason"] == "Because."


@pytest.mark.asyncio
async def test_the_lead_page_lists_the_threads_showings(leads):
    thread_id, _, _ = await casey(leads)
    async with AsyncSessionFactory() as session:
        attendee = (
            await session.scalars(
                select(ShowingAttendee).where(ShowingAttendee.zillow_thread_id == thread_id)
            )
        ).one()

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    (showing,) = page["showings"]
    assert showing["attendee_id"] == attendee.id
    assert showing["unit"] == "12-3"
    assert showing["status"] == "booked" and showing["past"] is False
    assert showing["booked_by"] == "Jordan"
    assert showing["source"] == "page" and showing["details_added"] is True
    assert showing["showings_path"] == f"/team/leads/bookings#showing-{attendee.showing_id}"


@pytest.mark.asyncio
async def test_a_place_booked_for_a_reply_never_sent_is_left_out(leads):
    thread_id, _, _ = await casey(leads, booked=False)
    showing = await add_showing(await add_agent(), unit="12-3")
    attendee = await add_attendee(showing, thread=thread_id, source="lead_agent")
    async with AsyncSessionFactory() as session:
        row = await session.get(ShowingAttendee, attendee)
        row.status, row.cancel_reason = "cancelled", "reply_not_sent"
        await session.commit()

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()
    assert page["showings"] == []
    assert page["thread"]["showing"] is None


@pytest.mark.asyncio
async def test_the_booking_link_carries_the_threads_token(leads, monkeypatch):
    thread_id, _, _ = await casey(leads, booked=False)
    rentals = [
        RentalListing(unit="12-3", address=LISTING.address),
        RentalListing(unit="40-1", address=OTHER.address),
        RentalListing(unit="77-2", address="77 Other Rd"),
    ]

    async def read_listings(session):
        return rentals

    async def read_rules(session, lock=False):
        return ShowingRules()

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    monkeypatch.setenv("SHOWING_PUBLIC_BASE_URL", "https://homes.example.com")
    await store_settings(public_booking_enabled=True, send_mode="capture")

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    async with AsyncSessionFactory() as session:
        token = await session.scalar(
            select(ZillowLeadLink.token).where(ZillowLeadLink.thread_id == thread_id)
        )
    assert token
    # Only the units the case offered, each with the thread's own token.
    assert [(link["unit"], link["url"]) for link in page["booking_links"]] == [
        ("12-3", f"https://homes.example.com/showings/12-3?t={token}"),
        ("40-1", f"https://homes.example.com/showings/40-1?t={token}"),
    ]
    assert page["booking_links_problem"] is None


@pytest.mark.asyncio
async def test_with_public_booking_off_the_page_says_why_there_is_no_link(leads, monkeypatch):
    thread_id, _, _ = await casey(leads, booked=False)

    async def read_listings(session):
        return [RentalListing(unit="12-3", address=LISTING.address)]

    async def read_rules(session, lock=False):
        return ShowingRules()

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    await store_settings(public_booking_enabled=False)

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()
    (link,) = page["booking_links"]
    assert link["url"] is None
    assert "switched off" in link["no_link_because"]


@pytest.mark.asyncio
async def test_an_unknown_thread_is_not_found_and_a_strange_id_is_refused(leads):
    assert (await get("/zillow-leads/threads/t0000000000000000")).status_code == 404
    assert (await get("/zillow-leads/threads/a%20b")).status_code == 422


@pytest.mark.asyncio
async def test_the_inbox_needs_a_sernia_account(leads):
    assert (await get("/zillow-leads/threads", allowed=False)).status_code == 403


# How a run ended and where a thread stands


def run(**fields) -> inbox.Run:
    base = {
        "id": "c",
        "created_at": datetime(2026, 5, 4, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 4, tzinfo=UTC),
        "thread_id": "t",
        "outcome": {"action": "reply"},
        "approval": [],
        "auto_delivery": {},
        "hand_off": {},
    }
    return inbox.Run(**{**base, **fields})


def test_how_each_run_ended():
    at = "2026-05-04T13:00:00+00:00"
    draft = {"tool_name": handle.SEND_REPLY}
    task = {"tool_name": handle.ASK_TEAM}
    assert inbox.run_result(run(pending=[draft])).result == "waiting"
    assert inbox.run_result(run(pending=[task])).result == "needs_team"
    assert inbox.run_result(run(auto_delivery={"status": "sent", "at": at})).result == (
        "sent_automatically"
    )
    edited = run(approval=[{**draft, "approved": True, "edited": True, "by": "Pat", "at": at}])
    assert inbox.run_result(edited) == inbox.Result(
        "sent_edited", "Pat", datetime(2026, 5, 4, 13, tzinfo=UTC)
    )
    assert inbox.run_result(run(approval=[{**draft, "approved": False}])).result == "rejected"
    assert inbox.run_result(run(approval=[{**task, "approved": True}])).result == "handled"
    # A hand-off rejects the draft too; the hand-off is what happened.
    handed = run(approval=[{**draft, "approved": False}], hand_off={"to": "chat-1", "by": "Pat"})
    assert inbox.run_result(handed).result == "handed_off"
    assert inbox.run_result(run(outcome={"action": "no_reply"})).result == "no_reply"


def test_a_reply_captured_outside_production_reads_captured_never_sent():
    at = "2026-05-04T13:00:00+00:00"
    when = datetime(2026, 5, 4, 13, tzinfo=UTC)
    approved = {"tool_name": handle.SEND_REPLY, "approved": True, "by": "Pat", "at": at}
    captured = run(approval=[{**approved, "sent_body": "Hello", "captured": True}])
    assert inbox.run_result(captured) == inbox.Result("captured", "Pat", when)
    assert inbox.sent_replies(captured) == [inbox.Sent("Hello", when, "captured")]
    automatic = run(auto_delivery={"status": "captured", "body": "Hi", "at": at})
    assert inbox.run_result(automatic) == inbox.Result("captured", None, when)
    assert inbox.sent_replies(automatic) == [inbox.Sent("Hi", when, "captured")]
    assert inbox.thread_status([captured]) == "handled"


def test_a_captured_reply_never_claims_an_email_sent_from_gmail():
    """Gmail has no copy of a captured reply: it is shown on its own, and a person's
    Gmail reply a minute later keeps its own mark (none)."""
    at = datetime(2026, 5, 4, 13, tzinfo=UTC)
    approved = {"tool_name": handle.SEND_REPLY, "approved": True, "by": "Pat"}
    captured = run(
        approval=[{**approved, "sent_body": "Draft", "captured": True, "at": at.isoformat()}]
    )
    live = [
        {
            "sent_at": (at - timedelta(hours=1)).isoformat(),
            "from_sernia": False,
            "subject": "Tour",
            "body": "Hi",
        },
        {
            "sent_at": (at + timedelta(minutes=1)).isoformat(),
            "from_sernia": True,
            "subject": "Re: Tour",
            "body": "Typed in Gmail",
        },
    ]

    found = inbox.emails([captured], live)

    assert [(email.text, email.sent_by) for email in found] == [
        ("Hi", None),
        ("Draft", "captured"),
        ("Typed in Gmail", None),
    ]
    stored = inbox.emails([captured])
    assert [(email.text, email.sent_by) for email in stored] == [("Draft", "captured")]


def test_a_draft_anywhere_in_the_thread_comes_first():
    sent = run(approval=[{"tool_name": handle.SEND_REPLY, "approved": True}])
    asking = run(pending=[{"tool_name": handle.ASK_TEAM}])
    drafted = run(pending=[{"tool_name": handle.SEND_REPLY}])
    assert inbox.thread_status([drafted, asking]) == "draft_waiting"
    assert inbox.thread_status([sent, asking]) == "needs_team"
    assert inbox.thread_status([asking, sent]) == "needs_team"
    assert inbox.thread_status([sent]) == "replied"
    assert inbox.thread_status([run(hand_off={"to": "chat-1"})]) == "handed_off"


def test_the_form_is_split_from_the_leads_words():
    words, facts = inbox.split_form(FIRST)
    assert words == "I would like to schedule a tour."
    assert [(fact.label, fact.value) for fact in facts] == [
        ("Name", "Casey Doe"),
        ("Move in", "Jan 01, 2027"),
        ("Pets", "Yes"),
        ("Number of Occupants", "2"),
    ]
    assert inbox.split_form("Just a question.") == ("Just a question.", [])
    # Zillow's tracking id at the end of a relayed message is not the lead's words.
    relayed = (
        "We have cats!  0f0e0d0c-1b2a-11f1-9a43-000000000000\n\n"
        + FORM_HEADING
        + "\n- Listing: 12 Example St"
    )
    assert inbox.split_form(relayed) == (
        "We have cats!",
        [inbox.FormFact(label="Listing", value="12 Example St")],
    )


@pytest.mark.parametrize(
    "unit, address, expected",
    [
        ("12-3", "", "12-3"),
        # The agent sometimes writes the address where the unit belongs.
        ("12 Example St Apt 3", "", "12-3"),
        (None, "12 Example St #3, Pittsburgh, PA, 15224", "12-3"),
        (None, "40 Sample Ave Unit 1, Pittsburgh, PA, 15224", "40-1"),
        (None, "99 Nowhere St #9, Pittsburgh, PA, 15224", None),
    ],
)
def test_the_listing_asked_about_is_found_by_unit_or_address(unit, address, expected):
    listings = [LISTING.model_dump(), OTHER.model_dump()]
    found = inbox._listing_for(listings, unit, address)
    assert (found or {}).get("unit") == expected


# A draft written before the lead booked (the incident the inbox exists for)


@pytest.mark.asyncio
async def test_a_draft_written_before_a_booking_says_so_on_its_card_and_row(leads):
    thread_id, _, two = await casey(leads)

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    (card,) = page["waiting"]
    (change,) = card["showing_changes"]
    assert change["kind"] == "booked"
    assert change["unit"] == "12-3" and change["booked_by"] == "Jordan"
    assert page["thread"]["draft_outdated"] is True


@pytest.mark.asyncio
async def test_a_booking_the_draft_already_knew_of_is_not_a_change(leads):
    thread_id = leads.thread_id()
    showing = await add_showing(
        await add_agent(), unit="12-3", starts_at=datetime.now(UTC) + timedelta(days=2)
    )
    await add_attendee(showing, name="Jordan", thread=thread_id)
    await leads.run(thread_id, outcome(), message(0, subject=SUBJECT, body=FIRST))

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    (card,) = page["waiting"]
    assert card["showing_changes"] == []
    assert page["thread"]["draft_outdated"] is False
    assert page["thread"]["showing"]["state"] == "booked"


@pytest.mark.asyncio
async def test_a_cancellation_after_the_draft_is_a_change_too(leads):
    thread_id = leads.thread_id()
    showing = await add_showing(
        await add_agent(), unit="12-3", starts_at=datetime.now(UTC) + timedelta(days=2)
    )
    attendee = await add_attendee(showing, name="Jordan", thread=thread_id)
    await leads.run(thread_id, outcome(), message(0, subject=SUBJECT, body=FIRST))
    async with AsyncSessionFactory() as session:
        row = await session.get(ShowingAttendee, attendee)
        row.status, row.cancel_reason = "cancelled", "prospect"
        row.cancelled_at = datetime.now(UTC)
        await session.commit()

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    (change,) = page["waiting"][0]["showing_changes"]
    assert (change["kind"], change["cancelled_by"]) == ("cancelled", "prospect")
    assert page["thread"]["draft_outdated"] is True


@pytest.mark.asyncio
async def test_the_team_homes_approvals_name_a_leads_thread(leads):
    from api.src.agent_conversations.identity import AGENT_NAME
    from api.src.agent_conversations.models import list_pending_conversations

    thread_id, _, two = await casey(leads, booked=False)

    pending = await list_pending_conversations(agent_name=AGENT_NAME, limit=200)

    (row,) = [row for row in pending if row["conversation_id"] == two]
    # The team home links it to the lead's page rather than the chat.
    assert row["lead_thread_id"] == thread_id


# Replies written in Gmail, which the stored runs see only with the lead's next email


@pytest.mark.asyncio
async def test_the_list_takes_gmails_count_and_latest_email_when_gmail_has_more(leads, gmail):
    thread_id = leads.thread_id()
    await leads.run(thread_id, outcome("escalate"), message(0, subject=SUBJECT, body=FIRST))
    # The escalation was answered in Gmail and marked handled.
    answered = datetime.now(UTC)
    gmail.summaries[thread_id] = ThreadSummary(
        count=2,
        latest_at=answered,
        latest_snippet="Hi Casey, yes & parking is free.",
        latest_from_sernia=True,
    )

    (row,) = [
        row
        for row in (await get("/zillow-leads/threads")).json()["threads"]
        if row["thread_id"] == thread_id
    ]

    assert row["email_count"] == 2
    assert row["latest_from_sernia"] is True
    assert row["snippet"] == "Hi Casey, yes & parking is free."


@pytest.mark.asyncio
async def test_without_gmail_the_list_shows_what_is_stored(leads, gmail):
    thread_id = leads.thread_id()
    await leads.run(thread_id, outcome("escalate"), message(0, subject=SUBJECT, body=FIRST))
    inbox.thread_summaries.side_effect = RuntimeError("Gmail is down")

    response = await get("/zillow-leads/threads")

    assert response.status_code == 200
    (row,) = [row for row in response.json()["threads"] if row["thread_id"] == thread_id]
    assert row["email_count"] == 1 and row["snippet"] == "I would like to schedule a tour."


@pytest.mark.asyncio
async def test_the_lead_page_reads_the_thread_from_gmail_when_gmail_has_more(leads, gmail):
    thread_id = leads.thread_id()
    first = message(0, subject=SUBJECT, body=FIRST)
    cid = await leads.run(thread_id, outcome(), first)
    await leads.decide(cid)
    async with AsyncSessionFactory() as session:
        sent_at = datetime.fromisoformat(
            (await session.get(AgentConversation, cid)).metadata_["approval"][0]["at"]
        )
    gmail.summaries[thread_id] = ThreadSummary(
        count=3, latest_at=sent_at, latest_snippet="Also", latest_from_sernia=True
    )
    gmail.live = lead_thread(
        thread_id,
        first,
        # Gmail's copy of the reply the card sent, a few seconds later.
        message(
            1,
            sernia=True,
            sent_at=sent_at + timedelta(seconds=3),
            body="Hello,\n\nSernia Capital Team",
        ),
        message(
            2, sernia=True, sent_at=sent_at + timedelta(minutes=30), body="Also: parking is free."
        ),
    )

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    assert page["emails_live"] is True
    assert [email["text"] for email in page["emails"]] == [
        "I would like to schedule a tour.",
        "Hello,\n\nSernia Capital Team",
        "Also: parking is free.",
    ]
    assert [email["sent_by"] for email in page["emails"]] == [None, "inbox", None]
    assert page["thread"]["email_count"] == 3


@pytest.mark.asyncio
async def test_the_lead_page_does_not_read_the_thread_when_gmail_has_nothing_new(leads, gmail):
    thread_id = leads.thread_id()
    await leads.run(thread_id, outcome("escalate"), message(0, subject=SUBJECT, body=FIRST))
    gmail.summaries[thread_id] = ThreadSummary(
        count=1, latest_at=datetime.now(UTC), latest_snippet="x", latest_from_sernia=False
    )

    page = (await get(f"/zillow-leads/threads/{thread_id}")).json()

    assert page["emails_live"] is False
    inbox.load_lead_thread.assert_not_called()


def test_a_gmail_summary_counts_sent_and_received_emails_but_not_drafts():
    from api.src.zillow_leads import thread as thread_module

    def gmail_message(ms, sender, labels=("INBOX",), snippet=""):
        return {
            "internalDate": str(ms),
            "labelIds": list(labels),
            "snippet": snippet,
            "payload": {"headers": [{"name": "From", "value": sender}]},
        }

    summary = thread_module._summary(
        {
            "messages": [
                gmail_message(1_000, "Zillow <noreply@mail.zillow.com>"),
                gmail_message(
                    2_000, "Sernia <all@serniacapital.com>", ("SENT",), "Hi &amp; welcome"
                ),
                gmail_message(3_000, "Sernia <all@serniacapital.com>", ("DRAFT",), "unsent"),
            ]
        }
    )

    assert summary == thread_module.ThreadSummary(
        count=2,
        latest_at=datetime.fromtimestamp(2, tz=UTC),
        latest_snippet="Hi & welcome",
        latest_from_sernia=True,
    )
    assert thread_module._summary({"messages": []}) is None
