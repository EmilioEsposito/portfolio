"""A message that follows another goes to real people only when the other really went out.

Each scenario books in capture mode, then goes live in production (with the
stand-ins and a moved clock) and cancels, removes or moves: what follows a
captured message is not sent. The same steps in capture mode keep every message,
and with everything really sent they all go, as before. ``told.py`` has the rules.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.showing_bookings import (
    bookings,
    captured,
    delivery_checks,
    host_event,
    host_texts,
    open_times,
    reminders,
    residents,
    routes,
    told,
)
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_MOBILE,
    JANE,
    ROBIN_EMAIL,
    SHOWINGS_CALENDAR,
    TUESDAY,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    install,
    rows,
)

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
RESIDENT = "+14125550142"
JANE_MOBILE = "+14125550101"
SAM_MOBILE = "+14125550102"
INVITE_CANCEL = host_event.INVITE_CANCEL_KIND
HOST_CANCEL = host_event.CANCEL_KIND
RESIDENT_CANCEL = residents.CANCELLED_KIND


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SITE_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name in ("SHOWING_TEST_PHONES", "SHOWING_TEST_EMAILS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "stand-in")
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    state.residents.add("Morgan Ellis", RESIDENT, unit=UNIT)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await capture_mode()
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def capture_mode() -> None:
    await store_settings(send_mode="capture", public_booking_enabled=True)


async def live_mode() -> None:
    await store_settings(send_mode="live", live_units=[UNIT], public_booking_enabled=True)


def robin(start=None) -> dict:
    return booking(start, name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)


async def book(world: PublicWorld, body: dict | None = None) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=body or booking())
    assert answer.status_code == 201, answer.text
    await world.send_pending()
    return answer.json()


async def cancel(world: PublicWorld, booked: dict) -> None:
    async with world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/bookings/cancel", json={"cancel_token": booked["cancel_token"]}
        )
    assert answer.status_code == 200, answer.text
    await world.send_pending()


async def team_post(world: PublicWorld, path: str, body: dict | None = None) -> None:
    app = world.team_app()
    app.dependency_overrides[routes.clock] = lambda: world.now
    app.dependency_overrides[routes.text_sender] = lambda: world.texts
    async with world.client(app) as client:
        answer = await client.post(f"{TEAM}/showings/{path}", json=body)
    assert answer.status_code == 200, answer.text
    await world.send_pending()


async def add_sam() -> int:
    async with AsyncSessionFactory() as session:
        sam = ShowingAgentRecord(
            name="Sam Roe",
            email="sam.roe@example.com",
            mobile=SAM_MOBILE,
            busy_calendar_ids=["sam.roe@example.com"],
            priority=2,
        )
        session.add(sam)
        await session.commit()
        return sam.id


async def showing_id() -> int:
    showings, _, _ = await rows()
    return showings[0].id


async def found(kind: str) -> list[tuple[str, str | None]]:
    _, _, notices = await rows()
    return [(n.status, n.last_error) for n in notices if n.kind == kind]


# The residents


@pytest.mark.asyncio
async def test_residents_never_told_get_no_cancellation(world):
    booked = await book(world)
    assert await found(residents.NEW_SHOWING_KIND) == [("captured", None)]
    await live_mode()

    await cancel(world, booked)

    assert await found(RESIDENT_CANCEL) == [("cancelled", residents.NEVER_TOLD_NOTE)]
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_residents_told_get_the_cancellation(world):
    await live_mode()
    booked = await book(world)
    assert await found(residents.NEW_SHOWING_KIND) == [("sent", None)]

    await cancel(world, booked)

    assert await found(RESIDENT_CANCEL) == [("sent", None)]
    assert len(world.texts.to(RESIDENT)) == 2


@pytest.mark.asyncio
async def test_residents_told_by_a_reminder_get_the_cancellation(world):
    """The announcement was captured, but the evening's reminder listed the showing."""
    booked = await book(world)
    await live_mode()
    world.now = at(18, day=TUESDAY)
    await world.send_pending()
    assert len(world.texts.to(RESIDENT)) == 1

    await cancel(world, booked)

    assert await found(RESIDENT_CANCEL) == [("sent", None)]


@pytest.mark.asyncio
async def test_residents_who_may_have_been_told_get_the_cancellation(world):
    await live_mode()
    world.texts.lose_answer(RESIDENT)
    booked = await book(world)
    assert (await found(residents.NEW_SHOWING_KIND))[0][0] == "unknown"

    await cancel(world, booked)

    assert (await found(RESIDENT_CANCEL))[0][0] in ("sent", "unknown")
    assert len(world.texts.to(RESIDENT)) == 2


@pytest.mark.asyncio
async def test_capture_mode_still_keeps_every_cancellation(world):
    booked = await book(world)

    await cancel(world, booked)

    for kind in (RESIDENT_CANCEL, HOST_CANCEL, INVITE_CANCEL, "prospect_cancelled"):
        assert await found(kind) == [("captured", None)], kind
    assert world.texts.sent == [] and world.calendar.log == []


# The invite and the host's event


@pytest.mark.asyncio
async def test_an_event_and_an_invite_never_sent_are_not_cancelled(world):
    booked = await book(world)
    await live_mode()

    await cancel(world, booked)

    for kind in (HOST_CANCEL, INVITE_CANCEL):
        assert await found(kind) == [("cancelled", host_event.NEVER_CREATED)], kind
    assert world.calendar.log == []
    # The prospect booked it, so they are told it is cancelled.
    assert await found("prospect_cancelled") == [("sent", None)]
    assert len(world.texts.to(ALEX_MOBILE)) == 1


@pytest.mark.asyncio
async def test_an_event_and_an_invite_that_went_out_are_cancelled(world):
    await live_mode()
    booked = await book(world)

    await cancel(world, booked)

    assert await found(HOST_CANCEL) == await found(INVITE_CANCEL) == [("sent", None)]
    assert sorted(entry[0] for entry in world.calendar.log) == [
        "cancel",
        "cancel",
        "create",
        "create",
    ]


@pytest.mark.asyncio
async def test_an_update_because_someone_left_does_not_create_a_captured_event(world):
    await book(world)
    second = await book(world, robin())
    await live_mode()

    await cancel(world, second)

    updates = await found(host_event.UPDATE_KIND)
    assert updates[-1] == ("cancelled", told.HOST_NEVER_TOLD)
    assert world.calendar.log == []
    showings, _, _ = await rows()
    assert showings[0].host_event_id is None


@pytest.mark.asyncio
async def test_someone_who_joins_live_brings_the_whole_event(world):
    """The host must hear of a live booking, even when the showing's first was captured."""
    await book(world)
    await live_mode()

    await book(world, robin())

    assert await found(host_event.UPDATE_KIND) == [("sent", None)]
    host = [e for e in world.calendar.events_in(SHOWINGS_CALENDAR) if e.draft.attendees == [JANE]]
    assert [event.draft.summary for event in host] == ["Showing: 320-03 (2 people)"]
    assert await found(host_event.CREATE_KIND) == [("captured", None)]


@pytest.mark.asyncio
async def test_an_update_to_an_event_that_went_out_goes(world):
    await live_mode()
    await book(world)
    second = await book(world, robin())

    await cancel(world, second)

    assert [status for status, _ in await found(host_event.UPDATE_KIND)] == ["sent", "sent"]
    host = [e for e in world.calendar.events_in(SHOWINGS_CALENDAR) if e.draft.attendees == [JANE]]
    assert [event.draft.summary for event in host] == ["Showing: 320-03 (1 person)"]


@pytest.mark.asyncio
async def test_an_old_hosts_captured_event_is_not_cancelled_and_the_new_one_gets_theirs(world):
    await book(world)
    sam = await add_sam()
    await live_mode()

    await team_post(world, f"{await showing_id()}/host", {"agent_id": sam})

    assert await found(host_event.MOVED_KIND) == [("cancelled", host_event.NEVER_CREATED)]
    (event,) = world.calendar.events.values()
    assert event.draft.attendees == ["sam.roe@example.com"] and not event.cancelled


# The host's texts inside 2 hours


async def captured_then_live(world: PublicWorld, *, second: bool = False) -> list[dict]:
    """Booked in capture mode; now live, and less than 2 hours before the start."""
    booked = [await book(world)]
    if second:
        booked.append(await book(world, robin()))
    world.now = at(14, 30)
    await live_mode()
    return booked


@pytest.mark.asyncio
async def test_a_host_never_told_is_not_texted_that_someone_left(world):
    _, second = await captured_then_live(world, second=True)

    await cancel(world, second)

    assert await found(delivery_checks.HOST_LEFT_KIND) == [("cancelled", told.HOST_NEVER_TOLD)]
    assert world.texts.to(JANE_MOBILE) == []


@pytest.mark.asyncio
async def test_a_host_never_told_is_not_texted_that_it_is_cancelled(world):
    await captured_then_live(world)

    await team_post(world, f"{await showing_id()}/cancel")

    assert await found(delivery_checks.HOST_SHOWING_CANCELLED_KIND) == [
        ("cancelled", told.HOST_NEVER_TOLD)
    ]
    assert world.texts.to(JANE_MOBILE) == []


@pytest.mark.asyncio
async def test_a_host_never_told_is_not_texted_that_it_moved_away_but_the_new_one_is(world):
    await captured_then_live(world)
    sam = await add_sam()

    await team_post(world, f"{await showing_id()}/host", {"agent_id": sam})

    assert await found(delivery_checks.HOST_MOVED_FROM_KIND) == [
        ("cancelled", told.HOST_NEVER_TOLD)
    ]
    assert await found(delivery_checks.HOST_MOVED_TO_KIND) == [("sent", None)]
    assert world.texts.to(JANE_MOBILE) == [] and len(world.texts.to(SAM_MOBILE)) == 1


@pytest.mark.asyncio
async def test_a_host_is_texted_that_someone_booked_whatever_came_before(world):
    """It names the showing and who is coming, so it says something alone."""
    await book(world)
    world.now = at(14, 30)
    await live_mode()
    async with AsyncSessionFactory() as session, session.begin():
        rules = await listings_service.read_showing_rules(session)
        showing = await session.scalar(select(Showing))
        alex = await session.scalar(select(ShowingAttendee))
        notice_id = await host_texts.queue_join(session, showing, alex, rules=rules, now=world.now)

    await bookings.deliver_host_text(notice_id, now=world.now, texts=world.texts)

    assert await found(delivery_checks.HOST_JOIN_KIND) == [("sent", None)]
    assert len(world.texts.to(JANE_MOBILE)) == 1


@pytest.mark.asyncio
async def test_a_host_told_by_the_event_is_texted(world):
    await live_mode()
    await book(world)
    second = await book(world, robin())
    world.now = at(14, 30)

    await cancel(world, second)
    await team_post(world, f"{await showing_id()}/cancel")

    assert await found(delivery_checks.HOST_LEFT_KIND) == [("sent", None)]
    assert await found(delivery_checks.HOST_SHOWING_CANCELLED_KIND) == [("sent", None)]
    assert len(world.texts.to(JANE_MOBILE)) == 2


@pytest.mark.asyncio
async def test_a_host_told_by_the_morning_text_is_texted(world):
    """Booked in capture mode; the morning text of the day listed it, live."""
    await book(world)
    await live_mode()
    world.now = at(8)
    await world.send_pending()
    assert len(world.texts.to(JANE_MOBILE)) == 1
    world.now = at(14, 30)

    await team_post(world, f"{await showing_id()}/cancel")

    assert await found(delivery_checks.HOST_SHOWING_CANCELLED_KIND) == [("sent", None)]
    assert len(world.texts.to(JANE_MOBILE)) == 2


@pytest.mark.asyncio
async def test_a_host_whose_event_failed_is_still_texted_as_before(world):
    """Nothing for them was captured: they were meant to be told, and the team was alerted."""
    await live_mode()
    world.calendar.lose(SHOWINGS_CALENDAR)
    await book(world)
    assert (await found(host_event.CREATE_KIND))[0][0] == "failed"
    world.now = at(14, 30)

    await team_post(world, f"{await showing_id()}/cancel")

    assert await found(delivery_checks.HOST_SHOWING_CANCELLED_KIND) == [("sent", None)]


@pytest.mark.asyncio
async def test_in_capture_mode_every_host_text_is_still_kept(world):
    await book(world)
    second = await book(world, robin())
    sam = await add_sam()
    world.now = at(14, 30)

    await cancel(world, second)
    await team_post(world, f"{await showing_id()}/host", {"agent_id": sam})
    await team_post(world, f"{await showing_id()}/cancel")

    for kind in (
        delivery_checks.HOST_LEFT_KIND,
        delivery_checks.HOST_MOVED_FROM_KIND,
        delivery_checks.HOST_MOVED_TO_KIND,
        delivery_checks.HOST_SHOWING_CANCELLED_KIND,
    ):
        assert await found(kind) == [("captured", None)], kind
    assert world.texts.sent == []


# A unit no longer live, and what a person sent by hand


@pytest.mark.asyncio
async def test_a_unit_unticked_with_showings_booked_keeps_them_and_captures_their_messages(world):
    await live_mode()
    booked = await book(world)
    sent_before = len(world.texts.sent)
    await store_settings(send_mode="live", live_units=["412-01"], public_booking_enabled=True)

    world.now = at(18, day=TUESDAY)
    await world.send_pending()
    showings, (alex,), _ = await rows()
    assert (showings[0].status, alex.status) == ("scheduled", "booked")
    assert await found(delivery_checks.PROSPECT_REMINDER_KIND) == [("captured", None)]
    # The link in their text still works; what it sends is captured too.
    await cancel(world, booked)

    assert await found("prospect_cancelled") == [("captured", None)]
    assert await found(RESIDENT_CANCEL) == [("captured", None)]
    assert len(world.texts.sent) == sent_before


@pytest.mark.asyncio
async def test_an_announcement_sent_by_hand_counts_as_told(world):
    booked = await book(world)
    app = world.team_app()
    app.dependency_overrides[routes.clock] = lambda: world.now
    app.dependency_overrides[routes.text_sender] = lambda: world.texts
    async with AsyncSessionFactory() as session:
        announcement = await session.scalar(
            select(ShowingNotice.id).where(ShowingNotice.kind == residents.NEW_SHOWING_KIND)
        )
    async with world.client(app) as client:
        answer = await client.post(f"{TEAM}/notices/send-captured", json={"ids": [announcement]})
    assert answer.json()["results"][0]["outcome"] == "sent"
    await live_mode()

    await cancel(world, booked)

    assert await found(RESIDENT_CANCEL) == [("sent", None)]
    assert len(world.texts.to(RESIDENT)) == 2


@pytest.mark.asyncio
async def test_a_captured_cancellation_is_ready_once_what_it_follows_was_sent(world):
    """And not before, so nobody can send it by hand to residents who were never told."""
    booked = await book(world)
    await cancel(world, booked)

    async def state() -> str:
        async with AsyncSessionFactory() as session:
            notice = await session.scalar(
                select(ShowingNotice).where(ShowingNotice.kind == RESIDENT_CANCEL)
            )
            return (await captured._look(session, notice, UNIT, world.now)).state

    assert await state() == "no_longer_applies"
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == residents.NEW_SHOWING_KIND)
            .values(status="sent")
        )
    assert await state() == "ready"
