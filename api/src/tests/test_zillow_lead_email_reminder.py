"""The reminder by email for a showing agreed in a Zillow lead's thread.

A lead the lead agent booked in their email thread, whose confirming reply was
sent and who has not added a mobile with consent, gets the evening-before
reminder by email in the same thread instead of a text. These run the lead
agent's code and the job every minute (``holds.send_pending``) against the test
database, with the stand-ins of ``showing_public.py`` and the lead fixtures of
``test_zillow_lead_bookings.py``: Gmail is ``RecordingEmailSender``, and the
model, Gmail and push are mocked. Names, numbers and threads are made up.

The showing is Wednesday at 4 PM; the evening before is Tuesday at 6 PM.
"""

# The ``world`` and ``lead`` fixtures are imported, so each test's parameter of
# the same name is that fixture, not a redefinition.
# ruff: noqa: F811

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update

from api.src.agent_conversations.models import AgentConversation
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import delivery_checks, gmail_thread, lead_email, outbox, wording
from api.src.showing_bookings.models import ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import NotSent, ThreadRoute
from api.src.tests.showing_helpers import LOCAL, store_settings
from api.src.tests.showing_public import (
    ALEX_MOBILE,
    TUESDAY,
    UNIT,
    WEDNESDAY,
    at,
    by_kind,
    rows,
)
from api.src.tests.test_zillow_lead_bookings import (  # noqa: F401  (fixtures)
    PUBLIC,
    THREAD,
    Lead,
    details,
    lead,
    outcome,
    world,
)
from api.src.zillow_leads import handle, lead_links
from api.src.zillow_leads.relay import relay_address

KIND = delivery_checks.LEAD_EMAIL_REMINDER_KIND
RELAY = f"{THREAD}@convo.zillow.com"
SUBJECT = "Re: 320 Example St inquiry"
MESSAGE_ID = f"{THREAD}-m10"


def tuesday(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TUESDAY, time(hour, minute), LOCAL)


EVENING = tuesday(18)


async def booked_by_email(lead: Lead) -> str:
    """Jordan booked for Wednesday at 4 by an approved reply, sent; the conversation id."""
    conversation_id = await lead.decide(outcome())
    await lead.decide_on(conversation_id)
    return conversation_id


async def email_notices() -> list[ShowingNotice]:
    _, _, notices = await rows()
    return by_kind(notices).get(KIND, [])


async def set_route(conversation_id: str, **changes) -> None:
    """Change what the conversation stores, as metadata."""
    async with AsyncSessionFactory() as session:
        row = await session.get(AgentConversation, conversation_id)
        metadata = dict(row.metadata_)
        for key, value in changes.items():
            metadata[key] = value
        row.metadata_ = metadata
        await session.commit()


async def go_live(**fields) -> None:
    await store_settings(lead_agent_uses_open_times=True, send_mode="test", **fields)


def live_to_the_thread(monkeypatch) -> None:
    """The thread's relay address is a test recipient, so the email really goes (to the stand-in)."""
    monkeypatch.setenv("SHOWING_TEST_EMAILS", RELAY)


# ---------------------------------------------------------------------------
# Who gets it, and when
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queued_at_6_pm_for_a_place_whose_reply_was_sent(lead):
    conversation_id = await booked_by_email(lead)

    await lead.world.send_pending(tuesday(17, 59))
    assert await email_notices() == []

    report = await lead.world.send_pending(EVENING)

    assert report.lead_email_reminders == 1 and report.prospect_reminders == 0
    (notice,) = await email_notices()
    assert (notice.channel, notice.status, notice.recipient) == ("email", "captured", RELAY)
    assert notice.recipient_key == f"attendee:{notice.attendee_id}"
    _, (jordan,), _ = await rows()
    token = await lead_links.token_for_thread(THREAD)
    assert notice.payload == {
        "subject": SUBJECT,
        "body": (
            "Hello,\n\n"
            "A reminder of your in-person showing on Wed, Apr 14 at 4:00 PM at "
            "320 Example St (unit 320-03).\n\n"
            "If you can no longer make it, please cancel here: "
            f"https://serniacapital.com/showings/cancel/{jordan.cancel_token}\n\n"
            "To get a text reminder and a calendar invite, add your details here: "
            f"https://serniacapital.com/showings/{UNIT}?t={token}\n\n"
            "Sernia Capital Team"
        ),
        "conversation_id": conversation_id,
        "reply_to_message_id": MESSAGE_ID,
        "daytime_timezone": "America/New_York",
    }
    # Captured: nothing went to Gmail, and nothing was texted to the lead.
    assert lead.world.emails.sent == []
    assert all(text.to != jordan.mobile for text in lead.world.texts.sent)


def test_the_wording_is_plain_text_from_a_fixed_template():
    visit = wording.Visit(
        unit=UNIT,
        address="320 Example St",
        starts_at=at(16),
        ends_at=at(16, 30),
        timezone="America/New_York",
    )
    body = wording.lead_email_reminder_text(visit, "https://c.example.com", "https://d.example.com")
    assert "<" not in body and wording.LEAD_EMAIL_VERSION == "v1"
    assert "https://c.example.com" in body and "https://d.example.com" in body


@pytest.mark.asyncio
async def test_a_place_booked_after_6_pm_gets_none(lead):
    lead.world.now = tuesday(18, 30)
    await booked_by_email(lead)

    await lead.world.send_pending(tuesday(18, 31))
    await lead.world.send_pending(tuesday(20))

    assert await email_notices() == []


@pytest.mark.asyncio
async def test_none_while_the_reply_was_never_sent(lead):
    conversation_id = await lead.decide(outcome())
    # A process booked the place for this reply and stopped before sending.
    from api.src.showing_bookings import lead_bookings

    await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=outcome().lead,
        tour=outcome().tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )

    await lead.world.send_pending(EVENING)

    _, (jordan,), _ = await rows()
    assert jordan.status == "booked" and jordan.lead_reply_sent_at is None
    assert await email_notices() == []


@pytest.mark.asyncio
async def test_none_while_the_reply_may_or_may_not_have_gone(lead, monkeypatch):
    """An uncertain send: the place stands, marked, and the lead may never have seen
    the confirmation. The team checks the thread; no reminder goes until a sent
    reply confirms it."""
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))
    conversation_id = await lead.decide(outcome())
    with pytest.raises(TimeoutError):
        await lead.decide_on(conversation_id)

    await lead.world.send_pending(EVENING)

    _, (jordan,), _ = await rows()
    assert jordan.attention_reason == "reply_uncertain" and jordan.lead_reply_sent_at is None
    assert await email_notices() == []


@pytest.mark.asyncio
async def test_none_for_a_lead_who_added_their_details_they_get_the_text(lead):
    await booked_by_email(lead)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )
    assert answer.status_code == 201, answer.text

    report = await lead.world.send_pending(EVENING)

    assert (report.lead_email_reminders, report.prospect_reminders) == (0, 1)
    assert await email_notices() == []
    _, _, notices = await rows()
    (text,) = by_kind(notices)["prospect_reminder"]
    assert text.recipient == ALEX_MOBILE


@pytest.mark.asyncio
async def test_details_added_before_it_goes_cancel_it_and_the_text_goes_instead(lead):
    await booked_by_email(lead)
    # The job missed the evening; at 9:30 PM the email is queued and waits for 8 AM.
    await lead.world.send_pending(tuesday(21, 30))
    (notice,) = await email_notices()
    assert notice.status == "pending"

    lead.world.now = tuesday(22)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )
    assert answer.status_code == 201, answer.text

    morning = datetime.combine(WEDNESDAY, time(8), LOCAL)
    await lead.world.send_pending(morning)
    await lead.world.send_pending(morning + timedelta(minutes=1))

    (notice,) = await email_notices()
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.TEXTED_INSTEAD)
    _, _, notices = await rows()
    assert [n.status for n in by_kind(notices)["prospect_reminder"]] == ["captured"]
    assert lead.world.emails.sent == []


@pytest.mark.asyncio
async def test_details_added_after_it_went_bring_no_reminder_text_as_well(lead):
    await booked_by_email(lead)
    await lead.world.send_pending(EVENING)
    assert [n.status for n in await email_notices()] == ["captured"]

    lead.world.now = tuesday(19)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/units/{UNIT}/lead-booking/details", json=details(token)
        )
    assert answer.status_code == 201, answer.text
    await lead.world.send_pending(tuesday(19, 1))
    await lead.world.send_pending(tuesday(20))

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert "prospect_reminder" not in kinds  # one reminder per place
    assert [n.status for n in kinds["prospect_confirmation"]] == ["captured"]


@pytest.mark.asyncio
async def test_none_after_a_cancellation(lead):
    await booked_by_email(lead)
    _, (jordan,), _ = await rows()
    async with lead.world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/bookings/cancel", json={"cancel_token": jordan.cancel_token}
        )
    assert answer.status_code == 200, answer.text

    await lead.world.send_pending(EVENING)

    assert await email_notices() == []


@pytest.mark.asyncio
async def test_a_cancellation_after_it_was_queued_stops_it(lead):
    await booked_by_email(lead)
    await lead.world.send_pending(tuesday(21, 30))  # queued, waits for 8 AM
    lead.world.now = tuesday(22)
    await lead.decide(outcome("no_reply", status="cancelled"))

    await lead.world.send_pending(datetime.combine(WEDNESDAY, time(8), LOCAL))

    (notice,) = await email_notices()
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.BOOKING_CANCELLED)
    assert lead.world.emails.sent == []


@pytest.mark.asyncio
async def test_none_with_the_switch_off(lead):
    await booked_by_email(lead)
    await store_settings(lead_agent_uses_open_times=False)

    report = await lead.world.send_pending(EVENING)

    assert report.lead_email_reminders == 0
    assert await email_notices() == []


@pytest.mark.asyncio
async def test_switched_off_after_it_was_queued_it_does_not_go(lead):
    await booked_by_email(lead)
    await lead.world.send_pending(tuesday(21, 30))
    await store_settings(lead_agent_uses_open_times=False)

    await lead.world.send_pending(datetime.combine(WEDNESDAY, time(8), LOCAL))

    (notice,) = await email_notices()
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.SWITCH_OFF)


@pytest.mark.asyncio
async def test_held_to_8_am_to_9_pm(lead):
    await booked_by_email(lead)
    morning = datetime.combine(WEDNESDAY, time(8), LOCAL)

    await lead.world.send_pending(tuesday(21, 30))
    (notice,) = await email_notices()
    assert (notice.status, notice.not_before) == ("pending", morning)

    await lead.world.send_pending(morning - timedelta(minutes=1))
    assert [n.status for n in await email_notices()] == ["pending"]

    await lead.world.send_pending(morning)
    assert [n.status for n in await email_notices()] == ["captured"]


# ---------------------------------------------------------------------------
# Where it goes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_route_is_read_from_the_conversation_when_it_goes(lead, monkeypatch):
    conversation_id = await booked_by_email(lead)
    await lead.world.send_pending(tuesday(21, 30))  # queued to the stored route, waits
    other = f"{THREAD}-later@convo.zillow.com"
    await set_route(
        conversation_id,
        reply_route={"to": other, "subject": "Re: Later", "reply_to_message_id": "later-m1"},
    )
    # The attendee's own fields play no part.
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAttendee).values(email="someone@example.com", mobile=ALEX_MOBILE)
        )
        await session.commit()
    monkeypatch.setenv("SHOWING_TEST_EMAILS", other)
    await go_live()

    await lead.world.send_pending(datetime.combine(WEDNESDAY, time(8), LOCAL))

    (sent,) = lead.world.emails.sent
    assert sent.route == ThreadRoute(to=other, subject="Re: Later", reply_to_message_id="later-m1")
    (notice,) = await email_notices()
    assert (notice.status, notice.recipient, notice.provider_id) == ("sent", other, "gmail-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("break_it", "problem"),
    [
        ("no_conversation", lead_email.CONVERSATION_MISSING),
        ("no_route", lead_email.NO_ROUTE),
        ("not_relay", lead_email.NOT_RELAY),
        ("own_address", lead_email.NOT_RELAY),
        ("other_thread", lead_email.OTHER_THREAD),
        ("not_lead_agent", lead_email.NOT_LEAD_CONVERSATION),
        ("no_conversation_id", lead_email.NO_CONVERSATION),
    ],
)
async def test_a_missing_or_unsafe_route_fails_for_the_team_and_sends_nothing(
    lead, monkeypatch, break_it, problem
):
    conversation_id = await booked_by_email(lead)
    route = {"to": RELAY, "subject": SUBJECT, "reply_to_message_id": MESSAGE_ID}
    if break_it == "no_conversation":
        async with AsyncSessionFactory() as session:
            await session.execute(
                delete(AgentConversation).where(AgentConversation.id == conversation_id)
            )
            await session.commit()
    elif break_it == "no_route":
        await set_route(conversation_id, reply_route=None)
    elif break_it == "not_relay":
        await set_route(conversation_id, reply_route={**route, "to": "jordan@example.com"})
    elif break_it == "own_address":
        await set_route(conversation_id, reply_route={**route, "to": "noreply@mail.zillow.com"})
    elif break_it == "other_thread":
        await set_route(conversation_id, lead_thread_id="thread-casey-0102")
    elif break_it == "not_lead_agent":
        await set_route(conversation_id, handler="someone_else")
    else:
        async with AsyncSessionFactory() as session:
            await session.execute(update(ShowingAttendee).values(lead_conversation_id=None))
            await session.commit()
    # Even an address listed as a test recipient, in live mode, is not sent to.
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{RELAY},jordan@example.com")
    await go_live()

    await lead.world.send_pending(EVENING)

    (notice,) = await email_notices()
    assert (notice.status, notice.provider_status, notice.last_error) == (
        "failed",
        problem.reason,
        problem.detail,
    )
    assert notice.alert_reason == "failed" and notice.alerted_at == EVENING
    assert lead.world.emails.sent == []
    # Never tried again.
    await lead.world.send_pending(EVENING + timedelta(minutes=1))
    assert lead.world.emails.sent == []


@pytest.mark.asyncio
async def test_a_route_lost_after_queuing_fails_at_delivery(lead, monkeypatch):
    conversation_id = await booked_by_email(lead)
    await lead.world.send_pending(tuesday(21, 30))
    await set_route(conversation_id, reply_route=None)
    live_to_the_thread(monkeypatch)
    await go_live()

    await lead.world.send_pending(datetime.combine(WEDNESDAY, time(8), LOCAL))

    (notice,) = await email_notices()
    assert (notice.status, notice.provider_status) == ("failed", "no_route")
    assert lead.world.emails.sent == []


def test_the_relay_rule_is_the_lead_agents_own():
    assert relay_address(" Lead-1@Convo.Zillow.com ") == "lead-1@convo.zillow.com"
    assert relay_address("lead@zillow.com") == "lead@zillow.com"
    for address in (
        "lead@example.com",
        "lead@zillow.com.example.com",
        "no-reply@mail.zillow.com",
        "do_not_reply@zillow.com",
        "@zillow.com",
        "",
        None,
    ):
        assert relay_address(address) is None, address


# ---------------------------------------------------------------------------
# The outbox and the guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_mode_captures_unless_the_thread_is_a_test_recipient(lead, monkeypatch):
    await booked_by_email(lead)
    monkeypatch.setenv("SHOWING_TEST_EMAILS", "pat@example.com")
    await go_live()

    await lead.world.send_pending(EVENING)

    (notice,) = await email_notices()
    assert (notice.status, notice.mode, notice.guard_reason) == (
        "captured",
        "test",
        "not_test_recipient",
    )
    assert lead.world.emails.sent == []


@pytest.mark.asyncio
async def test_live_outside_production_is_captured_whoever_it_is_to(lead, monkeypatch):
    await booked_by_email(lead)
    live_to_the_thread(monkeypatch)
    await store_settings(lead_agent_uses_open_times=True, send_mode="live", live_all_units=True)

    await lead.world.send_pending(EVENING)

    (notice,) = await email_notices()
    assert (notice.status, notice.mode, notice.guard_reason) == (
        "captured",
        "live",
        "not_production",
    )
    assert lead.world.emails.sent == []


@pytest.mark.asyncio
async def test_live_with_the_thread_as_a_test_recipient_sends_once_in_the_thread(lead, monkeypatch):
    await booked_by_email(lead)
    live_to_the_thread(monkeypatch)
    await go_live()

    for moment in (EVENING, EVENING + timedelta(minutes=1), tuesday(20), tuesday(20, 59)):
        await lead.world.send_pending(moment)

    (sent,) = lead.world.emails.sent
    assert sent.route == ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID)
    assert sent.body.startswith("Hello,\n\nA reminder of your in-person showing on Wed, Apr 14")
    (notice,) = await email_notices()
    assert (notice.status, notice.guard_reason, notice.attempts) == ("sent", "test_recipient", 1)
    # The lead's own number was never texted.
    _, (jordan,), _ = await rows()
    assert all(text.to != jordan.mobile for text in lead.world.texts.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing", "status"),
    [("rejected", "failed"), ("lost", "unknown"), ("thread_unreadable", "pending")],
)
async def test_what_gmail_answers_is_saved(lead, monkeypatch, failing, status):
    await booked_by_email(lead)
    live_to_the_thread(monkeypatch)
    await go_live()
    lead.world.emails.failing = failing

    await lead.world.send_pending(EVENING)

    (notice,) = await email_notices()
    assert notice.status == status
    if status == "pending":  # nothing went out: tried again later
        assert notice.not_before > EVENING and notice.attempts == 1


@pytest.mark.asyncio
async def test_without_a_stand_in_the_live_gmail_sender_refuses_under_pytest(lead, monkeypatch):
    await booked_by_email(lead)
    live_to_the_thread(monkeypatch)
    await go_live()
    await lead.world.send_pending(tuesday(17))  # the other notices go first

    from api.src.showing_bookings import reminders

    await reminders.queue_due(now=EVENING)
    (notice,) = await email_notices()
    with pytest.raises(gmail_thread.LiveGmailInTests):
        await outbox.deliver(notice.id, now=EVENING)
    with pytest.raises(gmail_thread.LiveGmailInTests):
        await gmail_thread.GmailThreadSender().send_in_thread(
            ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID), "Hi"
        )


@pytest.mark.asyncio
async def test_another_email_the_guard_would_send_still_cannot_go(lead, monkeypatch):
    """Only the lead's reminder has a sender; any other email kind fails unsent."""
    await booked_by_email(lead)
    _, (jordan,), _ = await rows()
    monkeypatch.setenv("SHOWING_TEST_EMAILS", RELAY)
    await go_live()
    from api.src.showing_bookings.schema import EmailPayload

    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind="some_other_email",
            channel="email",
            showing_id=jordan.showing_id,
            recipient=RELAY,
            recipient_key="other",
            payload=EmailPayload(subject="Hi", body="Hi", reply_to_message_id=MESSAGE_ID),
            now=EVENING,
        )
        await session.commit()

    assert await outbox.deliver(notice.id, now=EVENING, emails=lead.world.emails) == "failed"
    async with AsyncSessionFactory() as session:
        saved = await session.scalar(select(ShowingNotice).where(ShowingNotice.id == notice.id))
    assert saved.provider_status == "no_email_sender"
    assert lead.world.emails.sent == []


# ---------------------------------------------------------------------------
# The live Gmail sender, with Gmail mocked
# ---------------------------------------------------------------------------


def outside_pytest(monkeypatch) -> None:
    """Lets the live sender run its own logic, as in production, inside one test
    whose every Gmail call is mocked. Pytest sets the variable again for the next test."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")


@pytest.mark.asyncio
async def test_the_live_sender_uses_the_lead_agents_send_on_the_thread(monkeypatch):
    outside_pytest(monkeypatch)
    threading = {"thread_id": "T", "in_reply_to": "<m@zillow.example>", "references": "<m>"}
    headers = AsyncMock(return_value=threading)
    send = AsyncMock(return_value={"id": "gmail-msg-1"})
    monkeypatch.setattr(handle, "_get_threading_headers", headers)
    monkeypatch.setattr(handle, "send_thread_email", send)
    route = ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID)

    sent = await gmail_thread.GmailThreadSender().send_in_thread(route, "Body")

    assert sent.provider_id == "gmail-msg-1"
    headers.assert_awaited_once_with(MESSAGE_ID, handle.SHARED_EXTERNAL_EMAIL)
    send.assert_awaited_once_with(
        {"to": RELAY, "subject": SUBJECT, "reply_to_message_id": MESSAGE_ID},
        "Body",
        threading=threading,
    )


@pytest.mark.asyncio
async def test_the_live_sender_never_sends_outside_the_thread(monkeypatch):
    outside_pytest(monkeypatch)
    monkeypatch.setattr(handle, "_get_threading_headers", AsyncMock(return_value={}))
    send = AsyncMock()
    monkeypatch.setattr(handle, "send_thread_email", send)

    with pytest.raises(NotSent) as refused:
        await gmail_thread.GmailThreadSender().send_in_thread(
            ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID), "Body"
        )

    assert refused.value.reason == "thread_unreadable" and refused.value.retryable
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_live_sender_sends_nothing_outside_production(monkeypatch):
    """The guard sends here outside production only to the test list; should the two
    ever disagree, the sender refuses on its own, reading and sending nothing."""
    outside_pytest(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "portfolio-pr-123")
    headers, send = AsyncMock(), AsyncMock()
    monkeypatch.setattr(handle, "_get_threading_headers", headers)
    monkeypatch.setattr(handle, "send_thread_email", send)

    with pytest.raises(NotSent) as refused:
        await gmail_thread.GmailThreadSender().send_in_thread(
            ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID), "Body"
        )

    assert refused.value.reason == "not_production" and not refused.value.retryable
    headers.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_live_sender_sorts_gmails_errors_as_the_lead_agent_does(monkeypatch):
    from api.src.tests.test_zillow_lead_bookings import gmail_refused

    outside_pytest(monkeypatch)
    threading = {"thread_id": "T", "in_reply_to": "<m@zillow.example>"}
    monkeypatch.setattr(handle, "_get_threading_headers", AsyncMock(return_value=threading))
    route = ThreadRoute(to=RELAY, subject=SUBJECT, reply_to_message_id=MESSAGE_ID)

    monkeypatch.setattr(handle, "send_thread_email", AsyncMock(side_effect=gmail_refused()))
    with pytest.raises(NotSent):
        await gmail_thread.GmailThreadSender().send_in_thread(route, "Body")

    monkeypatch.setattr(handle, "send_thread_email", AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        await gmail_thread.GmailThreadSender().send_in_thread(route, "Body")


@pytest.mark.asyncio
async def test_a_reply_still_goes_through_the_same_gmail_call(monkeypatch):
    """``send_reply`` is unchanged for replies in production: the stored route, the
    thread's headers."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    send_email = AsyncMock(return_value={"id": "abc"})
    monkeypatch.setattr(handle, "_send_email", send_email)
    monkeypatch.setattr(handle, "get_delegated_credentials", lambda **kwargs: "credentials")
    route = {"to": RELAY, "subject": SUBJECT, "reply_to_message_id": MESSAGE_ID}

    result = await handle.send_reply(route, "Body", threading={"thread_id": "T"})

    assert result == handle.Delivery(f"Email sent to {RELAY} (message ID: abc).")
    kwargs = send_email.await_args.kwargs
    assert (kwargs["to"], kwargs["subject"], kwargs["message_text"], kwargs["thread_id"]) == (
        RELAY,
        SUBJECT,
        "Body",
        "T",
    )
    assert kwargs["sender"] == handle.GOOGLE_DELEGATION_EMAIL


# ---------------------------------------------------------------------------
# The team's Messages list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_messages_list_names_it_and_its_thread(lead):
    conversation_id = await booked_by_email(lead)
    await lead.world.send_pending(EVENING)

    async with lead.world.client(lead.world.team_app()) as client:
        listed = (await client.get("/api/showing-bookings/notices")).json()

    (item,) = [n for n in listed if n["kind"] == KIND]
    assert item["kind_label"] == "Lead's reminder by email, in their Zillow thread"
    assert (item["channel"], item["recipient"]) == ("email", RELAY)
    assert item["payload"]["conversation_id"] == conversation_id
    assert item["wording"].startswith(f"Subject: {SUBJECT}\n\nHello,")
