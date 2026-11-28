"""Captured messages, and sending them by hand from the Booking settings tab.

Each scenario books through the routes in capture mode, with the stand-ins and a
moved clock, then lists and sends through ``/api/showing-bookings/notices``.
Sends run as production (``RAILWAY_ENVIRONMENT_NAME``) unless a test says
otherwise; every provider is a stand-in, so nothing leaves the process.
"""

import asyncio
from datetime import datetime, timedelta
from itertools import product
from types import SimpleNamespace

import pytest
import pytest_asyncio
from logfire.testing import CaptureLogfire
from logfire.testing import capfire as capfire
from sqlalchemy import func, select, update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import (
    alerts,
    captured,
    delivery_checks,
    digests,
    holds,
    host_event,
    open_times,
    outbox,
    reminders,
    residents,
    retention,
    routes,
)
from api.src.showing_bookings.fakes import RecordingAlerter, RecordingTextSender
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import CalendarPayload, EmailPayload, TextPayload
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
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
    install,
    rows,
)

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
APPROVER = "pat@serniacapital.com"
RESIDENT = "+14125550142"
JANE_MOBILE = "+14125550101"
CONFIRMATION = host_event.CONFIRMATION_KIND
EVENT = host_event.CREATE_KIND
EVENT_UPDATE = host_event.UPDATE_KIND
INVITE = host_event.INVITE_KIND
NEW = residents.NEW_SHOWING_KIND
# Every booking texts the host (Jane, 412-555-0101) from the AI line.
HOST_TEXT = delivery_checks.HOST_JOIN_KIND
PERSON = SimpleNamespace(
    id="user_pat",
    email_addresses=[
        SimpleNamespace(email_address=APPROVER, verification=SimpleNamespace(status="verified"))
    ],
)


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("SHOWING_PUBLIC_BASE_URL", "SHOWING_TEST_PHONES", "SHOWING_TEST_EMAILS"):
        monkeypatch.delenv(name, raising=False)
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


@pytest.fixture
def production(monkeypatch):
    """From here on, as production: what a person sends really goes, to the stand-ins."""

    def turn_on():
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
        # Only so that a booking made after this passes Turnstile's stand-in.
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "stand-in")

    return turn_on


def team_app(world: PublicWorld):
    app = world.team_app()
    app.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    app.dependency_overrides[routes.clock] = lambda: world.now
    app.dependency_overrides[routes.text_sender] = lambda: world.texts
    app.dependency_overrides[routes.calendar_source] = lambda: world.calendar
    app.dependency_overrides[routes.email_sender] = lambda: world.emails
    return app


def lives_here(world: PublicWorld, mobile: str = RESIDENT) -> None:
    world.residents.add("Morgan Ellis", mobile, unit=UNIT)


def robin(start=None) -> dict:
    return booking(start, name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)


async def book(world: PublicWorld, body: dict | None = None) -> dict:
    """A booking on the page, and one run of the job, all captured."""
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


async def listed(world: PublicWorld) -> list[dict]:
    async with world.client(team_app(world)) as client:
        answer = await client.get(f"{TEAM}/notices/captured")
    assert answer.status_code == 200, answer.text
    return answer.json()


async def states(world: PublicWorld) -> dict[str, str]:
    """The list's state for each kind; for tests with one notice of a kind."""
    return {notice["kind"]: notice["state"] for notice in await listed(world)}


async def ids(*kinds: str) -> list[int]:
    """The captured notices' ids, all of them or of these kinds, oldest first."""
    async with AsyncSessionFactory() as session:
        query = select(ShowingNotice.id).where(ShowingNotice.status == "captured")
        if kinds:
            query = query.where(ShowingNotice.kind.in_(kinds))
        return list(await session.scalars(query.order_by(ShowingNotice.id)))


async def send(world: PublicWorld, *kinds: str, picked: list[int] | None = None) -> dict[int, dict]:
    """Send the captured notices of these kinds (or all) by hand; the results by id."""
    picked = picked if picked is not None else await ids(*kinds)
    async with world.client(team_app(world)) as client:
        answer = await client.post(f"{TEAM}/notices/send-captured", json={"ids": picked})
    assert answer.status_code == 200, answer.text
    results = answer.json()["results"]
    assert [result["id"] for result in results] == picked
    return {result["id"]: result for result in results}


async def outcomes(world: PublicWorld, *kinds: str) -> dict[str, str]:
    sent = await send(world, *kinds)
    async with AsyncSessionFactory() as session:
        kind_of = dict(
            (
                await session.execute(
                    select(ShowingNotice.id, ShowingNotice.kind).where(ShowingNotice.id.in_(sent))
                )
            ).all()
        )
    return {kind_of[notice_id]: result["outcome"] for notice_id, result in sent.items()}


async def one(kind: str) -> ShowingNotice:
    _, _, notices = await rows()
    (found,) = [notice for notice in notices if notice.kind == kind]
    return found


async def statuses_by_kind() -> dict[str, str]:
    _, _, notices = await rows()
    return {notice.kind: notice.status for notice in notices}


async def job(world: PublicWorld, now: datetime | None = None):
    if now is not None:
        world.now = now
    return await world.send_pending()


async def add_sam() -> int:
    async with AsyncSessionFactory() as session:
        sam = ShowingAgentRecord(
            name="Sam Roe",
            email="sam.roe@example.com",
            mobile="+14125550102",
            busy_calendar_ids=["sam.roe@example.com"],
            priority=2,
        )
        session.add(sam)
        await session.commit()
        return sam.id


async def give_to(world: PublicWorld, agent_id: int) -> None:
    showings, _, _ = await rows()
    async with world.client(team_app(world)) as client:
        answer = await client.post(
            f"{TEAM}/showings/{showings[0].id}/host", json={"agent_id": agent_id}
        )
    assert answer.status_code == 200, answer.text


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_list_says_what_each_is_to_whom_and_what_it_says(world, production):
    lives_here(world)
    await book(world)
    production()

    found = {notice["kind"]: notice for notice in await listed(world)}

    assert set(found) == {CONFIRMATION, EVENT, INVITE, NEW, HOST_TEXT}
    assert all(n["state"] == "ready" and n["reason"] is None for n in found.values())
    assert all(n["goes_out"] for n in found.values())
    assert {kind: n["to"] for kind, n in found.items()} == {
        CONFIRMATION: "Alex Parker, 412-555-0117",
        EVENT: f"Jane Doe, {JANE}",
        INVITE: f"Alex Parker, {ALEX_EMAIL}",
        NEW: "Residents, 412-555-0142",
        HOST_TEXT: "Jane Doe, 412-555-0101",
    }
    assert found[HOST_TEXT]["kind_label"] == "Host's text: someone booked"
    assert found[HOST_TEXT]["wording"].startswith(
        "Sernia Capital: Alex Parker booked your showing of 320 Example St (unit 320-03) "
        "on Wed, Apr 14 at 4:00 PM.\n\nYour upcoming showings:\n"
    )
    assert found[CONFIRMATION]["kind_label"] == "Prospect's confirmation"
    assert (
        "your in-person showing is booked for Wed, Apr 14 at 4:00 PM"
        in (found[CONFIRMATION]["wording"])
    )
    assert found[EVENT]["wording"].startswith("New event: Showing: 320-03 (1 person)")
    assert {n["unit"] for n in found.values()} == {UNIT}
    # They wait for the confirmation when it is sent with them, and only then.
    confirmation = found[CONFIRMATION]["id"]
    assert found[CONFIRMATION]["waits_for_notice_id"] is None
    assert found[HOST_TEXT]["waits_for_notice_id"] is None
    assert {found[kind]["waits_for_notice_id"] for kind in (EVENT, INVITE, NEW)} == {confirmation}
    assert not any(n["confirmation_on_its_way"] for n in found.values())
    # Listing them changes nothing.
    assert set((await statuses_by_kind()).values()) == {"captured"}


@pytest.mark.asyncio
async def test_a_text_to_residents_at_night_says_it_waits(world, production):
    lives_here(world)
    await book(world)
    production()
    world.now = at(21, 0, TUESDAY)

    found = {notice["kind"]: notice for notice in await listed(world)}

    assert found[NEW]["state"] == "ready"
    assert found[NEW]["note"] == "Residents get texts only between 8 AM and 9 PM."
    assert found[CONFIRMATION]["note"] is None
    world.now = at(20, 59, TUESDAY)
    assert {n["kind"]: n["note"] for n in await listed(world)}[NEW] is None


@pytest.mark.asyncio
async def test_an_event_is_listed_as_it_would_be_sent_now(world):
    await book(world)
    await book(world, robin())

    found = {notice["kind"]: notice for notice in await listed(world)}

    for kind in (EVENT, EVENT_UPDATE):
        assert "Showing: 320-03 (2 people)" in found[kind]["wording"]
        assert "Robin Lee, 412-555-0118" in found[kind]["wording"]


@pytest.mark.asyncio
async def test_outside_production_only_what_is_to_the_test_list_would_leave(world, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0117")
    await book(world)

    found = {notice["kind"]: notice for notice in await listed(world)}

    assert {kind: n["goes_out"] for kind, n in found.items()} == {
        CONFIRMATION: True,
        EVENT: False,
        INVITE: False,
        HOST_TEXT: False,
    }
    assert {n["state"] for n in found.values()} == {"ready"}


@pytest.mark.asyncio
async def test_only_captured_messages_are_listed(world, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0117")
    await store_settings(send_mode="test")
    await book(world)

    found = [notice["kind"] for notice in await listed(world)]

    # The confirmation was sent and delivered; the others were captured.
    assert (await one(CONFIRMATION)).status == "sent"
    assert CONFIRMATION not in found and set(found) == {EVENT, INVITE, HOST_TEXT}


# Too late


@pytest.mark.asyncio
async def test_once_the_showing_starts_its_messages_are_too_late(world, production):
    lives_here(world)
    await book(world)
    production()
    world.now = at(15, 59)
    assert set((await states(world)).values()) == {"ready"}

    world.now = at(16)
    found = await listed(world)

    assert {n["state"] for n in found} == {"too_late"}
    assert {n["reason"] for n in found} == {"The showing had started."}
    assert set((await outcomes(world)).values()) == {"not_sent"}
    assert world.texts.sent == [] and world.calendar.log == []
    assert set((await statuses_by_kind()).values()) == {"captured"}


@pytest.mark.asyncio
async def test_a_cancellation_is_too_late_at_the_original_time(world, production):
    booked = await book(world)
    await cancel(world, booked)
    production()
    world.now = at(15, 59)
    assert (await states(world))["prospect_cancelled"] == "ready"

    world.now = at(16)

    assert (await states(world))["prospect_cancelled"] == "too_late"


@pytest.mark.asyncio
async def test_the_morning_text_is_too_late_at_noon_of_its_day(world, production):
    await book(world)
    await job(world, at(8))
    production()
    world.now = at(11, 59)
    assert (await states(world))[delivery_checks.HOST_MORNING_KIND] == "ready"

    world.now = at(12)

    (morning,) = [n for n in await listed(world) if n["kind"] == delivery_checks.HOST_MORNING_KIND]
    assert (morning["state"], morning["reason"]) == ("too_late", "The morning had passed.")
    # The other messages are for the showing itself, still ahead.
    assert (await states(world))[CONFIRMATION] == "ready"


@pytest.mark.asyncio
async def test_the_apply_text_has_its_own_window(world, production):
    await book(world)
    await job(world, at(18))
    production()
    world.now = at(18) + timedelta(hours=40)
    assert (await states(world))[delivery_checks.APPLY_KIND] == "ready"

    world.now = at(16) + delivery_checks.APPLY_LOOKBACK
    (apply,) = [n for n in await listed(world) if n["kind"] == delivery_checks.APPLY_KIND]

    assert (apply["state"], apply["reason"]) == (
        "too_late",
        "The showing was more than two days ago.",
    )
    assert (await outcomes(world, delivery_checks.APPLY_KIND)) == {
        delivery_checks.APPLY_KIND: "not_sent"
    }
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_the_residents_reminder_lasts_until_the_last_showing_it_lists(world, production):
    lives_here(world)
    await book(world)
    await book(world, robin(at(17)))
    await job(world, at(8))
    production()
    world.now = at(16, 30)

    (reminder,) = [
        n for n in await listed(world) if n["kind"] == delivery_checks.RESIDENT_REMINDER_KIND
    ]

    # The first showing has started: what would be sent lists the other one only.
    assert reminder["state"] == "ready"
    assert "5:00 PM" in reminder["wording"] and "4:00 PM" not in reminder["wording"]
    world.now = at(17)
    assert (await states(world))[delivery_checks.RESIDENT_REMINDER_KIND] == "too_late"


@pytest.mark.asyncio
async def test_what_cannot_be_sent_leaves_the_list_after_two_days(world, production):
    booked = await book(world, booking(at(16, day=WEDNESDAY + timedelta(days=5))))
    await cancel(world, booked)
    production()
    kinds = {n["kind"]: n["state"] for n in await listed(world)}
    assert kinds[CONFIRMATION] == "no_longer_applies"
    assert kinds["prospect_cancelled"] == "ready"

    world.now = NOW + captured.KEEP_LISTED
    kinds = {n["kind"]: n["state"] for n in await listed(world)}

    # The cancellation can still be sent; the confirmation is left to the Messages list.
    assert kinds == {"prospect_cancelled": "ready"}
    async with world.client(team_app(world)) as client:
        messages = (await client.get(f"{TEAM}/notices")).json()
    assert CONFIRMATION in {notice["kind"] for notice in messages}


@pytest.mark.asyncio
async def test_ready_messages_come_first_then_the_rest(world, production):
    booked = await book(world)
    await book(world, robin(at(17)))
    await cancel(world, booked)
    production()

    found = [notice["state"] for notice in await listed(world)]

    assert found == sorted(found, key=lambda state: state != "ready")
    assert {"ready", "no_longer_applies"} == set(found)


# No longer applies


@pytest.mark.asyncio
async def test_a_cancelled_bookings_messages_no_longer_apply(world, production):
    lives_here(world)
    booked = await book(world)
    await cancel(world, booked)
    await job(world)
    production()

    found = {n["kind"]: n for n in await listed(world)}

    assert {kind: (n["state"], n["reason"]) for kind, n in found.items()} == {
        CONFIRMATION: ("no_longer_applies", "The booking was cancelled."),
        INVITE: ("no_longer_applies", "The booking was cancelled."),
        EVENT: ("no_longer_applies", "The showing has no one booked, or is cancelled."),
        NEW: ("no_longer_applies", "The showing was cancelled."),
        # The prospect booked it themselves, so this says something alone.
        "prospect_cancelled": ("ready", None),
        "prospect_invite_cancel": (
            "no_longer_applies",
            "The event was never created, so there is nothing to cancel.",
        ),
        "host_event_cancel": (
            "no_longer_applies",
            "The event was never created, so there is nothing to cancel.",
        ),
        residents.CANCELLED_KIND: (
            "no_longer_applies",
            "The residents' text about this showing never went out, so there is nothing to cancel.",
        ),
        HOST_TEXT: ("no_longer_applies", "The booking was cancelled."),
        delivery_checks.HOST_LEFT_KIND: (
            "no_longer_applies",
            "The host's messages about this showing were captured and never sent, so there is "
            "nothing to change.",
        ),
    }
    sent = await outcomes(world)
    assert sent.pop("prospect_cancelled") == "sent"
    assert set(sent.values()) == {"not_sent"}
    assert [text.to for text in world.texts.sent] == [ALEX_MOBILE]
    assert world.calendar.log == []


@pytest.mark.asyncio
async def test_a_text_to_a_number_corrected_since_no_longer_applies(world, production):
    booked = await book(world)
    async with world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": booked["cancel_token"], "mobile": ROBIN_MOBILE},
        )
    assert answer.status_code == 200, answer.text
    await job(world)
    production()

    found = {(n["kind"], n["to"]): n for n in await listed(world)}

    old = found[(CONFIRMATION, "Alex Parker, 412-555-0117")]
    assert (old["state"], old["reason"]) == (
        "no_longer_applies",
        "The booking's mobile was changed after this was written.",
    )
    new = found[(CONFIRMATION, "Alex Parker, 412-555-0118")]
    assert new["state"] == "ready"
    # The invite was captured behind the old text; it waits for the new one now.
    (invite,) = [n for n in found.values() if n["kind"] == INVITE]
    assert invite["waits_for_notice_id"] == new["id"]
    sent = await send(world, picked=[new["id"], invite["id"], old["id"]])
    assert sent[new["id"]]["outcome"] == "sent" and sent[old["id"]]["outcome"] == "not_sent"
    assert world.texts.to(ALEX_MOBILE) == [] and len(world.texts.to(ROBIN_MOBILE)) == 1
    assert (await one(INVITE)).waits_for_notice_id == new["id"]


@pytest.mark.asyncio
async def test_a_number_known_to_be_wrong_is_not_texted(world, production):
    booked = await book(world)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee).values(attention_reason="confirmation_undelivered")
        )
    production()
    found = {n["kind"]: n for n in await listed(world)}
    assert (found[CONFIRMATION]["state"], found[CONFIRMATION]["reason"]) == (
        "no_longer_applies",
        "This person is not texted any more (no mobile, no consent, or a wrong number).",
    )
    assert (await outcomes(world, CONFIRMATION)) == {CONFIRMATION: "not_sent"}
    assert world.texts.sent == []
    assert booked


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sending_them_all_sends_the_text_and_then_what_waits_for_it(world, production):
    lives_here(world)
    await book(world)
    production()
    world.texts.report(ALEX_MOBILE, "queued")

    sent = await outcomes(world)

    assert sent == {
        CONFIRMATION: "sent",
        EVENT: "waiting",
        INVITE: "waiting",
        NEW: "waiting",
        HOST_TEXT: "sent",
    }
    assert [(text.to, text.line) for text in world.texts.sent] == [
        (ALEX_MOBILE, "team"),
        (JANE_MOBILE, "ai"),
    ]
    _, _, notices = await rows()
    assert {(n.send_approved_by, n.send_approved_at) for n in notices} == {(APPROVER, NOW)}
    confirmation = await one(CONFIRMATION)
    assert (confirmation.guard_reason, confirmation.mode) == ("approved", "capture")
    assert {n.status for n in notices if n.kind not in (CONFIRMATION, HOST_TEXT)} == {"held"}
    # While they wait, they are not on the list any more.
    assert await listed(world) == []

    # The every-minute job: the report arrives, and they go, still in capture mode.
    world.texts.report(ALEX_MOBILE, "delivered")
    report = await job(world, NOW + timedelta(minutes=1))

    assert (report.released, report.sent) == (3, 3)
    assert set((await statuses_by_kind()).values()) == {"sent"}
    assert len(world.texts.to(RESIDENT)) == 1
    (event, invite) = world.calendar.events_in(SHOWINGS_CALENDAR)
    assert event.draft.attendees == [JANE] and invite.draft.attendees == [ALEX_EMAIL]


@pytest.mark.asyncio
async def test_the_booking_page_says_texted_once_it_is_sent_by_hand(world, production):
    booked = await book(world)
    assert booked["booking"]["texted"] is False
    production()
    await store_settings(public_booking_enabled=True)

    await send(world, CONFIRMATION)

    async with world.client() as client:
        seen = await client.post(
            f"{PUBLIC}/bookings/lookup", json={"cancel_token": booked["cancel_token"]}
        )
    assert seen.json()["texted"] is True


@pytest.mark.asyncio
async def test_what_waits_goes_at_once_when_the_report_is_already_in(world, production):
    await book(world)
    production()

    assert await outcomes(world) == {
        CONFIRMATION: "sent",
        EVENT: "sent",
        INVITE: "sent",
        HOST_TEXT: "sent",
    }
    assert len(world.calendar.events) == 2


@pytest.mark.asyncio
async def test_it_goes_in_any_mode(world, production):
    production_settings = [
        {"send_mode": "test"},
        {"send_mode": "capture"},
        {"send_mode": "live", "live_units": []},
        {"send_mode": "live", "live_all_units": True},
    ]
    starts = [at(16), at(17), at(16, day=WEDNESDAY + timedelta(days=1)), at(17, 30)]
    for number, start in enumerate(starts):
        await book(
            world, booking(start, mobile=f"412-555-011{number}", email=f"a{number}@example.com")
        )
    production()
    confirmations = await ids(CONFIRMATION)

    for number, (settings, notice_id) in enumerate(
        zip(production_settings, confirmations, strict=True)
    ):
        await store_settings(**settings)
        sent = await send(world, picked=[notice_id])
        assert sent[notice_id]["outcome"] == "sent", settings
        assert len(world.texts.to(f"+1412555011{number}")) == 1
    async with AsyncSessionFactory() as session:
        saved = await session.execute(
            select(ShowingNotice.mode, ShowingNotice.guard_reason)
            .where(ShowingNotice.id.in_(confirmations))
            .order_by(ShowingNotice.id)
        )
        assert saved.all() == [
            (settings["send_mode"], "approved") for settings in production_settings
        ]


@pytest.mark.asyncio
async def test_only_the_ones_picked_are_sent(world, production):
    lives_here(world)
    await book(world)
    production()

    assert await outcomes(world, NEW) == {NEW: "sent"}

    # The confirmation was left unsent by choice, so the residents' text went by itself.
    assert [text.to for text in world.texts.sent] == [RESIDENT]
    _, _, notices = await rows()
    for notice in notices:
        if notice.kind != NEW:
            assert notice.status == "captured" and notice.send_approved_at is None, notice.kind
    for later in (1, 10, 60 * 24):
        await job(world, NOW + timedelta(minutes=later))
    assert [text.to for text in world.texts.sent] == [RESIDENT]
    assert world.calendar.log == []


@pytest.mark.asyncio
async def test_outside_production_only_the_test_list_is_reached(world, monkeypatch):
    lives_here(world)
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0117")
    for mode, environment in product(["test", "capture", "live"], [None, "development", "pr-1"]):
        await clear_showing_data()
        await add_world_rows(world)
        world.texts, world.now = RecordingTextSender(), NOW
        if environment:
            monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", environment)
        else:
            monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
        # Captured first, then the mode: a preview's copy of any row of production's.
        await book(world)
        await store_settings(send_mode=mode, live_units=[UNIT], live_all_units=True)

        sent = await outcomes(world)
        await job(world, NOW + timedelta(minutes=10))

        assert sent == {
            CONFIRMATION: "sent",
            EVENT: "kept",
            INVITE: "kept",
            NEW: "kept",
            HOST_TEXT: "kept",
        }, mode
        assert {text.to for text in world.texts.sent} == {ALEX_MOBILE}
        assert world.calendar.log == []
        for kind in (EVENT, INVITE, NEW, HOST_TEXT):
            kept = await one(kind)
            assert (kept.status, kept.guard_reason) == ("captured", "not_production")


@pytest.mark.asyncio
async def test_a_preview_with_rows_production_had_approved_sends_nobody_else_anything(world):
    """A preview's database copied while production was sending by hand."""
    lives_here(world)
    await book(world)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice).values(
                status="pending", send_approved_by=APPROVER, send_approved_at=NOW, not_before=NOW
            )
        )
    await store_settings(send_mode="live", live_all_units=True)

    report = await job(world, NOW + timedelta(minutes=1))

    assert (report.sent, report.captured) == (0, 5)
    assert world.texts.sent == [] and world.calendar.log == []


# Once only


@pytest.mark.asyncio
async def test_a_message_already_handled_is_reported_and_not_sent_again(world, production):
    await book(world)
    production()
    picked = await ids()
    first = await send(world, picked=picked)
    assert {result["outcome"] for result in first.values()} == {"sent"}

    second = await send(world, picked=picked)

    assert {result["outcome"] for result in second.values()} == {"handled"}
    assert len(world.texts.sent) == 2 and len(world.calendar.log) == 2


@pytest.mark.asyncio
async def test_two_people_pressing_at_once_send_each_once(world, production):
    lives_here(world)
    await book(world)
    production()
    picked = await ids()

    results = await asyncio.gather(*(send(world, picked=picked) for _ in range(3)))
    await job(world, NOW + timedelta(minutes=6))

    assert [text.to for text in world.texts.sent] == [ALEX_MOBILE, JANE_MOBILE, RESIDENT]
    assert [entry[0] for entry in world.calendar.log] == ["create", "create"]
    assert set((await statuses_by_kind()).values()) == {"sent"}
    for notice_id in picked:
        assert sorted(result[notice_id]["outcome"] != "handled" for result in results) == [
            False,
            False,
            True,
        ]


@pytest.mark.asyncio
async def test_a_person_and_the_job_at_once_send_each_once(world, production):
    lives_here(world)
    await book(world)
    production()
    picked = await ids()

    await asyncio.gather(send(world, picked=picked), world.send_pending(), world.send_pending())
    await job(world, NOW + timedelta(minutes=6))

    assert sorted(text.to for text in world.texts.sent) == sorted(
        [ALEX_MOBILE, JANE_MOBILE, RESIDENT]
    )
    assert [entry[0] for entry in world.calendar.log] == ["create", "create"]


class ProcessDied(BaseException):
    """Escapes everything, the way a killed process does."""


class DiesSending(RecordingTextSender):
    async def send_text(self, to: str, body: str, *, line: str = "team"):
        raise ProcessDied()


@pytest.mark.asyncio
async def test_a_process_that_stops_midway_is_finished_by_the_job(world, production):
    lives_here(world)
    await book(world)
    await book(world, robin(at(17)))
    production()
    picked = await ids(CONFIRMATION, NEW)

    with pytest.raises(ProcessDied):
        await captured.send(
            picked, by=APPROVER, now=NOW, texts=DiesSending(), calendar=world.calendar
        )

    # The first text may have gone out; the others were not reached and are pending.
    _, _, notices = await rows()
    texts = [n for n in notices if n.kind == CONFIRMATION]
    assert [(n.status, n.sending_started_at is not None) for n in texts] == [
        ("pending", True),
        ("pending", False),
    ]
    assert all(n.send_approved_by == APPROVER for n in notices if n.id in picked)
    await job(world, NOW + timedelta(minutes=1))
    assert world.texts.to(ROBIN_MOBILE) and not world.texts.to(ALEX_MOBILE)
    await job(world, NOW + outbox.INTERRUPTED_AFTER + timedelta(minutes=1))
    await job(world, NOW + outbox.INTERRUPTED_AFTER + timedelta(minutes=10))
    # Never sent twice: the one cut off midway is unknown, and what waited for it goes.
    _, _, notices = await rows()
    assert [n.status for n in notices if n.kind == CONFIRMATION] == ["unknown", "sent"]
    assert {n.status for n in notices if n.kind == NEW} == {"sent"}
    assert world.texts.to(ALEX_MOBILE) == []


@pytest.mark.asyncio
async def test_what_one_request_has_no_time_for_goes_with_the_job(world, production, monkeypatch):
    monkeypatch.setattr(captured, "DELIVER_FOR_SECONDS", -1)
    await book(world)
    production()

    sent = await send(world, CONFIRMATION)

    (result,) = sent.values()
    assert (result["outcome"], result["detail"]) == ("waiting", "Goes within a minute.")
    assert world.texts.sent == []
    assert (await job(world, NOW + timedelta(minutes=1))).sent == 1


@pytest.mark.asyncio
async def test_a_message_that_is_not_there_or_not_captured_is_answered(world, production):
    await book(world)
    production()
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == INVITE)
            .values(status="cancelled", last_error="Not sent: a test.")
        )
    invite = await one(INVITE)

    sent = await send(world, picked=[invite.id, 123456789])

    assert sent[invite.id]["outcome"] == "handled"
    assert sent[123456789]["outcome"] == "not_sent"
    assert (await one(INVITE)).status == "cancelled"
    async with world.client(team_app(world)) as client:
        for body in (
            {"ids": []},
            {"ids": list(range(1, 300))},
            {"ids": ["x"]},
            {"ids": [0]},
            {"ids": [2**31]},
            {},
        ):
            answer = await client.post(f"{TEAM}/notices/send-captured", json=body)
            assert answer.status_code == 422, body


# The confirmation text


@pytest.mark.asyncio
async def test_a_text_that_does_not_reach_the_number_stops_what_waits(world, production):
    lives_here(world)
    await book(world)
    production()
    world.texts.report(ALEX_MOBILE, "queued")
    await send(world)

    world.texts.report(ALEX_MOBILE, "undelivered")
    await job(world, NOW + timedelta(minutes=1))

    found = await statuses_by_kind()
    assert found == {
        CONFIRMATION: "sent",
        EVENT: "cancelled",
        INVITE: "cancelled",
        NEW: "cancelled",
        HOST_TEXT: "sent",
    }
    _, (alex,), _ = await rows()
    assert alex.attention_reason == "confirmation_undelivered"
    # The booking stands, so the host's text, which waits for nothing, went.
    assert [text.to for text in world.texts.sent] == [ALEX_MOBILE, JANE_MOBILE]
    assert world.calendar.log == []


@pytest.mark.asyncio
async def test_with_no_report_they_go_after_five_minutes(world, production):
    await book(world)
    production()
    world.texts.report(ALEX_MOBILE, "queued")
    await send(world)
    await job(world, NOW + timedelta(minutes=4, seconds=59))
    assert (await one(EVENT)).status == "held"

    await job(world, NOW + holds.REPORT_WAIT)

    assert (await one(EVENT)).status == (await one(INVITE)).status == "sent"


@pytest.mark.asyncio
async def test_one_picked_while_its_text_is_on_its_way_waits_for_it(world, production):
    await book(world)
    production()
    world.texts.report(ALEX_MOBILE, "queued")
    await send(world, CONFIRMATION)
    found = {n["kind"]: n for n in await listed(world)}
    assert found[EVENT]["confirmation_on_its_way"] and found[EVENT]["state"] == "ready"
    assert found[EVENT]["waits_for_notice_id"] is None

    assert await outcomes(world, EVENT) == {EVENT: "waiting"}

    world.texts.report(ALEX_MOBILE, "delivered")
    await job(world, NOW + timedelta(minutes=1))
    assert (await one(EVENT)).status == "sent"
    assert (await one(INVITE)).status == "captured"


@pytest.mark.asyncio
async def test_one_whose_text_did_not_arrive_no_longer_applies(world, production):
    await book(world)
    production()
    world.texts.report(ALEX_MOBILE, "undelivered")
    await send(world, CONFIRMATION, EVENT)

    found = {n["kind"]: n for n in await listed(world)}

    assert (found[INVITE]["state"], found[INVITE]["reason"]) == (
        "no_longer_applies",
        "Its confirmation text did not reach the person.",
    )
    assert await outcomes(world, INVITE) == {INVITE: "not_sent"}
    assert world.calendar.log == []


@pytest.mark.asyncio
async def test_a_refused_number_marks_the_booking_and_alerts_the_team(world, production):
    """Nobody is on the booking page to be told, so it is not withdrawn either."""
    await book(world)
    production()
    world.texts.refuse(ALEX_MOBILE, "invalid_number")

    sent = await send(world)

    by_kind = {result["kind_label"]: result for result in sent.values()}
    assert by_kind["Prospect's confirmation"]["outcome"] == "not_sent"
    _, (alex,), _ = await rows()
    assert (alex.status, alex.attention_reason) == ("booked", "confirmation_refused")
    assert (await one(EVENT)).status == (await one(INVITE)).status == "cancelled"
    alerter = RecordingAlerter()
    assert await alerts.check(now=NOW + timedelta(minutes=1), alerter=alerter) == 1
    assert alerter.alerts[0].body.startswith("Prospect's confirmation for unit 320-03")


@pytest.mark.asyncio
async def test_a_refused_number_is_marked_with_nothing_waiting_for_the_text(world, production):
    await book(world)
    production()
    world.texts.refuse(ALEX_MOBILE, "opted_out")

    assert await outcomes(world, CONFIRMATION) == {CONFIRMATION: "not_sent"}

    _, (alex,), _ = await rows()
    assert alex.attention_reason == "confirmation_refused"
    # Nothing more is texted to it: the reminder is not queued.
    await job(world, at(8))
    assert delivery_checks.PROSPECT_REMINDER_KIND not in await statuses_by_kind()


@pytest.mark.asyncio
async def test_a_corrected_mobile_does_not_send_what_was_stopped(world, production):
    """Stopped after it was sent by hand, it follows the mode again: capture."""
    lives_here(world)
    booked = await book(world)
    production()
    await store_settings(public_booking_enabled=True)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await send(world)
    assert (await one(EVENT)).status == (await one(NEW)).status == "cancelled"

    async with world.client() as client:
        answer = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": booked["cancel_token"], "mobile": ROBIN_MOBILE},
        )
    assert answer.status_code == 200, answer.text
    for minutes in (1, 10):
        await job(world, NOW + timedelta(minutes=minutes))

    assert [text.to for text in world.texts.sent] == [ALEX_MOBILE, JANE_MOBILE]
    assert world.calendar.log == []
    for kind in (EVENT, INVITE, NEW):
        stopped = await one(kind)
        assert (stopped.status, stopped.send_approved_at) == ("captured", None), kind
    assert {n["kind"]: n["state"] for n in await listed(world) if n["kind"] != CONFIRMATION} == {
        EVENT: "ready",
        INVITE: "ready",
        NEW: "ready",
    }


@pytest.mark.asyncio
async def test_a_correction_while_they_wait_leaves_them_to_the_mode(world, production):
    booked = await book(world)
    production()
    await store_settings(public_booking_enabled=True)
    world.texts.report(ALEX_MOBILE, "queued")
    await send(world)

    async with world.client() as client:
        await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": booked["cancel_token"], "mobile": ROBIN_MOBILE},
        )
    await job(world, NOW + timedelta(minutes=6))

    assert [text.to for text in world.texts.sent] == [ALEX_MOBILE, JANE_MOBILE]
    assert world.calendar.log == []
    assert (await one(EVENT)).status == (await one(INVITE)).status == "captured"


@pytest.mark.asyncio
async def test_a_join_does_not_send_the_residents_text_that_was_stopped(world, production):
    """Stopped after it was sent by hand, a join brings it back to follow the mode: capture."""
    lives_here(world)
    await book(world)
    production()
    await store_settings(public_booking_enabled=True)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await send(world)
    assert (await one(NEW)).status == "cancelled"

    await book(world, robin())
    await job(world, NOW + timedelta(minutes=10))

    brought = await one(NEW)
    assert (brought.status, brought.send_approved_at, brought.last_error) == (
        "captured",
        None,
        None,
    )
    assert world.texts.to(RESIDENT) == []


# Checked again as they go


@pytest.mark.asyncio
async def test_a_residents_text_waits_for_the_daytime_and_then_goes(world, production):
    lives_here(world)
    await book(world)
    production()
    world.now = at(21, 30, TUESDAY)

    sent = await send(world, NEW)

    (result,) = sent.values()
    assert (result["outcome"], result["detail"]) == (
        "waiting",
        "Residents get texts only between 8 AM and 9 PM.",
    )
    assert datetime.fromisoformat(result["waits_until"]) == at(8)
    assert (await job(world, at(7, 59))).sent == 0
    assert (await job(world, at(8))).sent >= 1
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_showing_cancelled_before_it_goes_stops_it(world, production):
    lives_here(world)
    booked = await book(world)
    production()
    await store_settings(public_booking_enabled=True)
    world.now = at(21, 30, TUESDAY)
    await send(world, NEW)

    await cancel(world, booked)
    await job(world, at(8))

    assert world.texts.to(RESIDENT) == []
    assert (await one(NEW)).status == "cancelled"
    assert residents.CANCELLED_KIND not in await statuses_by_kind()


@pytest.mark.asyncio
async def test_one_that_becomes_too_late_before_it_goes_is_not_sent(world, production):
    await book(world)
    production()
    world.texts.refuse(ALEX_MOBILE, "unavailable", retryable=True)
    world.now = at(15, 59)
    (result,) = (await send(world, CONFIRMATION)).values()
    assert result["outcome"] == "waiting" and "Nothing went out yet" in result["detail"]

    world.texts.accept(ALEX_MOBILE)
    await job(world, at(16, 1))

    confirmation = await one(CONFIRMATION)
    assert (confirmation.status, confirmation.last_error) == ("cancelled", delivery_checks.STARTED)
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_the_reminder_goes_to_the_mobile_on_the_booking_then(world, production):
    booked = await book(world)
    await job(world, at(8))
    async with world.client() as client:
        await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": booked["cancel_token"], "mobile": ROBIN_MOBILE},
        )
    production()

    assert await outcomes(world, delivery_checks.PROSPECT_REMINDER_KIND) == {
        delivery_checks.PROSPECT_REMINDER_KIND: "sent"
    }

    assert len(world.texts.to(ROBIN_MOBILE)) == 1 and world.texts.to(ALEX_MOBILE) == []


@pytest.mark.asyncio
async def test_the_apply_text_is_checked_for_a_no_show(world, production):
    await book(world)
    await job(world, at(18))
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(ShowingAttendee).values(attendance="no_show"))
    production()

    found = {n["kind"]: n for n in await listed(world)}
    assert found[delivery_checks.APPLY_KIND]["reason"] == "This person was marked a no-show."
    assert await outcomes(world, delivery_checks.APPLY_KIND) == {
        delivery_checks.APPLY_KIND: "not_sent"
    }
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_the_morning_text_by_hand_lists_every_showing_of_the_hosts_day(world, production):
    """In live mode too, where by itself it lists only the units that are live."""
    other = world.listing.model_copy(update={"unit": "320-05"})
    world.other_listings.append(other)
    await book(world)
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/320-05/bookings", json=robin(at(17)))
    assert answer.status_code == 201, answer.text
    await job(world, at(8))
    production()
    for settings in ({"send_mode": "capture"}, {"send_mode": "live", "live_units": ["320-05"]}):
        await store_settings(**settings)
        (morning,) = [
            n for n in await listed(world) if n["kind"] == delivery_checks.HOST_MORNING_KIND
        ]
        assert "4:00 PM" in morning["wording"] and "5:00 PM" in morning["wording"], settings
    world.now = at(9)

    assert await outcomes(world, delivery_checks.HOST_MORNING_KIND) == {
        delivery_checks.HOST_MORNING_KIND: "sent"
    }

    (text,) = world.texts.to(JANE_MOBILE)
    assert "4:00 PM" in text and "5:00 PM" in text and "320-05" in text


@pytest.mark.asyncio
async def test_a_leads_reminder_by_email_is_not_sent_without_its_route(world, production):
    await book(world)
    async with AsyncSessionFactory() as session, session.begin():
        showing = await session.scalar(select(Showing))
        lead = ShowingAttendee(
            showing_id=showing.id, name="Zillow lead", source="lead_agent", lead_reply_sent_at=NOW
        )
        session.add(lead)
        await session.flush()
        notice = await outbox.enqueue(
            session,
            kind=delivery_checks.LEAD_EMAIL_REMINDER_KIND,
            channel="email",
            showing_id=showing.id,
            attendee_id=lead.id,
            recipient="relay@example.com",
            recipient_key=f"attendee:{lead.id}",
            payload=EmailPayload(subject="Re: hi", body="Hello"),
            now=NOW,
        )
        notice.status, notice.delivered_at = "captured", NOW
    await store_settings(lead_agent_uses_open_times=True)
    production()

    (found,) = [n for n in await listed(world) if n["channel"] == "email"]

    assert found["state"] == "no_longer_applies"
    assert found["reason"] == "This booking has no lead conversation to answer in."
    assert world.emails.sent == []


# The host


@pytest.mark.asyncio
async def test_several_captured_for_one_showing_end_as_one_correct_event(world, production):
    await book(world)
    await book(world, robin())
    production()
    world.texts.report(ALEX_MOBILE, "queued")
    world.texts.report(ROBIN_MOBILE, "queued")

    sent = await send(world)
    world.texts.report(ALEX_MOBILE, "delivered")
    world.texts.report(ROBIN_MOBILE, "delivered")
    await job(world, NOW + timedelta(minutes=1))

    host_events = [
        e for e in world.calendar.events_in(SHOWINGS_CALENDAR) if e.draft.attendees == [JANE]
    ]
    assert len(host_events) == 1
    assert host_events[0].draft.summary == "Showing: 320-03 (2 people)"
    assert "Robin Lee" in host_events[0].draft.description
    # The other one would have changed nothing, so the host is not told twice.
    _, _, notices = await rows()
    host = [n for n in notices if n.kind in host_event.KINDS]
    assert sorted((n.status, n.last_error) for n in host) == [
        ("cancelled", host_event.ALREADY_SAYS),
        ("sent", None),
    ]
    assert len(sent) == 8
    (host_text,) = world.texts.to(JANE_MOBILE)
    assert "Alex Parker booked" in host_text and "Robin Lee booked" in host_text
    showings, _, _ = await rows()
    assert showings[0].host_event_id == host_events[0].event_id


@pytest.mark.asyncio
async def test_an_update_sent_alone_creates_the_event_and_the_create_then_adds_nothing(
    world, production
):
    await book(world)
    await book(world, robin())
    production()

    assert await outcomes(world, EVENT_UPDATE) == {EVENT_UPDATE: "sent"}
    assert (await states(world))[EVENT] == "no_longer_applies"
    sent = await send(world, EVENT)

    (result,) = sent.values()
    assert (result["outcome"], result["detail"]) == (
        "not_sent",
        "The host's event already says this.",
    )
    assert [entry[0] for entry in world.calendar.log] == ["create"]


@pytest.mark.asyncio
async def test_a_change_after_the_event_was_sent_by_hand_updates_it(world, production):
    await book(world)
    await send_as_production(world, production, EVENT)
    await book(world, robin())
    assert (await states(world))[EVENT_UPDATE] == "ready"

    assert await outcomes(world, EVENT_UPDATE) == {EVENT_UPDATE: "sent"}

    assert [entry[0] for entry in world.calendar.log] == ["create", "update"]
    (event,) = world.calendar.events.values()
    assert event.draft.summary == "Showing: 320-03 (2 people)"


async def send_as_production(world: PublicWorld, production, *kinds: str) -> None:
    await store_settings(public_booking_enabled=True)
    production()
    assert set((await outcomes(world, *kinds)).values()) == {"sent"}


@pytest.mark.asyncio
async def test_a_cancellation_by_hand_cancels_the_event_that_was_sent_by_hand(
    world, production, monkeypatch
):
    booked = await book(world)
    await send_as_production(world, production)
    await cancel(world, booked)
    await job(world, NOW + timedelta(minutes=1))
    found = await states(world)
    assert found["host_event_cancel"] == found["prospect_invite_cancel"] == "ready"

    sent = await outcomes(world)

    assert set(sent.values()) == {"sent"}
    assert sorted(entry[0] for entry in world.calendar.log) == [
        "cancel",
        "cancel",
        "create",
        "create",
    ]
    assert all(event.cancelled for event in world.calendar.events.values())


@pytest.mark.asyncio
async def test_after_a_move_the_old_hosts_messages_no_longer_apply(world, production):
    await book(world)
    sam = await add_sam()
    await give_to(world, sam)
    await job(world, NOW + timedelta(minutes=1))
    production()

    found = {}
    for notice in await listed(world):
        found.setdefault(notice["kind"], []).append(notice)

    old, new = sorted(found[EVENT], key=lambda n: n["id"])
    assert (old["state"], old["reason"]) == (
        "no_longer_applies",
        "The showing moved to another host.",
    )
    assert (new["state"], new["to"]) == ("ready", "Sam Roe, sam.roe@example.com")
    (moved,) = found[host_event.MOVED_KIND]
    assert (moved["state"], moved["reason"]) == (
        "no_longer_applies",
        "The event was never created, so there is nothing to cancel.",
    )
    sent = await send(world, EVENT, host_event.MOVED_KIND)
    assert {notice_id: result["outcome"] for notice_id, result in sent.items()} == {
        old["id"]: "not_sent",
        new["id"]: "sent",
        moved["id"]: "not_sent",
    }
    (event,) = world.calendar.events.values()
    assert event.draft.attendees == ["sam.roe@example.com"] and not event.cancelled


@pytest.mark.asyncio
async def test_an_old_hosts_event_sent_by_hand_is_cancelled_by_hand_after_a_move(world, production):
    await book(world)
    sam = await add_sam()
    await send_as_production(world, production, EVENT)
    await book(world, robin())  # an update for the old host, captured and never sent

    await give_to(world, sam)
    await job(world, NOW + timedelta(minutes=1))

    found = {}
    for notice in await listed(world):
        found.setdefault(notice["kind"], []).append(notice["state"])
    assert found[host_event.MOVED_KIND] == ["ready"]
    assert found[EVENT_UPDATE] == ["no_longer_applies"]
    sent = await outcomes(world, host_event.MOVED_KIND, EVENT)
    assert sent == {host_event.MOVED_KIND: "sent", EVENT: "sent"}
    old, new = world.calendar.events.values()
    assert (old.draft.attendees, old.cancelled) == ([JANE], True)
    assert (new.draft.attendees, new.cancelled) == (["sam.roe@example.com"], False)


@pytest.mark.asyncio
async def test_a_host_changed_before_it_goes_stops_the_old_hosts_event(world, production):
    await book(world)
    sam = await add_sam()
    production()
    world.texts.report(ALEX_MOBILE, "queued")
    await send(world, CONFIRMATION, EVENT)

    await give_to(world, sam)
    world.texts.report(ALEX_MOBILE, "delivered")
    await job(world, NOW + timedelta(minutes=1))

    # Nobody sent the new host's event by hand, and the mode is capture.
    assert world.calendar.log == []
    _, _, notices = await rows()
    host = {n.recipient: n.status for n in notices if n.kind == EVENT}
    assert host == {JANE: "cancelled", "sam.roe@example.com": "captured"}


@pytest.mark.asyncio
async def test_the_hosts_text_goes_to_the_host_it_is_now(world, production):
    await book(world)
    sam = await add_sam()
    # Someone joins two hours before the start: the host is owed a text for each.
    world.now = at(14)
    await book(world, robin())
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(Showing).values(agent_id=sam))
    production()

    joined = [n for n in await listed(world) if n["kind"] == HOST_TEXT]
    assert [n["to"] for n in joined] == ["Sam Roe, 412-555-0102"] * 2
    sent = await send(world, HOST_TEXT)
    assert [result["outcome"] for result in sent.values()] == ["sent", "not_sent"]
    (text,) = world.texts.to("+14125550102")
    assert "Alex Parker booked" in text and "Robin Lee booked" in text
    assert world.texts.to(JANE_MOBILE) == []


@pytest.mark.asyncio
async def test_an_event_captured_before_a_calendar_was_connected_goes_to_the_calendar_now(
    world, production
):
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(CalendarConnection).values(showings_calendar_id=None))
    await book(world)
    assert (await one(EVENT)).payload["calendar_id"] == host_event.NO_SHOWINGS_CALENDAR
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(CalendarConnection).values(showings_calendar_id=SHOWINGS_CALENDAR)
        )
    production()

    assert set((await outcomes(world)).values()) == {"sent"}
    assert len(world.calendar.events_in(SHOWINGS_CALENDAR)) == 2


# ---------------------------------------------------------------------------
# Nothing goes by itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switching_modes_never_sends_what_is_already_captured(world, production, monkeypatch):
    lives_here(world)
    await book(world)
    await job(world, at(8))
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{RESIDENT},{JANE_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{JANE}")
    production()
    before = await statuses_by_kind()
    assert set(before.values()) == {"captured"}

    for minute, settings in enumerate(
        [
            {"send_mode": "test"},
            {"send_mode": "live", "live_units": [UNIT]},
            {"send_mode": "live", "live_all_units": True},
            {"send_mode": "capture"},
            {"send_mode": "off", "test_sends_enabled": True},
        ]
    ):
        await store_settings(**settings)
        await job(world, at(9, minute))

    assert await statuses_by_kind() == before
    assert world.texts.sent == [] and world.calendar.log == []


@pytest.mark.asyncio
async def test_productions_row_today_sends_nothing_by_itself(world, production, monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{JANE_MOBILE},{RESIDENT}")
    await store_settings(
        send_mode="capture",
        live_units=[],
        test_sends_enabled=False,
        public_booking_enabled=True,
        lead_agent_uses_open_times=True,
    )
    production()
    lives_here(world)

    booked = await book(world)
    for moment in (at(18, day=TUESDAY), at(8), at(13, 59)):
        await job(world, moment)
    await book(world, robin())
    await cancel(world, booked)
    await job(world, at(18))

    assert world.texts.sent == [] and world.calendar.log == [] and world.emails.sent == []
    assert set((await statuses_by_kind()).values()) <= {"captured", "cancelled"}


# ---------------------------------------------------------------------------
# Alerts, the Messages list, the logs, and removing details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_that_fails_or_sticks_alerts_the_team_once(world, production):
    lives_here(world)
    await book(world)
    production()
    world.calendar.lose(SHOWINGS_CALENDAR)
    world.texts.refuse(RESIDENT, "unavailable", retryable=True)
    await send(world)
    alerter = RecordingAlerter()

    assert await alerts.check(now=NOW + timedelta(minutes=1), alerter=alerter) == 2
    failed = sorted(alert.body.split(" for ")[0] for alert in alerter.alerts)
    assert failed == ["Host's event", "Prospect's invite"]

    later = NOW + timedelta(hours=1)
    assert await alerts.check(now=later, alerter=alerter) == 1
    assert alerter.alerts[-1].body.startswith("Residents' text: new showing")
    assert await alerts.check(now=later, alerter=alerter) == 0


@pytest.mark.asyncio
async def test_the_messages_list_says_who_sent_it(world, production):
    await book(world)
    production()
    await send(world, CONFIRMATION)

    async with world.client(team_app(world)) as client:
        messages = (await client.get(f"{TEAM}/notices")).json()

    by_kind = {notice["kind"]: notice for notice in messages}
    assert by_kind[CONFIRMATION]["send_approved_by"] == APPROVER
    assert datetime.fromisoformat(by_kind[CONFIRMATION]["send_approved_at"]) == NOW
    assert by_kind[INVITE]["send_approved_by"] is None


@pytest.mark.asyncio
async def test_a_send_is_logged_with_the_person_the_ids_and_counts_only(
    world, production, capfire: CaptureLogfire
):
    lives_here(world)
    await book(world)
    production()
    picked = await ids()
    capfire.exporter.clear()

    await send(world, picked=picked)

    spans = capfire.exporter.exported_spans_as_dict()
    (logged,) = [s for s in spans if s["name"].endswith("captured messages sent by hand")]
    attributes = logged["attributes"]
    assert attributes["user_id"] == "user_pat"
    assert attributes["notice_ids"] == str(picked).replace(" ", "")
    counts = {
        name: attributes[name]
        for name in ("sent", "waiting", "unknown", "not_sent", "kept", "handled")
    }
    assert counts == {"sent": 5, "waiting": 0, "unknown": 0, "not_sent": 0, "kept": 0, "handled": 0}
    for private in ("Alex", "Parker", "0117", "0142", "example.com", "serniacapital"):
        assert private not in str(attributes), private


@pytest.mark.asyncio
async def test_details_are_removed_after_12_months_whoever_sent_it(world, production):
    await book(world)
    production()
    await send(world)
    long_ago = datetime.now(at(16).tzinfo) - timedelta(days=370)
    async with AsyncSessionFactory() as session, session.begin():
        showing = await session.scalar(select(Showing))
        # Two that list showings in their payload, which a cleared row no longer has.
        listing = {"text": {"daytime_timezone": "America/New_York", "showing_ids": [showing.id]}}
        await capture(
            session,
            showing.id,
            delivery_checks.HOST_MORNING_KIND,
            key=f"agent:{showing.agent_id}:morning:{long_ago.date()}",
            **listing,
        )
        await capture(
            session,
            showing.id,
            delivery_checks.RESIDENT_REMINDER_KIND,
            key=f"residents:{long_ago.date()}",
            to=RESIDENT,
            **listing,
        )
        await session.execute(
            update(Showing).values(starts_at=long_ago, ends_at=long_ago + timedelta(minutes=30))
        )
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == INVITE)
            .values(status="captured", send_approved_by=None, send_approved_at=None)
        )

    report = await retention.remove_old_details()

    assert (report.showings, report.notices) == (1, 6)
    _, _, notices = await rows()
    for notice in notices:
        assert notice.recipient == retention.REMOVED_RECIPIENT
        assert notice.payload["removed"] is True and "Alex" not in str(notice.payload)
    # The approver is the team's, not the prospect's, and stays with when.
    assert {n.send_approved_by for n in notices if n.status == "sent"} == {APPROVER}
    assert await retention.remove_old_details() == retention.RemovalReport()
    # A cleared row that was captured is far too late, and reads without a problem.
    assert {(n["kind"], n["state"], n["to"]) for n in await listed(world)} == {
        (INVITE, "too_late", "Removed, removed"),
        (delivery_checks.HOST_MORNING_KIND, "too_late", "Jane Doe, removed"),
        (delivery_checks.RESIDENT_REMINDER_KIND, "too_late", "Residents, removed"),
    }
    assert {result["outcome"] for result in (await send(world)).values()} == {"not_sent"}


# ---------------------------------------------------------------------------
# The states and the send agree
# ---------------------------------------------------------------------------


async def capture(session, showing_id: int, kind: str, *, key: str, **fields) -> int:
    """A captured notice of any kind, as the queue would have left it."""
    calendar = fields.pop("calendar", False)
    notice = await outbox.enqueue(
        session,
        kind=kind,
        channel=fields.pop("channel", "calendar_event" if calendar else "sms"),
        showing_id=showing_id,
        recipient=JANE if calendar else fields.pop("to", JANE_MOBILE),
        recipient_key=key,
        payload=CalendarPayload(
            operation="cancel",
            calendar_id=SHOWINGS_CALENDAR,
            summary="Showing",
            starts_at=at(16),
            ends_at=at(16, 30),
            attendees=[JANE],
        )
        if calendar
        else TextPayload(body="Hello", **fields.pop("text", {})),
        now=NOW,
        **fields,
    )
    notice.status, notice.delivered_at = "captured", NOW
    return notice.id


@pytest.mark.asyncio
async def test_every_kind_is_too_late_by_the_one_rule(world, production):
    """Each kind's boundary, in the list and in the send alike."""
    await book(world)
    # Booked as if within 75 minutes of the start: no text 1 hour before, which
    # would stand in for the reminder, is owed.
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(ShowingAttendee).values(created_at=at(15)))
    showings, (alex,), _ = await rows()
    showing = showings[0]
    zone = "America/New_York"
    agent = f"agent:{showing.agent_id}"
    start = at(16)
    kinds: dict[str, tuple[dict, datetime]] = {
        "prospect_reminder": ({"key": f"attendee:{alex.id}", "attendee_id": alex.id}, start),
        "prospect_cancelled": (
            {"key": f"attendee:{alex.id}", "attendee_id": alex.id, "to": ALEX_MOBILE},
            start,
        ),
        "prospect_cancelled_by_team": (
            {"key": f"attendee:{alex.id}", "attendee_id": alex.id, "to": ALEX_MOBILE},
            start,
        ),
        residents.NEW_SHOWING_KIND: ({"key": "residents"}, start),
        delivery_checks.RESIDENT_REMINDER_KIND: (
            {"key": "residents:2027-04-14", "text": {"showing_ids": [showing.id]}},
            start,
        ),
        delivery_checks.HOST_JOIN_KIND: (
            {"key": f"{agent}:attendee:{alex.id}", "attendee_id": alex.id},
            start,
        ),
        delivery_checks.HOST_MOVED_TO_KIND: ({"key": f"{agent}:moved:ab"}, start),
        delivery_checks.HOST_MORNING_KIND: (
            {
                "key": f"{agent}:morning:2027-04-14",
                "text": {"daytime_timezone": zone, "showing_ids": [showing.id]},
            },
            at(12),
        ),
        delivery_checks.APPLY_KIND: (
            {"key": f"attendee:{alex.id}", "attendee_id": alex.id, "to": ALEX_MOBILE},
            start + timedelta(days=2),
        ),
    }
    made: dict[str, int] = {}
    async with AsyncSessionFactory() as session, session.begin():
        for kind, (fields, _) in kinds.items():
            made[kind] = await capture(session, showing.id, kind, **fields)
        made.update(
            {
                notice.kind: notice.id
                for notice in await session.scalars(
                    select(ShowingNotice).where(
                        ShowingNotice.kind.in_([CONFIRMATION, EVENT, INVITE])
                    )
                )
            }
        )
    boundaries = {kind: until for kind, (_, until) in kinds.items()}
    boundaries |= {CONFIRMATION: start, EVENT: start, INVITE: start}
    production()

    for kind, until in boundaries.items():
        for moment, expected in ((until - timedelta(seconds=1), "ready"), (until, "too_late")):
            world.now = moment
            async with AsyncSessionFactory() as session:
                notice = await session.get_one(ShowingNotice, made[kind])
                look = await captured._look(session, notice, UNIT, moment)
            assert look.state == expected, (kind, moment, look)

    # And a send at the boundary sends none of them.
    world.now = max(boundaries.values())
    sent = await send(world, picked=sorted(made.values()))
    assert {result["outcome"] for result in sent.values()} == {"not_sent"}
    assert world.texts.sent == [] and world.calendar.log == []


@pytest.mark.asyncio
async def test_what_the_list_calls_ready_is_what_a_send_sends(world, production):
    """Everything on the list at once: a ready one goes or waits, any other is not sent.
    A ready one due with another text to the same host goes in that text."""
    lives_here(world)
    booked = await book(world)
    await book(world, robin(at(17)))
    await cancel(world, booked)
    await job(world, at(8))
    production()
    found = {notice["id"]: notice for notice in await listed(world)}
    assert {n["state"] for n in found.values()} == {"ready", "no_longer_applies"}

    sent = await send(world, picked=sorted(found))

    merged = {digests.merged_note(other): other for other in sent}
    for notice_id, notice in found.items():
        expected = {"sent", "waiting"} if notice["state"] == "ready" else {"not_sent"}
        went_with = merged.get(sent[notice_id]["detail"])
        if went_with is not None:
            assert notice["state"] == "ready"
            notice_id, notice = went_with, found[went_with]
        assert sent[notice_id]["outcome"] in expected, (notice, sent[notice_id])
    async with AsyncSessionFactory() as session:
        left = await session.scalar(
            select(func.count())
            .select_from(ShowingNotice)
            .where(ShowingNotice.status.in_(["pending", "held"]))
        )
    assert left == sum(1 for result in sent.values() if result["outcome"] == "waiting")
