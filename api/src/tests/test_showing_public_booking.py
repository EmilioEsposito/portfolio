"""A booking from the public routes to the rows it leaves, with the stand-ins and a moved clock.

Each scenario books through ``/api/showing-bookings/public/`` against the test
database, then runs the job's two steps (``holds.send_pending``) as the clock
moves, and checks ``showings``, ``showing_attendees`` and ``showing_notices``.
Quo is a ``RecordingTextSender``, the calendars an ``InMemoryCalendar``, and
Turnstile a mock transport. Nothing is sent anywhere.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import holds, open_times, wording
from api.src.showing_bookings.models import Showing
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ADDRESS,
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    JANE,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    SHOWINGS_CALENDAR,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
    statuses,
)

BOOK = f"/api/showing-bookings/public/units/{UNIT}/bookings"


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SHOWING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def live() -> None:
    await store_settings(send_mode="live", test_sends_enabled=True)


async def book(world: PublicWorld, body: dict | None = None, **headers):
    async with world.client() as client:
        return await client.post(BOOK, json=body or booking(), headers=headers)


# A new showing


@pytest.mark.asyncio
async def test_a_new_showing_in_capture_mode(world):
    answer = await book(world)

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert result["kind"] == "new"
    assert result["confirmation"] == "captured"
    assert result["booking"]["address"] == ADDRESS
    assert result["booking"]["mobile"] == "•••-•••-••17"
    showings, attendees, notices = await rows()
    assert [(s.unit, s.starts_at, s.agent_id, s.status) for s in showings] == [
        (UNIT, at(16), world.agent_id, "scheduled")
    ]
    (alex,) = attendees
    assert (alex.name, alex.mobile, alex.email, alex.source) == (
        "Alex Parker",
        ALEX_MOBILE,
        ALEX_EMAIL,
        "page",
    )
    assert alex.texts_accepted and alex.texts_consent_at == NOW
    assert alex.texts_consent_version == wording.TEXTS_CONSENT_VERSION
    assert alex.texts_consent_page == f"https://serniacapital.com/showings/{UNIT}"
    assert [(c["key"], c["wording"]) for c in alex.requirement_confirmations] == [
        ("lease_start", "My lease can start between Jun 1 and Jul 1, 2027."),
        ("pets", "No pet dogs will live in the apartment."),
    ]
    assert alex.cancel_token == result["cancel_token"]
    # The confirmation is captured; the event and the invite wait for it.
    assert statuses(notices) == [
        ("prospect_confirmation", "captured"),
        ("host_event", "held"),
        ("prospect_invite", "held"),
    ]
    confirmation = notices[0]
    assert confirmation.recipient == ALEX_MOBILE
    body = confirmation.payload["body"]
    assert "Sernia Capital" in body and "Wed, Apr 14 at 4:00 PM" in body and ADDRESS in body
    assert f"https://serniacapital.com/showings/cancel/{alex.cancel_token}" in body
    assert world.texts.sent == []

    # Capture mode has no delivery report: the next run releases them at once.
    report = await world.send_pending()
    assert (report.released, report.captured) == (2, 2)
    _, _, notices = await rows()
    assert statuses(notices) == [
        ("prospect_confirmation", "captured"),
        ("host_event", "captured"),
        ("prospect_invite", "captured"),
    ]
    host, invite = notices[1], notices[2]
    assert host.payload["attendees"] == [JANE] and host.payload["operation"] == "create"
    assert host.payload["calendar_id"] == SHOWINGS_CALENDAR
    assert invite.payload["attendees"] == [ALEX_EMAIL]
    assert world.calendar.events == {}


@pytest.mark.asyncio
async def test_a_new_showing_in_live_mode_with_a_test_recipient(world):
    await live()

    answer = await book(world)

    assert answer.status_code == 201, answer.text
    assert answer.json()["confirmation"] == "sent"
    assert len(world.texts.to(ALEX_MOBILE)) == 1
    _, _, notices = await rows()
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "held"),
        ("prospect_invite", "held"),
    ]
    # Nothing but the text until it is reported delivered.
    assert world.calendar.events == {}

    await world.send_pending(NOW + timedelta(minutes=1))

    _, attendees, notices = await rows()
    assert notices[0].provider_status == "delivered"
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "sent"),
        ("prospect_invite", "sent"),
    ]
    events = world.calendar.events_in(SHOWINGS_CALENDAR)
    assert sorted(event.draft.attendees for event in events) == [[ALEX_EMAIL], [JANE]]
    async with AsyncSessionFactory() as session:
        showing = await session.get(Showing, notices[0].showing_id)
    assert showing.host_event_id == notices[1].provider_id
    assert attendees[0].calendar_event_id == notices[2].provider_id


@pytest.mark.asyncio
async def test_undelivered_stops_the_event_and_invite_and_marks_the_booking(world):
    await live()
    world.texts.report(ALEX_MOBILE, "undelivered")
    await book(world)

    report = await world.send_pending(NOW + timedelta(minutes=1))

    assert report.stopped == 2
    _, attendees, notices = await rows()
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "cancelled"),
        ("prospect_invite", "cancelled"),
    ]
    assert attendees[0].status == "booked"
    assert attendees[0].attention_reason == "confirmation_undelivered"
    assert world.calendar.events == {}
    # Later runs leave them stopped.
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.calendar.events == {}


@pytest.mark.asyncio
async def test_five_minutes_with_no_report_lets_them_go(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")  # neither delivered nor undelivered
    await book(world)

    await world.send_pending(NOW + timedelta(minutes=4, seconds=59))
    _, _, notices = await rows()
    assert [n.status for n in notices[1:]] == ["held", "held"]
    assert world.calendar.events == {}

    report = await world.send_pending(NOW + timedelta(minutes=5))
    assert report.released == 2
    _, attendees, notices = await rows()
    assert [n.status for n in notices[1:]] == ["sent", "sent"]
    assert attendees[0].attention_reason is None
    assert len(world.calendar.events) == 2


@pytest.mark.asyncio
async def test_quo_refusing_the_number_withdraws_the_booking(world):
    await live()
    world.texts.refuse(ALEX_MOBILE, "opted_out")

    answer = await book(world)

    assert answer.status_code == 422
    detail = answer.json()["detail"]
    assert detail["code"] == "number_refused"
    assert list(detail["fields"]) == ["mobile"]
    showings, attendees, notices = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "no_attendees")
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "number_refused")
    assert statuses(notices) == [
        ("prospect_confirmation", "failed"),
        ("host_event", "cancelled"),
        ("prospect_invite", "cancelled"),
    ]
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.calendar.events == {} and world.texts.sent == []
    # The time is open again.
    world.texts = type(world.texts)()
    again = await book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL))
    assert again.status_code == 201 and again.json()["kind"] == "new"


@pytest.mark.asyncio
async def test_a_withdrawn_join_leaves_the_showing_and_its_event(world):
    await live()
    await book(world)
    world.texts.refuse(ROBIN_MOBILE, "invalid_number")

    answer = await book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee"))

    assert answer.status_code == 422
    showings, attendees, notices = await rows()
    assert [s.status for s in showings] == ["scheduled"]
    assert [a.status for a in attendees] == ["booked", "cancelled"]
    assert by_kind(notices)["host_event_update"][0].status == "cancelled"


# Joining


@pytest.mark.asyncio
async def test_a_join_adds_no_second_event_and_updates_the_first(world):
    await live()
    first = await book(world)
    second = await book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee"))

    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["kind"] == "join"
    showings, attendees, notices = await rows()
    assert len(showings) == 1
    assert [a.showing_id for a in attendees] == [showings[0].id] * 2
    kinds = by_kind(notices)
    assert len(kinds["host_event"]) == 1
    (update_notice,) = kinds["host_event_update"]
    assert update_notice.payload["operation"] == "update"
    assert "Alex Parker" in update_notice.payload["description"]
    assert "Robin Lee" in update_notice.payload["description"]
    assert [n.recipient for n in kinds["prospect_invite"]] == [ALEX_EMAIL, ROBIN_EMAIL]

    await world.send_pending(NOW + timedelta(minutes=1))

    host_events = [e for e in world.calendar.events.values() if e.draft.attendees == [JANE]]
    assert len(host_events) == 1
    assert ("update", SHOWINGS_CALENDAR, host_events[0].event_id) in world.calendar.log
    assert "Robin Lee" in host_events[0].draft.description
    # Each prospect has an event of their own, with no other guest.
    invites = sorted(e.draft.attendees for e in world.calendar.events.values())
    assert invites == [[ALEX_EMAIL], [JANE], [ROBIN_EMAIL]]


@pytest.mark.asyncio
async def test_two_bookings_at_the_same_moment_make_one_showing(world):
    answers = await asyncio.gather(
        book(world),
        book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee")),
    )

    assert sorted(a.status_code for a in answers) == [201, 201]
    assert sorted(a.json()["kind"] for a in answers) == ["join", "new"]
    showings, attendees, notices = await rows()
    assert len(showings) == 1 and len(attendees) == 2
    kinds = by_kind(notices)
    assert len(kinds["host_event"]) == 1 and len(kinds["host_event_update"]) == 1


@pytest.mark.asyncio
async def test_a_full_showing_refuses(world):
    await book(world)
    async with AsyncSessionFactory() as session:
        await session.execute(update(Showing).values(capacity=1))
        await session.commit()

    answer = await book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL))

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "full"
    assert list(answer.json()["detail"]["fields"]) == ["start"]
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_the_same_mobile_cannot_be_booked_twice_into_one_showing(world):
    await book(world)

    answer = await book(world, booking(email=ROBIN_EMAIL))

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "already_booked"
    assert list(answer.json()["detail"]["fields"]) == ["mobile"]
    _, attendees, _ = await rows()
    assert len(attendees) == 1


@pytest.mark.asyncio
async def test_a_time_gone_at_the_recheck_is_refused(world):
    # The page offered 4:00 PM; Jane's calendar fills before the booking arrives.
    async with world.client() as client:
        page = (await client.get(f"/api/showing-bookings/public/units/{UNIT}")).json()
    assert at(16) in [datetime.fromisoformat(offer["start"]) for offer in page["offers"]]
    world.calendar.add_busy(JANE, at(15, 30), at(17))

    answer = await book(world)

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "time_gone"
    assert list(answer.json()["detail"]["fields"]) == ["start"]
    assert await rows() == ([], [], [])


# The job's two steps


@pytest.mark.asyncio
async def test_the_job_does_nothing_quickly_with_nothing_pending(world, monkeypatch):
    async def must_not_run(**kwargs):
        raise AssertionError("send_pending ran with nothing to do")

    monkeypatch.setattr(holds, "send_pending", must_not_run)
    assert await holds.anything_to_do(NOW) is False
    await holds.scheduled_run()


@pytest.mark.asyncio
async def test_the_job_runs_the_release_and_the_drain(world, monkeypatch):
    await book(world)
    seen = []

    async def recorded(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(holds, "send_pending", recorded)
    assert await holds.anything_to_do(NOW) is True
    await holds.scheduled_run()
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_send_pending_now_runs_the_same_two_steps(world, monkeypatch):
    await book(world)
    calls = []

    async def recorded(*, now):
        calls.append(now)
        return holds.SendPendingReport(released=2, captured=2)

    monkeypatch.setattr(holds, "send_pending", recorded)
    async with world.client(world.team_app()) as client:
        answer = await client.post("/api/showing-bookings/notices/send-pending")
    assert answer.status_code == 200
    assert answer.json()["released"] == 2 and len(calls) == 1
