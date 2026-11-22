"""The team's actions on the Booking settings tab, from the routes to the rows they leave:
a booking for someone who called, another host for a showing, and who came.

Each scenario goes through the team routes (and the public ones where a page
booking is part of it), runs the job's two steps as the clock moves, and
checks ``showings``, ``showing_attendees`` and ``showing_notices``. Quo is a
``RecordingTextSender``, the calendars an ``InMemoryCalendar``, Turnstile a
mock transport. Nothing is sent anywhere. Names and numbers are made up.
"""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import (
    cancelling,
    holds,
    host_event,
    open_times,
    residents,
    routes,
    wording,
)
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingNotice,
)
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    JANE,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    SHOWINGS_CALENDAR,
    TUESDAY,
    UNIT,
    WEDNESDAY,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
    statuses,
)
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.utils import clerk as clerk_module

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
SAM = "sam.roe@example.com"
RESIDENT = "+14125550142"
NEW = residents.NEW_SHOWING_KIND
OTHER_UNIT = "412-01"  # another building
SAME_BUILDING = "320-05"


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE},{RESIDENT}"
    )
    monkeypatch.setenv(
        "SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE},{SAM}"
    )
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()
    state = PublicWorld()
    state.other_listings = [RentalListing(unit=OTHER_UNIT, address="9 Other Rd")]
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()


async def live() -> None:
    await store_settings(send_mode="test")


async def add_sam(weekly_hours: list | None = None) -> int:
    async with AsyncSessionFactory() as session:
        agent = ShowingAgentRecord(
            name="Sam Roe",
            email=SAM,
            mobile="+14125550102",
            busy_calendar_ids=[SAM],
            weekly_hours=weekly_hours or [],
            priority=2,
        )
        session.add(agent)
        await session.commit()
        return agent.id


def team_app(world: PublicWorld) -> FastAPI:
    application = world.team_app()
    application.dependency_overrides[routes.text_sender] = lambda: world.texts
    application.dependency_overrides[routes.clock] = lambda: world.now
    return application


def caller(
    start=None,
    *,
    name: str = "Casey Morgan",
    mobile: str = "",
    email: str = "",
    texts: bool = False,
    unit: str = UNIT,
) -> dict:
    return {
        "unit": unit,
        "start": (start or at(16)).isoformat(),
        "name": name,
        "mobile": mobile,
        "email": email,
        "texts": texts,
    }


async def team_book(world: PublicWorld, body: dict) -> httpx.Response:
    async with world.client(team_app(world)) as client:
        return await client.post(f"{TEAM}/showings/bookings", json=body)


async def page_book(world: PublicWorld, body: dict | None = None) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=body or booking())
    assert answer.status_code == 201, answer.text
    return answer.json()


async def move(world: PublicWorld, showing_id: int, agent_id: int) -> httpx.Response:
    async with world.client(team_app(world)) as client:
        return await client.post(f"{TEAM}/showings/{showing_id}/host", json={"agent_id": agent_id})


async def mark(world: PublicWorld, showing_id: int, attendee_id: int, value: str):
    async with world.client(team_app(world)) as client:
        return await client.put(
            f"{TEAM}/showings/{showing_id}/attendees/{attendee_id}/attendance",
            json={"attendance": value},
        )


async def showings_view(world: PublicWorld) -> dict:
    open_times.forget_free_busy()
    async with world.client(team_app(world)) as client:
        answer = await client.get(f"{TEAM}/showings")
    assert answer.status_code == 200, answer.text
    return answer.json()


def refusal(answer: httpx.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["detail"]["code"]


async def add_other_showing(agent_id: int, unit: str, start) -> int:
    async with AsyncSessionFactory() as session:
        showing = Showing(
            unit=unit,
            address="9 Other Rd" if unit == OTHER_UNIT else "322 Example St",
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            agent_id=agent_id,
        )
        session.add(showing)
        await session.commit()
        return showing.id


# ---------------------------------------------------------------------------
# A booking for someone who called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_caller_who_takes_texts_is_booked_like_a_page_booking(world):
    await live()

    answer = await team_book(world, caller(mobile="412-555-0119", email=CASEY_EMAIL, texts=True))

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert (result["kind"], result["texts_accepted"], result["confirmation"]) == (
        "new",
        True,
        "sent",
    )
    assert [(n["kind"], n["status"], n["held"]) for n in result["notices"]] == [
        ("prospect_confirmation", "sent", False),
        ("host_event", "held", True),
        ("prospect_invite", "held", True),
    ]
    showings, attendees, notices = await rows()
    (casey,) = attendees
    assert (casey.source, casey.mobile, casey.email, casey.texts_accepted) == (
        "team",
        CASEY_MOBILE,
        CASEY_EMAIL,
        True,
    )
    assert (casey.texts_consent_at, casey.texts_consent_page) == (NOW, "team:user_test")
    assert casey.texts_consent_version == "v1"
    # The team spoke to the caller: no form ticks, no user agent, no address hash.
    assert casey.requirement_confirmations == []
    assert (casey.user_agent, casey.client_ip_hash) == (None, None)
    assert len(world.texts.to(CASEY_MOBILE)) == 1
    assert world.calendar.events == {}
    confirmation = by_kind(notices)["prospect_confirmation"][0]
    assert all(
        n.waits_for_notice_id == confirmation.id
        for n in notices
        if n.kind in ("host_event", "prospect_invite")
    )

    await world.send_pending(NOW + timedelta(minutes=1))

    _, _, notices = await rows()
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "sent"),
        ("prospect_invite", "sent"),
    ]
    guests = sorted(event.draft.attendees[0] for event in world.calendar.events.values())
    assert guests == [CASEY_EMAIL, JANE]


@pytest.mark.asyncio
async def test_a_caller_who_declines_texts_gets_none_and_nothing_is_held(world):
    await live()

    answer = await team_book(world, caller(mobile="412-555-0119", email=CASEY_EMAIL))

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert (result["texts_accepted"], result["confirmation"]) == (False, None)
    assert [(n["kind"], n["status"]) for n in result["notices"]] == [
        ("host_event", "pending"),
        ("prospect_invite", "pending"),
    ]
    _, (casey,), notices = await rows()
    assert (casey.texts_accepted, casey.texts_consent_at, casey.texts_consent_page) == (
        False,
        None,
        None,
    )
    assert casey.mobile == CASEY_MOBILE
    assert all(n.waits_for_notice_id is None for n in notices)

    await world.send_pending(NOW + timedelta(minutes=1))

    _, _, notices = await rows()
    assert statuses(notices) == [("host_event", "sent"), ("prospect_invite", "sent")]
    assert world.texts.sent == []
    # A cancellation later texts them nothing either, and still withdraws the invite.
    async with world.client(team_app(world)) as client:
        answer = await client.post(f"{TEAM}/showings/{result['showing_id']}/cancel")
    assert answer.json()["texts_queued"] == 0
    await world.send_pending(NOW + timedelta(minutes=2))
    assert world.texts.sent == []
    assert sorted(op for op, *_ in world.calendar.log) == ["cancel", "cancel", "create", "create"]


@pytest.mark.asyncio
async def test_texts_without_a_mobile_are_saved_as_no_texts(world):
    answer = await team_book(world, caller(email=CASEY_EMAIL, texts=True))

    assert answer.status_code == 201, answer.text
    assert (answer.json()["texts_accepted"], answer.json()["confirmation"]) == (False, None)
    _, (casey,), notices = await rows()
    assert (casey.mobile, casey.texts_accepted, casey.texts_consent_at) == (None, False, None)
    assert [n.kind for n in notices] == ["host_event", "prospect_invite"]


@pytest.mark.asyncio
async def test_a_caller_with_only_a_name_gives_the_host_an_event_and_nothing_else(world):
    await live()

    answer = await team_book(world, caller(name="  Casey   Morgan "))

    assert answer.status_code == 201, answer.text
    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("host_event", "pending")
    ]
    _, (casey,), _ = await rows()
    assert (casey.name, casey.mobile, casey.email) == ("Casey Morgan", None, None)

    await world.send_pending(NOW + timedelta(minutes=1))

    (event,) = world.calendar.events.values()
    assert event.draft.attendees == [JANE]
    assert "Casey Morgan, no mobile, no email" in event.draft.description
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_the_team_adds_a_caller_to_a_showing_booked_on_the_page(world):
    await live()
    alex = await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    assert len(world.calendar.events) == 2

    answer = await team_book(world, caller(email=CASEY_EMAIL))

    assert answer.status_code == 201, answer.text
    result = answer.json()
    assert result["kind"] == "join"
    assert [(n["kind"], n["status"]) for n in result["notices"]] == [
        ("host_event_update", "pending"),
        ("prospect_invite", "pending"),
    ]
    showings, attendees, _ = await rows()
    assert len(showings) == 1
    assert [a.source for a in attendees] == ["page", "team"]
    assert alex["kind"] == "new"

    await world.send_pending(NOW + timedelta(minutes=2))

    host_event_id = showings[0].host_event_id
    assert host_event_id is not None
    description = world.calendar.events[host_event_id].draft.description
    assert "Alex Parker" in description and "Casey Morgan" in description
    # One event for the host, updated; Alex was texted once, Casey never.
    assert [op for op, *_ in world.calendar.log].count("create") == 3
    assert len(world.texts.to(ALEX_MOBILE)) == 1 and world.texts.to(CASEY_MOBILE) == []


@pytest.mark.asyncio
async def test_a_number_quo_refuses_withdraws_the_team_booking(world):
    await live()
    world.texts.refuse(CASEY_MOBILE, "invalid_number")

    answer = await team_book(world, caller(mobile=CASEY_MOBILE, email=CASEY_EMAIL, texts=True))

    assert refusal(answer) == (422, "number_refused")
    detail = answer.json()["detail"]
    assert "withdrawn" in detail["message"] and detail["fields"] == {
        "mobile": "Quo refused this number."
    }
    showings, (casey,), notices = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "no_attendees")
    assert (casey.status, casey.cancel_reason) == ("cancelled", "number_refused")
    assert statuses(notices) == [
        ("prospect_confirmation", "failed"),
        ("host_event", "cancelled"),
        ("prospect_invite", "cancelled"),
    ]
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.calendar.events == {}


@pytest.mark.asyncio
async def test_a_time_gone_at_the_recheck_is_refused(world):
    world.calendar.add_busy(JANE, at(16), at(16, 30))

    answer = await team_book(world, caller())

    assert refusal(answer) == (409, "time_gone")
    assert answer.json()["detail"]["fields"] == {"start": "That time is no longer open."}
    showings, attendees, notices = await rows()
    assert (showings, attendees, notices) == ([], [], [])


@pytest.mark.asyncio
async def test_a_full_showing_is_refused(world):
    for number in range(4):
        answer = await team_book(world, caller(name=f"Caller {number}"))
        assert answer.status_code == 201, answer.text

    answer = await team_book(world, caller(name="One Too Many"))

    assert refusal(answer) == (409, "full")
    _, attendees, _ = await rows()
    assert len(attendees) == 4


@pytest.mark.asyncio
async def test_the_page_limits_and_ticks_do_not_apply_to_the_team(world):
    await store_settings(
        public_limits={
            "upcoming_per_mobile": 1,
            "upcoming_per_email": 1,
            "bookings_per_ip_per_day": 1,
            "bookings_per_day": 1,
        }
    )
    await page_book(world)
    async with world.client() as client:
        refused = await client.post(
            f"{PUBLIC}/units/{UNIT}/bookings", json=booking(at(17), mobile=ROBIN_MOBILE)
        )
    assert refused.status_code == 429

    # The same mobile and email as the page booking, past every limit, no ticks, no Turnstile.
    answer = await team_book(
        world, caller(at(17), name="Alex Parker", mobile=ALEX_MOBILE, email=ALEX_EMAIL, texts=True)
    )

    assert answer.status_code == 201, answer.text
    assert answer.json()["confirmation"] == "captured"
    _, attendees, _ = await rows()
    assert [(a.source, a.mobile) for a in attendees] == [
        ("page", ALEX_MOBILE),
        ("team", ALEX_MOBILE),
    ]


@pytest.mark.asyncio
async def test_the_team_booking_checks_its_fields(world):
    answer = await team_book(
        world, {"unit": UNIT, "start": "tomorrow", "name": " ", "mobile": "555", "email": "x@"}
    )

    assert refusal(answer) == (422, "invalid")
    assert set(answer.json()["detail"]["fields"]) == {"start", "name", "mobile", "email"}

    unknown = await team_book(world, caller(unit="999-99"))
    assert refusal(unknown) == (404, "unknown_unit")

    inside_notice = await team_book(world, caller(NOW + timedelta(hours=2)))
    assert refusal(inside_notice) == (409, "time_gone")
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_number_already_booked_in_the_showing_is_refused(world):
    await page_book(world)

    answer = await team_book(world, caller(mobile=ALEX_MOBILE))

    assert refusal(answer) == (409, "already_booked")


# ---------------------------------------------------------------------------
# Another host
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_showing_moves_to_a_free_agent_after_the_event_went_out(world):
    await live()
    sam = await add_sam()
    alex = await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    showings, _, _ = await rows()
    jane_event = showings[0].host_event_id
    assert world.calendar.events[jane_event].draft.attendees == [JANE]

    answer = await move(world, showings[0].id, sam)

    assert answer.status_code == 200, answer.text
    result = answer.json()
    assert (result["host_name"], result["previous_host_name"]) == ("Sam Roe", "Jane Doe")
    assert [(n["kind"], n["status"]) for n in result["notices"]] == [
        ("host_event_moved", "pending"),
        ("host_event", "pending"),
    ]
    showings, _, notices = await rows()
    assert (showings[0].agent_id, showings[0].host_event_id) == (sam, None)
    (moved,) = by_kind(notices)["host_event_moved"]
    assert (moved.recipient, moved.payload["event_id"]) == (JANE, jane_event)

    await world.send_pending(NOW + timedelta(minutes=2))

    assert world.calendar.events[jane_event].cancelled
    showings, _, notices = await rows()
    sam_event = showings[0].host_event_id
    assert sam_event not in (None, jane_event)
    assert world.calendar.events[sam_event].draft.attendees == [SAM]
    assert world.calendar.events[sam_event].calendar_id == SHOWINGS_CALENDAR
    # Prospects are told nothing: one text, one invite, both from the booking.
    assert len(world.texts.to(ALEX_MOBILE)) == 1
    assert [e.draft.attendees for e in world.calendar.events.values()].count([ALEX_EMAIL]) == 1

    # Cancelling later cancels Sam's event, not Jane's old one again.
    async with world.client() as client:
        await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": alex["cancel_token"]})
    await world.send_pending(NOW + timedelta(minutes=3))
    assert world.calendar.events[sam_event].cancelled
    invite_event = world.calendar.log[1][2]
    cancelled = [event_id for op, _, event_id in world.calendar.log if op == "cancel"]
    assert sorted(cancelled) == sorted([jane_event, sam_event, invite_event])


@pytest.mark.asyncio
async def test_a_showing_moves_while_its_event_is_held(world):
    await live()
    sam = await add_sam()
    await page_book(world)
    showings, _, notices = await rows()
    confirmation = by_kind(notices)["prospect_confirmation"][0]

    answer = await move(world, showings[0].id, sam)

    assert answer.status_code == 200, answer.text
    # Nothing went out, so nothing is cancelled at Google: the held event is Sam's now.
    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [("host_event", "held")]
    _, _, notices = await rows()
    old, new = by_kind(notices)["host_event"]
    assert (old.status, old.last_error, old.waits_for_notice_id) == (
        "cancelled",
        host_event.HOST_CHANGED,
        None,
    )
    assert (new.status, new.recipient, new.waits_for_notice_id) == (
        "held",
        SAM,
        confirmation.id,
    )
    assert "host_event_moved" not in by_kind(notices)

    await world.send_pending(NOW + timedelta(minutes=1))

    (event,) = [e for e in world.calendar.events.values() if e.draft.attendees != [ALEX_EMAIL]]
    assert event.draft.attendees == [SAM]
    assert not event.cancelled
    assert [op for op, *_ in world.calendar.log] == ["create", "create"]


@pytest.mark.asyncio
async def test_a_move_after_the_event_with_a_join_still_held(world):
    await live()
    sam = await add_sam()
    await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    world.texts.report(ROBIN_MOBILE, "queued")  # no final report yet: Robin's update stays held
    await page_book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee"))
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)

    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("host_event_moved", "pending"),
        ("host_event_update", "held"),
        ("host_event", "pending"),
    ]
    await world.send_pending(NOW + timedelta(minutes=2))
    showings, _, _ = await rows()
    sam_event = world.calendar.events[showings[0].host_event_id]
    assert sam_event.draft.attendees == [SAM]
    # Rendered at delivery from who is booked now.
    assert "Robin Lee" in sam_event.draft.description


@pytest.mark.asyncio
async def test_a_showing_cancelled_after_a_move_before_the_job_leaves_no_event(world):
    await live()
    sam = await add_sam()
    await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    showings, _, _ = await rows()
    jane_event = showings[0].host_event_id

    assert (await move(world, showings[0].id, sam)).status_code == 200
    async with world.client(team_app(world)) as client:
        cancelled = await client.post(f"{TEAM}/showings/{showings[0].id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    await world.send_pending(NOW + timedelta(minutes=2))

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.status for n in kinds["host_event_moved"]] == ["sent"]
    # Sam's event was never created, so there is nothing of his to cancel.
    assert [n.status for n in kinds["host_event"] if n.recipient == SAM] == ["cancelled"]
    host_events = [e for e in world.calendar.events.values() if e.draft.attendees != [ALEX_EMAIL]]
    assert [(e.event_id, e.cancelled) for e in host_events] == [(jane_event, True)]
    assert all(e.cancelled for e in world.calendar.events.values())


SAM_CALENDAR = "sam-showings@example.com"


async def add_sam_on_own_connection() -> int:
    async with AsyncSessionFactory() as session:
        connection = CalendarConnection(
            google_email=SAM,
            google_subject="100000000000000000002",
            connected_by="pat@serniacapital.com",
            connected_at=NOW - timedelta(days=3),
            showings_calendar_id=SAM_CALENDAR,
        )
        session.add(connection)
        await session.flush()
        agent = ShowingAgentRecord(
            name="Sam Roe",
            email=SAM,
            mobile="+14125550102",
            busy_calendar_ids=[SAM],
            connection_id=connection.id,
            priority=2,
        )
        session.add(agent)
        await session.commit()
        return agent.id


@pytest.mark.asyncio
async def test_a_correction_after_a_move_brings_back_the_new_hosts_event_on_their_calendar(
    world,
):
    await live()
    sam = await add_sam_on_own_connection()
    alex = await page_book(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await world.send_pending(NOW + timedelta(minutes=1))
    _, (attendee,), notices = await rows()
    assert attendee.attention_reason == "confirmation_undelivered"
    (old,) = by_kind(notices)["host_event"]
    assert (old.status, old.recipient) == ("cancelled", JANE)
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)

    assert answer.status_code == 200, answer.text
    _, _, notices = await rows()
    old, new = by_kind(notices)["host_event"]
    assert (old.status, old.waits_for_notice_id) == ("cancelled", None)
    assert (new.status, new.recipient, new.payload["calendar_id"]) == (
        "cancelled",
        SAM,
        SAM_CALENDAR,
    )
    assert new.waits_for_notice_id is not None

    async with world.client() as client:
        corrected = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": alex["cancel_token"], "mobile": ROBIN_MOBILE},
        )
    assert corrected.status_code == 200, corrected.text
    await world.send_pending(NOW + timedelta(minutes=2))

    _, _, notices = await rows()
    old, new = by_kind(notices)["host_event"]
    assert old.status == "cancelled"
    assert (new.status, new.recipient) == ("sent", SAM)
    host_events = [e for e in world.calendar.events.values() if e.draft.attendees != [ALEX_EMAIL]]
    assert [(e.calendar_id, e.draft.attendees) for e in host_events] == [(SAM_CALENDAR, [SAM])]
    assert world.calendar.events_in(SHOWINGS_CALENDAR) == [
        e for e in world.calendar.events.values() if e.draft.attendees == [ALEX_EMAIL]
    ]


@pytest.mark.asyncio
async def test_a_move_after_the_old_hosts_event_failed_gives_the_new_host_one(world):
    await live()
    sam = await add_sam_on_own_connection()
    await page_book(world)
    world.calendar.lose(SHOWINGS_CALENDAR)
    await world.send_pending(NOW + timedelta(minutes=1))
    _, _, notices = await rows()
    assert [n.status for n in by_kind(notices)["host_event"]] == ["failed"]
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)

    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("host_event", "pending")
    ]
    await world.send_pending(NOW + timedelta(minutes=2))
    (event,) = world.calendar.events_in(SAM_CALENDAR)
    assert event.draft.attendees == [SAM]


@pytest.mark.asyncio
async def test_a_move_before_a_due_event_goes_out_keeps_it_due_for_the_new_host(world):
    await live()
    sam = await add_sam()
    booked = await team_book(world, caller())  # no texts: the host's event is due, not held
    showing_id = booked.json()["showing_id"]

    answer = await move(world, showing_id, sam)

    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("host_event", "pending")
    ]
    await world.send_pending(NOW + timedelta(minutes=1))
    (event,) = world.calendar.events.values()
    assert event.draft.attendees == [SAM]
    assert [op for op, *_ in world.calendar.log] == ["create"]


@pytest.mark.asyncio
async def test_a_move_in_capture_mode_keeps_the_cancellation_and_the_new_event(world):
    sam = await add_sam()
    await page_book(world)
    await world.send_pending()
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)
    await world.send_pending(NOW + timedelta(minutes=1))

    assert answer.status_code == 200, answer.text
    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.status for n in kinds["host_event_moved"]] == ["captured"]
    assert kinds["host_event_moved"][0].payload["attendees"] == [JANE]
    new = [n for n in kinds["host_event"] if n.recipient == SAM]
    assert [(n.status, n.payload["attendees"]) for n in new] == [("captured", [SAM])]
    assert world.calendar.events == {}


@pytest.mark.asyncio
async def test_a_move_after_an_unknown_create_flags_the_old_event_and_still_creates_the_new(
    world,
):
    await live()
    sam = await add_sam()
    await page_book(world)
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == "host_event")
            .values(status="unknown", waits_for_notice_id=None)
        )
        await session.commit()
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)
    await world.send_pending(NOW + timedelta(minutes=1))

    assert answer.status_code == 200, answer.text
    _, _, notices = await rows()
    (moved,) = by_kind(notices)["host_event_moved"]
    assert (moved.status, moved.last_error) == ("cancelled", host_event.MAYBE_CREATED)
    sam_event = [n for n in by_kind(notices)["host_event"] if n.recipient == SAM]
    assert [n.status for n in sam_event] == ["sent"]


@pytest.mark.asyncio
async def test_a_move_to_a_busy_agent_is_refused(world):
    sam = await add_sam()
    await page_book(world)
    world.calendar.add_busy(SAM, at(15, 45), at(16, 15))
    showings, _, before = await rows()

    answer = await move(world, showings[0].id, sam)

    assert refusal(answer) == (409, "calendar_busy")
    assert "Sam Roe is busy" in answer.json()["detail"]["message"]
    showings, _, after = await rows()
    assert showings[0].agent_id == world.agent_id
    assert statuses(after) == statuses(before)


@pytest.mark.asyncio
async def test_a_move_to_an_agent_outside_their_hours_is_refused(world):
    sam = await add_sam([{"days": ["mon"], "start": "16:00", "end": "18:00"}])
    await page_book(world)
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)

    assert refusal(answer) == (409, "outside_hours")
    assert answer.json()["detail"]["message"] == "Sam Roe's hours do not cover 4:00 PM that day."


@pytest.mark.asyncio
async def test_a_move_needs_travel_time_between_buildings_and_none_inside_one(world):
    sam = await add_sam()
    await page_book(world)
    showings, _, _ = await rows()
    other = await add_other_showing(sam, OTHER_UNIT, at(16, 30))

    answer = await move(world, showings[0].id, sam)

    assert refusal(answer) == (409, "in_another_showing")
    assert "412-01 at 4:30 PM" in answer.json()["detail"]["message"]

    # The same showing next door in the same building needs no travel.
    async with AsyncSessionFactory() as session:
        await session.execute(update(Showing).where(Showing.id == other).values(unit=SAME_BUILDING))
        await session.commit()
    assert (await move(world, showings[0].id, sam)).status_code == 200


@pytest.mark.asyncio
async def test_a_move_after_the_start_and_other_refusals(world):
    sam = await add_sam()
    await page_book(world)
    showings, _, _ = await rows()
    showing_id = showings[0].id

    assert refusal(await move(world, showing_id, world.agent_id)) == (409, "same_host")
    assert refusal(await move(world, showing_id + 1000, sam)) == (404, "unknown_showing")
    assert refusal(await move(world, showing_id, sam + 1000)) == (404, "unknown_agent")
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAgentRecord).where(ShowingAgentRecord.id == sam).values(active=False)
        )
        await session.commit()
    assert refusal(await move(world, showing_id, sam)) == (409, "inactive")

    world.now = at(16, 5)
    assert refusal(await move(world, showing_id, sam)) == (409, "started")


@pytest.mark.asyncio
async def test_a_move_waits_while_the_hosts_event_is_being_sent(world):
    sam = await add_sam()
    await page_book(world)
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == "host_event")
            .values(status="pending", sending_started_at=NOW)
        )
        await session.commit()
    showings, _, _ = await rows()

    answer = await move(world, showings[0].id, sam)

    assert refusal(answer) == (409, "event_being_sent")
    showings, _, _ = await rows()
    assert showings[0].agent_id == world.agent_id


# ---------------------------------------------------------------------------
# Who came
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attendance_is_marked_after_the_start_and_can_change(world):
    await page_book(world)
    showings, (alex,), before = await rows()
    showing_id = showings[0].id

    early = await mark(world, showing_id, alex.id, "attended")
    assert refusal(early) == (409, "not_started")

    world.now = at(18)  # after the showing ended: still listed so it can be marked
    answer = await mark(world, showing_id, alex.id, "attended")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "showing_id": showing_id,
        "attendee_id": alex.id,
        "attendance": "attended",
        "notices": [],
    }
    changed = await mark(world, showing_id, alex.id, "no_show")
    assert changed.json()["attendance"] == "no_show"

    view = await showings_view(world)
    (listed,) = view["booked"]
    assert (listed["started"], listed["host_id"]) == (True, world.agent_id)
    assert listed["people"][0]["attendance"] == "no_show"
    _, (alex,), after = await rows()
    assert alex.attendance == "no_show"
    assert statuses(after) == statuses(before)  # nothing queued

    invalid = await mark(world, showing_id, alex.id, "maybe")
    assert invalid.status_code == 422
    assert refusal(await mark(world, showing_id, alex.id + 1000, "attended")) == (
        404,
        "unknown_attendee",
    )


@pytest.mark.asyncio
async def test_attendance_is_refused_for_a_cancelled_booking(world):
    alex = await page_book(world)
    await page_book(world, booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee"))
    async with world.client() as client:
        await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": alex["cancel_token"]})
    showings, (alex_row, _), _ = await rows()
    world.now = at(16, 10)

    answer = await mark(world, showings[0].id, alex_row.id, "attended")

    assert refusal(answer) == (409, "not_booked")


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_sign_in_the_team_actions_are_refused(world, monkeypatch):
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=lambda request, options: SimpleNamespace(is_signed_in=False)
        ),
    )
    await page_book(world)
    showings, (alex,), before = await rows()
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        calls = [
            client.post(f"{TEAM}/showings/bookings", json=caller(at(17))),
            client.post(f"{TEAM}/showings/{showings[0].id}/host", json={"agent_id": 1}),
            client.put(
                f"{TEAM}/showings/{showings[0].id}/attendees/{alex.id}/attendance",
                json={"attendance": "attended"},
            ),
        ]
        for call in calls:
            assert (await call).status_code == 401
    after = await rows()
    assert [s.agent_id for s in after[0]] == [s.agent_id for s in showings]
    assert len(after[1]) == 1 and after[1][0].attendance == "unknown"
    assert statuses(after[2]) == statuses(before)


# ---------------------------------------------------------------------------
# The unit's residents, through the team's booking path
# ---------------------------------------------------------------------------


def lives_here(world: PublicWorld) -> None:
    world.residents.add("Morgan Ellis", RESIDENT, unit=UNIT)


async def resident_texts() -> list[ShowingNotice]:
    _, _, notices = await rows()
    return [n for n in notices if n.kind in residents.TEXT_KINDS]


@pytest.mark.asyncio
async def test_a_team_booking_with_texts_holds_the_residents_text_behind_its_confirmation(world):
    await live()
    lives_here(world)

    answer = await team_book(world, caller(mobile=CASEY_MOBILE, texts=True))

    assert answer.status_code == 201, answer.text
    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("prospect_confirmation", "sent"),
        ("host_event", "held"),
        (NEW, "held"),
    ]
    _, _, notices = await rows()
    confirmation = by_kind(notices)["prospect_confirmation"][0]
    (text,) = await resident_texts()
    assert (text.recipient, text.waits_for_notice_id) == (RESIDENT, confirmation.id)
    assert world.texts.to(RESIDENT) == []

    await world.send_pending(NOW + timedelta(minutes=1))

    assert [(n.kind, n.status) for n in await resident_texts()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_team_booking_without_texts_tells_the_residents_at_once(world):
    await live()
    lives_here(world)

    answer = await team_book(world, caller())

    assert [(n["kind"], n["status"]) for n in answer.json()["notices"]] == [
        ("host_event", "pending"),
        (NEW, "pending"),
    ]
    (text,) = await resident_texts()
    assert text.waits_for_notice_id is None

    await world.send_pending()

    assert [(n.kind, n.status) for n in await resident_texts()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_team_booking_without_texts_at_night_tells_the_residents_at_8_am(world):
    await live()
    lives_here(world)
    world.now = at(21, 30, day=TUESDAY)

    answer = await team_book(world, caller(at(16, day=WEDNESDAY + timedelta(days=1))))
    await world.send_pending(world.now)

    assert answer.status_code == 201, answer.text
    (text,) = await resident_texts()
    assert (text.status, text.not_before) == ("pending", at(8, 0, day=WEDNESDAY))
    assert world.texts.to(RESIDENT) == []

    await world.send_pending(at(8, 0, day=WEDNESDAY))

    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_team_join_brings_back_a_stopped_announcement(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    (text,) = await resident_texts()
    assert (text.status, text.last_error) == ("cancelled", holds.UNDELIVERED_NOTE)
    world.now = NOW + timedelta(minutes=2)

    answer = await team_book(world, caller())  # no texts: nothing to wait for

    assert answer.json()["kind"] == "join"
    assert (NEW, "pending") in [(n["kind"], n["status"]) for n in answer.json()["notices"]]
    (text,) = await resident_texts()
    assert (text.status, text.waits_for_notice_id, text.last_error) == ("pending", None, None)

    await world.send_pending(NOW + timedelta(minutes=3))

    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_team_join_with_texts_holds_the_brought_back_announcement(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    world.now = NOW + timedelta(minutes=2)

    answer = await team_book(world, caller(mobile=CASEY_MOBILE, texts=True))

    _, attendees, notices = await rows()
    casey_confirmation = next(
        n for n in by_kind(notices)["prospect_confirmation"] if n.attendee_id == attendees[1].id
    )
    (text,) = await resident_texts()
    assert (text.status, text.waits_for_notice_id) == ("held", casey_confirmation.id)
    assert (NEW, "held") in [(n["kind"], n["status"]) for n in answer.json()["notices"]]

    await world.send_pending(NOW + timedelta(minutes=3))

    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_failed_confirmation_hands_the_residents_text_to_a_caller_without_texts(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await page_book(world)  # the residents' text waits for Alex's confirmation
    await team_book(world, caller())  # Casey joins without texts

    await world.send_pending(NOW + timedelta(minutes=1))

    # Alex's text was undelivered, but Casey is still coming: the residents are told.
    assert [(n.kind, n.status) for n in await resident_texts()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_team_booking_of_a_vacant_unit_texts_no_resident(world):
    answer = await team_book(world, caller())

    assert answer.status_code == 201, answer.text
    assert await resident_texts() == []
    showings, _, _ = await rows()
    assert showings[0].resident_attention is None


@pytest.mark.asyncio
async def test_a_resident_lookup_problem_marks_the_showing_and_the_team_booking_stands(world):
    world.residents.fail()

    answer = await team_book(world, caller())

    assert answer.status_code == 201, answer.text
    assert await resident_texts() == []
    showings, _, _ = await rows()
    assert showings[0].resident_attention == "lookup_failed"


@pytest.mark.asyncio
async def test_a_withdrawn_team_booking_stops_the_residents_text(world):
    await live()
    lives_here(world)
    world.texts.refuse(CASEY_MOBILE)

    answer = await team_book(world, caller(mobile=CASEY_MOBILE, texts=True))

    assert refusal(answer) == (422, "number_refused")
    assert [(n.kind, n.status) for n in await resident_texts()] == [(NEW, "cancelled")]
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_a_change_of_host_and_attendance_tell_the_residents_nothing(world):
    await live()
    lives_here(world)
    sam = await add_sam()
    await page_book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    showings, (alex,), _ = await rows()
    before = [(n.id, n.status) for n in await resident_texts()]

    assert (await move(world, showings[0].id, sam)).status_code == 200
    world.now = at(16, 10)
    assert (await mark(world, showings[0].id, alex.id, "attended")).status_code == 200
    await world.send_pending(world.now)

    assert [(n.id, n.status) for n in await resident_texts()] == before
    assert len(world.texts.to(RESIDENT)) == 1


# ---------------------------------------------------------------------------
# The host's event says how each person booked
# ---------------------------------------------------------------------------


def test_the_host_event_description_is_true_for_every_source():
    guests = [
        wording.Guest("Alex Parker", ALEX_MOBILE, ALEX_EMAIL, ["No pet dogs."], source="page"),
        wording.Guest("Casey Morgan", None, None, [], source="team", texts=False),
        wording.Guest("Robin Lee", ROBIN_MOBILE, None, [], source="team"),
        wording.Guest("Lee Park", None, None, [], source="lead_agent", texts=False),
    ]

    assert wording.host_event_description(guests) == (
        "Booked for this Sernia Capital showing:\n"
        "- Alex Parker, 412-555-0117, alex@example.com (booked on the booking page)\n"
        "  Confirmed: No pet dogs.\n"
        "- Casey Morgan, no mobile, no email (booked by the team, no texts)\n"
        "- Robin Lee, 412-555-0118, no email (booked by the team)\n"
        "- Lee Park, no mobile, no email (booked by email with the lead agent, no texts)"
    )
    assert "booking page" not in wording.host_event_description(guests[1:])


@pytest.mark.asyncio
async def test_the_host_event_says_a_caller_was_booked_by_the_team(world):
    await live()
    await page_book(world)
    await team_book(world, caller())

    await world.send_pending(NOW + timedelta(minutes=1))

    showings, _, _ = await rows()
    description = world.calendar.events[showings[0].host_event_id].draft.description
    assert "Alex Parker, 412-555-0117, alex@example.com (booked on the booking page)" in description
    assert "Casey Morgan, no mobile, no email (booked by the team, no texts)" in description
