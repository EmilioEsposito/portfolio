"""The notice outbox against the test database, with the stand-ins for Quo and the calendars.

Nothing here reaches a provider: every send goes to ``RecordingTextSender`` or
``InMemoryCalendar``, and a run that forgot its stand-in fails.
"""

import asyncio
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from api.src.database.app_settings import AppSetting
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import outbox
from api.src.showing_bookings.fakes import InMemoryCalendar, RecordingTextSender
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.quo import QuoTextSender
from api.src.showing_bookings.schema import CalendarPayload, EmailPayload, TextPayload
from api.src.showing_bookings.settings import SETTINGS_KEY
from api.src.tests.showing_helpers import (
    PROSPECT_PHONE,
    TEAM_EMAIL,
    TEAM_PHONE,
    UNIT,
    add_agent,
    add_attendee,
    add_showing,
    at,
    clear_showing_data,
    store_settings,
)

NOW = at(9)  # the morning of the showing day


@pytest_asyncio.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0150")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", TEAM_EMAIL)
    await clear_showing_data()
    yield
    await clear_showing_data()


@pytest_asyncio.fixture
async def showing():
    return await add_showing(await add_agent())


def production(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")


async def live(**fields):
    await store_settings(send_mode="live", **fields)


async def queue_text(
    showing_id: int,
    *,
    to: str = PROSPECT_PHONE,
    key: str = "attendee:1",
    kind: str = "prospect_confirmation",
    body: str = "You're booked for Wed, Apr 14 at 4:00 PM.",
    not_before=None,
) -> int:
    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind=kind,
            channel="sms",
            showing_id=showing_id,
            recipient=to,
            recipient_key=key,
            payload=TextPayload(body=body),
            now=NOW,
            not_before=not_before,
        )
        await session.commit()
        return notice.id


async def row(notice_id: int) -> ShowingNotice:
    async with AsyncSessionFactory() as session:
        return await session.get(ShowingNotice, notice_id)


# Queuing


@pytest.mark.asyncio
async def test_queuing_the_same_notice_twice_returns_the_first(showing):
    async with AsyncSessionFactory() as session:
        first = await outbox.enqueue(
            session,
            kind="resident_new_showing",
            channel="sms",
            showing_id=showing,
            recipient=PROSPECT_PHONE,
            recipient_key="resident:CT_example",
            payload=TextPayload(body="First wording"),
            now=NOW,
        )
        again = await outbox.enqueue(
            session,
            kind="resident_new_showing",
            channel="sms",
            showing_id=showing,
            recipient="(412) 555-0199",
            recipient_key="resident:CT_example",
            payload=TextPayload(body="Second wording"),
            now=NOW,
        )
        assert again.id == first.id
        assert again.payload == {"body": "First wording"}
        await session.commit()

    async with AsyncSessionFactory() as session:
        rows = (await session.scalars(select(ShowingNotice))).all()
    assert [(r.recipient, r.status, r.not_before) for r in rows] == [
        (PROSPECT_PHONE, "pending", NOW)
    ]


@pytest.mark.asyncio
async def test_a_notice_is_saved_only_with_the_callers_transaction(showing):
    async with AsyncSessionFactory() as session:
        await outbox.enqueue(
            session,
            kind="prospect_confirmation",
            channel="sms",
            showing_id=showing,
            recipient=PROSPECT_PHONE,
            recipient_key="attendee:1",
            payload=TextPayload(body="Booked."),
            now=NOW,
        )
        async with AsyncSessionFactory() as other:
            assert (await other.scalars(select(ShowingNotice))).all() == []
        await session.rollback()

    async with AsyncSessionFactory() as session:
        assert (await session.scalars(select(ShowingNotice))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "recipient", "payload", "kind"),
    [
        ("sms", "alex@example.com", TextPayload(body="Hi"), "prospect_confirmation"),
        ("sms", "555", TextPayload(body="Hi"), "prospect_confirmation"),
        ("calendar_invite", PROSPECT_PHONE, None, "prospect_invite"),
        ("calendar_invite", "alex@example.com", TextPayload(body="Hi"), "prospect_invite"),
        ("sms", PROSPECT_PHONE, TextPayload(body="Hi"), "Prospect Confirmation"),
        ("email", PROSPECT_PHONE, EmailPayload(subject="Hi", body="Hi"), "prospect_email"),
        ("email", "alex@example.com", TextPayload(body="Hi"), "prospect_email"),
    ],
)
async def test_a_malformed_notice_is_refused(showing, channel, recipient, payload, kind):
    payload = payload or CalendarPayload(
        operation="create",
        calendar_id="showings@example.com",
        summary="Showing",
        starts_at=at(16),
        ends_at=at(16, 30),
    )
    async with AsyncSessionFactory() as session:
        with pytest.raises(ValueError):
            await outbox.enqueue(
                session,
                kind=kind,
                channel=channel,
                showing_id=showing,
                recipient=recipient,
                recipient_key="attendee:1",
                payload=payload,
                now=NOW,
            )


# What the guard decides, as the outbox applies it


@pytest.mark.asyncio
async def test_capture_keeps_the_rendered_text_and_calls_no_provider(showing):
    notice = await queue_text(showing)
    texts = RecordingTextSender()

    report = await outbox.drain(now=NOW, texts=texts)

    assert texts.sent == []
    assert (report.captured, report.total) == (1, 1)
    saved = await row(notice)
    assert (saved.status, saved.mode, saved.guard_reason) == (
        "captured",
        "capture",
        "send_mode_capture",
    )
    assert saved.payload == {"body": "You're booked for Wed, Apr 14 at 4:00 PM."}
    assert (saved.delivered_at, saved.attempts, saved.provider_id) == (NOW, 0, None)


@pytest.mark.asyncio
async def test_an_old_row_that_was_off_captures(showing):
    await store_settings(send_mode="off", live_units=[UNIT], test_sends_enabled=True)
    notice = await queue_text(showing)
    texts = RecordingTextSender()

    assert (await outbox.drain(now=NOW, texts=texts)).captured == 1
    assert texts.sent == []
    saved = await row(notice)
    assert (saved.status, saved.mode) == ("captured", "capture")


@pytest.mark.asyncio
@pytest.mark.parametrize("row_value", [{"send_mode": "loud"}, {"surprise": True}, ["not a row"]])
async def test_a_settings_row_that_does_not_read_captures(showing, monkeypatch, row_value):
    production(monkeypatch)
    async with AsyncSessionFactory() as session:
        await session.merge(AppSetting(key=SETTINGS_KEY, value=row_value))
        await session.commit()
    notice = await queue_text(showing, to=TEAM_PHONE)
    texts = RecordingTextSender()

    assert (await outbox.drain(now=NOW, texts=texts)).captured == 1
    assert texts.sent == [] and (await row(notice)).status == "captured"


@pytest.mark.asyncio
async def test_all_units_sends_for_any_unit_in_production(showing, monkeypatch):
    production(monkeypatch)
    await live(live_all_units=True)
    notice = await queue_text(showing)
    texts = RecordingTextSender()

    assert (await outbox.drain(now=NOW, texts=texts)).sent == 1
    assert (await row(notice)).guard_reason == "all_units_live"


@pytest.mark.asyncio
async def test_test_mode_in_production_texts_only_the_test_list(showing, monkeypatch):
    production(monkeypatch)
    await store_settings(send_mode="test", live_units=[UNIT], live_all_units=True)
    to_team = await queue_text(showing, to=TEAM_PHONE, key="agent:1", kind="agent_heads_up")
    to_prospect = await queue_text(showing)
    texts = RecordingTextSender()

    report = await outbox.drain(now=NOW, texts=texts)

    assert (report.sent, report.captured) == (1, 1)
    assert [text.to for text in texts.sent] == [TEAM_PHONE]
    assert ((await row(to_team)).mode, (await row(to_team)).guard_reason) == (
        "test",
        "test_recipient",
    )
    kept = await row(to_prospect)
    assert (kept.status, kept.mode, kept.guard_reason) == ("captured", "test", "not_test_recipient")


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", [None, "development", "portfolio-pr-401"])
async def test_live_in_the_row_but_not_production_is_still_captured(
    showing, monkeypatch, environment
):
    if environment:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", environment)
    await live(live_units=[UNIT], live_all_units=True)
    notice = await queue_text(showing)
    texts = RecordingTextSender()

    await outbox.drain(now=NOW, texts=texts)

    assert texts.sent == []
    saved = await row(notice)
    assert (saved.status, saved.mode, saved.guard_reason) == ("captured", "live", "not_production")


@pytest.mark.asyncio
async def test_a_live_unit_in_production_is_sent(showing, monkeypatch):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_text(showing)
    texts = RecordingTextSender()

    report = await outbox.drain(now=NOW, texts=texts)

    assert report.sent == 1
    assert texts.to(PROSPECT_PHONE) == ["You're booked for Wed, Apr 14 at 4:00 PM."]
    saved = await row(notice)
    assert (saved.status, saved.guard_reason, saved.provider_status) == (
        "sent",
        "live_unit",
        "queued",
    )
    assert (saved.provider_id, saved.attempts, saved.sending_started_at) == ("AC0001", 1, None)


@pytest.mark.asyncio
async def test_outside_production_only_the_test_recipient_is_texted(showing):
    await store_settings(send_mode="test")
    to_team = await queue_text(showing, to=TEAM_PHONE, key="agent:1", kind="agent_heads_up")
    to_prospect = await queue_text(showing)
    texts = RecordingTextSender()

    report = await outbox.drain(now=NOW, texts=texts)

    assert (report.sent, report.captured) == (1, 1)
    assert [text.to for text in texts.sent] == [TEAM_PHONE]
    assert (await row(to_team)).guard_reason == "test_recipient"
    assert (await row(to_prospect)).status == "captured"


@pytest.mark.asyncio
async def test_the_live_sender_is_never_used_in_tests(showing, monkeypatch):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_text(showing)

    with pytest.raises(RuntimeError, match="stand-in"):
        await outbox.drain(now=NOW)
    assert (await row(notice)).status == "pending"


# Refusals, unknowns and retries


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["opted_out", "invalid_number", "unreachable_number"])
async def test_a_refused_number_fails_with_the_reason(showing, monkeypatch, reason):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_text(showing)
    texts = RecordingTextSender()
    texts.refuse(PROSPECT_PHONE, reason)

    assert (await outbox.drain(now=NOW, texts=texts)).failed == 1
    saved = await row(notice)
    assert (saved.status, saved.provider_status, saved.attempts) == ("failed", reason, 1)
    assert saved.last_error
    # Failed is final: a later run leaves it alone.
    assert (await outbox.drain(now=NOW + timedelta(hours=1), texts=texts)).total == 0
    assert texts.sent == []


@pytest.mark.asyncio
async def test_an_error_after_the_text_may_have_gone_is_unknown_and_never_retried(
    showing, monkeypatch
):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_text(showing)
    texts = RecordingTextSender()
    texts.lose_answer(PROSPECT_PHONE)

    assert (await outbox.drain(now=NOW, texts=texts)).unknown == 1
    saved = await row(notice)
    assert saved.status == "unknown" and saved.last_error.startswith("TimeoutError")

    await outbox.drain(now=NOW + timedelta(days=1), texts=texts)
    assert len(texts.sent) == 1


@pytest.mark.asyncio
async def test_nothing_sent_is_tried_again_later_then_failed(showing, monkeypatch):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_text(showing)
    texts = RecordingTextSender()
    texts.refuse(PROSPECT_PHONE, "unavailable", retryable=True)

    clock = NOW
    for attempt in range(1, outbox.MAX_ATTEMPTS):
        assert (await outbox.drain(now=clock, texts=texts)).retrying == 1
        saved = await row(notice)
        assert (saved.status, saved.attempts) == ("pending", attempt)
        # Not due again until its pause is over.
        assert (
            await outbox.drain(now=saved.not_before - timedelta(seconds=1), texts=texts)
        ).total == 0
        clock = saved.not_before

    assert (await outbox.drain(now=clock, texts=texts)).failed == 1
    saved = await row(notice)
    assert (saved.status, saved.attempts, saved.provider_status) == (
        "failed",
        outbox.MAX_ATTEMPTS,
        "unavailable",
    )


@pytest.mark.asyncio
async def test_quos_answers_land_on_the_row(showing, monkeypatch):
    """The Quo sender and the outbox together: a refusal fails, a lost answer is unknown."""
    production(monkeypatch)
    await live(live_units=[UNIT])
    refused = await queue_text(showing, to="+14125550120", key="attendee:1")
    timed_out = await queue_text(showing, to="+14125550121", key="attendee:2")

    def quo(request: httpx.Request) -> httpx.Response:
        if b"+14125550121" in request.content:
            raise httpx.ReadTimeout("no answer", request=request)
        return httpx.Response(
            400, json={"code": "0201400", "title": "User Opted Out", "status": 400}
        )

    texts = QuoTextSender(
        from_phone_id="PNexample",
        client_factory=lambda: httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(quo)
        ),
    )
    report = await outbox.drain(now=NOW, texts=texts)

    assert (report.failed, report.unknown) == (1, 1)
    assert (await row(refused)).provider_status == "opted_out"
    assert (await row(timed_out)).status == "unknown"


# Email notices: nothing sends email yet


async def queue_email(showing_id: int, *, to: str = "alex@example.com") -> int:
    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind="lead_showing_agreed",
            channel="email",
            showing_id=showing_id,
            recipient=to,
            recipient_key="thread:thread-a",
            payload=EmailPayload(subject="Your showing", body="See you Wed at 4:00 PM."),
            now=NOW,
        )
        await session.commit()
        return notice.id


@pytest.mark.asyncio
async def test_an_email_notice_is_captured_like_any_other(showing):
    notice = await queue_email(showing)
    texts = RecordingTextSender()

    assert (await outbox.drain(now=NOW, texts=texts)).captured == 1
    saved = await row(notice)
    assert (saved.status, saved.channel) == ("captured", "email")
    assert saved.payload == {"subject": "Your showing", "body": "See you Wed at 4:00 PM."}
    assert texts.sent == []


@pytest.mark.asyncio
async def test_an_email_the_guard_would_send_fails_for_want_of_a_sender(showing, monkeypatch):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_email(showing)

    assert (await outbox.drain(now=NOW, texts=RecordingTextSender())).failed == 1
    saved = await row(notice)
    assert (saved.status, saved.provider_status, saved.attempts) == ("failed", "no_email_sender", 1)


# The clock


@pytest.mark.asyncio
async def test_a_notice_waits_for_its_not_before(showing):
    notice = await queue_text(showing, not_before=NOW + timedelta(hours=1))

    assert (await outbox.drain(now=NOW)).total == 0
    assert (await outbox.drain(now=NOW + timedelta(minutes=59))).total == 0
    assert (await row(notice)).status == "pending"
    assert (await outbox.drain(now=NOW + timedelta(hours=1))).captured == 1
    assert (await row(notice)).delivered_at == NOW + timedelta(hours=1)


# Running twice at once, and interrupted sends


@pytest.mark.asyncio
async def test_two_runs_at_once_send_each_notice_once(showing):
    await store_settings(send_mode="test")
    ids = [
        await queue_text(showing, to=TEAM_PHONE, key=f"agent:{n}", body=f"Text {n}")
        for n in range(8)
    ]
    texts = RecordingTextSender()

    first, second = await asyncio.gather(
        outbox.drain(now=NOW, texts=texts), outbox.drain(now=NOW, texts=texts)
    )

    assert first.sent + second.sent == 8
    assert sorted(text.body for text in texts.sent) == sorted(f"Text {n}" for n in range(8))
    assert {(await row(i)).status for i in ids} == {"sent"}


@pytest.mark.asyncio
async def test_a_send_cut_off_midway_becomes_unknown_and_is_not_sent_again(showing):
    await store_settings(send_mode="test")
    notice = await queue_text(showing, to=TEAM_PHONE, key="agent:1")
    texts = RecordingTextSender()
    # A run claims the row and stops before calling Quo, as a restart would.
    claimed, settled = await outbox._claim_one(notice, now=NOW)
    assert claimed is not None and settled is None

    assert (await outbox.drain(now=NOW + timedelta(minutes=5), texts=texts)).total == 0
    report = await outbox.drain(
        now=NOW + outbox.INTERRUPTED_AFTER + timedelta(seconds=1), texts=texts
    )

    assert report.interrupted == 1 and texts.sent == []
    saved = await row(notice)
    assert (saved.status, saved.last_error) == ("unknown", outbox.INTERRUPTED)

    # If the cut-off run's answer arrives after all, it is the better record.
    await outbox._record(claimed, outbox._Outcome("sent", provider_id="AC9999"), now=NOW)
    assert ((await row(notice)).status, (await row(notice)).provider_id) == ("sent", "AC9999")


class ProcessDied(BaseException):
    """Not an Exception: escapes the outbox the way a killed process or a cancelled task does."""


class DiesOnFirstSend(RecordingTextSender):
    async def send_text(self, to: str, body: str):
        raise ProcessDied()


@pytest.mark.asyncio
async def test_a_run_that_dies_mid_batch_leaves_the_rows_it_had_not_reached(showing):
    await store_settings(send_mode="test")
    first, *rest = [
        await queue_text(showing, to=TEAM_PHONE, key=f"agent:{n}", body=f"Text {n}")
        for n in range(4)
    ]

    with pytest.raises(ProcessDied):
        await outbox.drain(now=NOW, texts=DiesOnFirstSend())

    # Only the row it was sending is claimed; the others are untouched.
    cut_off = await row(first)
    assert (cut_off.status, cut_off.sending_started_at, cut_off.attempts) == ("pending", NOW, 1)
    for notice in rest:
        untouched = await row(notice)
        assert (untouched.status, untouched.sending_started_at, untouched.attempts) == (
            "pending",
            None,
            0,
        )

    texts = RecordingTextSender()
    assert (await outbox.drain(now=NOW + timedelta(minutes=1), texts=texts)).sent == 3
    assert [text.body for text in texts.sent] == ["Text 1", "Text 2", "Text 3"]

    # The one cut off midway is never sent again; it becomes unknown once it has waited.
    later = await outbox.drain(
        now=NOW + outbox.INTERRUPTED_AFTER + timedelta(seconds=1), texts=texts
    )
    assert later.interrupted == 1 and len(texts.sent) == 3
    assert (await row(first)).status == "unknown"


@pytest.mark.asyncio
async def test_settling_and_claiming_are_separate_steps(showing):
    """A capture is settled in the batch; a send is claimed only when its turn comes."""
    await store_settings(send_mode="test")
    to_team = await queue_text(showing, to=TEAM_PHONE, key="agent:1")
    to_prospect = await queue_text(showing)

    to_send, report = await outbox._settle_due(now=NOW, limit=10)

    assert (to_send, report.captured) == ([to_team], 1)
    assert (await row(to_prospect)).status == "captured"
    unclaimed = await row(to_team)
    assert (unclaimed.status, unclaimed.sending_started_at, unclaimed.mode) == (
        "pending",
        None,
        None,
    )


# Delivering one notice at once


@pytest.mark.asyncio
async def test_deliver_sends_one_due_notice_once(showing):
    await store_settings(send_mode="test")
    now_notice = await queue_text(showing, to=TEAM_PHONE, key="agent:1")
    later = await queue_text(
        showing, to=TEAM_PHONE, key="agent:2", not_before=NOW + timedelta(hours=1)
    )
    texts = RecordingTextSender()

    assert await outbox.deliver(now_notice, now=NOW, texts=texts) == "sent"
    assert await outbox.deliver(now_notice, now=NOW, texts=texts) == "sent"
    assert await outbox.deliver(later, now=NOW, texts=texts) == "pending"
    assert await outbox.deliver(123456789, now=NOW, texts=texts) is None
    assert len(texts.sent) == 1


@pytest.mark.asyncio
async def test_a_sent_texts_status_is_read_back_and_saved(showing):
    await store_settings(send_mode="test")
    sent = await queue_text(showing, to=TEAM_PHONE, key="agent:1")
    captured = await queue_text(showing, key="attendee:1")
    texts = RecordingTextSender()
    await outbox.drain(now=NOW, texts=texts)
    texts.report(TEAM_PHONE, "undelivered")

    assert await outbox.refresh_text_status(sent, texts=texts) == "undelivered"
    assert (await row(sent)).provider_status == "undelivered"
    assert await outbox.refresh_text_status(captured, texts=texts) is None


# Calendar notices


def event(operation: str, attendees: list[str], **fields) -> CalendarPayload:
    return CalendarPayload(
        operation=operation,
        calendar_id="showings@example.com",
        summary=f"Showing {UNIT}",
        location="100 Example Ave",
        starts_at=at(16),
        ends_at=at(16, 30),
        attendees=attendees,
        **fields,
    )


async def queue_event(showing_id, kind, payload, *, channel="calendar_event", attendee_id=None):
    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind=kind,
            channel=channel,
            showing_id=showing_id,
            attendee_id=attendee_id,
            recipient=payload.attendees[0],
            recipient_key=f"{channel}:{attendee_id or 'host'}",
            payload=payload,
            now=NOW,
        )
        await session.commit()
        return notice.id


@pytest.mark.asyncio
async def test_the_hosts_event_is_created_then_changed_by_its_saved_id(showing):
    await store_settings(send_mode="test")
    calendar = InMemoryCalendar()
    await queue_event(showing, "host_event_created", event("create", [TEAM_EMAIL]))
    await outbox.drain(now=NOW, calendar=calendar)

    async with AsyncSessionFactory() as session:
        host_event_id = (await session.get(Showing, showing)).host_event_id
    assert host_event_id == "evt0001"

    await queue_event(showing, "host_event_updated", event("update", [TEAM_EMAIL]))
    await queue_event(showing, "host_event_cancelled", event("cancel", [TEAM_EMAIL]))
    report = await outbox.drain(now=NOW + timedelta(minutes=1), calendar=calendar)

    assert report.sent == 2
    assert [entry[0] for entry in calendar.log] == ["create", "update", "cancel"]
    assert calendar.events["evt0001"].cancelled


@pytest.mark.asyncio
async def test_a_prospects_invite_is_saved_on_the_attendee(showing):
    await store_settings(send_mode="test")
    attendee = await add_attendee(showing)
    calendar = InMemoryCalendar()
    await queue_event(
        showing,
        "prospect_invite",
        event("create", [TEAM_EMAIL]),
        channel="calendar_invite",
        attendee_id=attendee,
    )

    assert (await outbox.drain(now=NOW, calendar=calendar)).sent == 1
    async with AsyncSessionFactory() as session:
        assert (await session.get(ShowingAttendee, attendee)).calendar_event_id == "evt0001"


@pytest.mark.asyncio
async def test_a_change_to_an_event_not_yet_created_waits(showing):
    await store_settings(send_mode="test")
    notice = await queue_event(showing, "host_event_updated", event("update", [TEAM_EMAIL]))

    assert (await outbox.drain(now=NOW, calendar=InMemoryCalendar())).retrying == 1
    assert (await row(notice)).status == "pending"


@pytest.mark.asyncio
async def test_an_invite_naming_anyone_outside_the_test_list_is_captured(showing):
    await store_settings(send_mode="test")
    calendar = InMemoryCalendar()
    notice = await queue_event(
        showing, "host_event_created", event("create", [TEAM_EMAIL, "alex@example.com"])
    )

    assert (await outbox.drain(now=NOW, calendar=calendar)).captured == 1
    assert calendar.log == [] and (await row(notice)).guard_reason == "not_test_recipient"


@pytest.mark.asyncio
async def test_a_calendar_notice_cannot_be_sent_before_calendars_are_connected(
    showing, monkeypatch
):
    production(monkeypatch)
    await live(live_units=[UNIT])
    notice = await queue_event(showing, "host_event_created", event("create", [TEAM_EMAIL]))

    assert (await outbox.drain(now=NOW)).failed == 1
    assert (await row(notice)).provider_status == "calendar_not_connected"


@pytest.mark.asyncio
async def test_times_must_carry_a_timezone(showing):
    naive = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone"):
        await outbox.drain(now=naive)
    with pytest.raises(ValueError, match="timezone"):
        await queue_text(showing, not_before=naive)
