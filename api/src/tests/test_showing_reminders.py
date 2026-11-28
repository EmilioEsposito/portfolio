"""Reminders at 8 AM on the day, the host's texts on every change, and the team's alerts.

Each scenario books through the public or team routes, cancels through them,
runs the job's steps (``holds.send_pending``) as the clock moves, and checks
the rows in ``showings`` and ``showing_notices``, what the stand-in for Quo
recorded and what the stand-in alert channel was handed. Residents come from
``StandInContacts``. Nothing is sent anywhere; names and numbers are made up.
"""

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import (
    alerts,
    cancelling,
    delivery_checks,
    holds,
    open_times,
    outbox,
    reminders,
    residents,
)
from api.src.showing_bookings.fakes import RecordingAlerter
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingNotice
from api.src.showing_bookings.schema import TextPayload
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
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.tests.test_showing_cancelling import team
from api.src.tests.test_showing_team_actions import add_sam, caller, move, team_book

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
RESIDENT = "+14125550142"
ROOMMATE = "+14125550143"
JANE_MOBILE = "+14125550101"
SAM_MOBILE = "+14125550102"
PROSPECT_REMINDER = delivery_checks.PROSPECT_REMINDER_KIND
RESIDENT_REMINDER = delivery_checks.RESIDENT_REMINDER_KIND
MORNING = at(8)  # 8 AM on the day of Wednesday's showings
EVERY_TEST_PHONE = [ALEX_MOBILE, ROBIN_MOBILE, CASEY_MOBILE, RESIDENT, ROOMMATE]


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES", ",".join([*EVERY_TEST_PHONE, JANE_MOBILE, SAM_MOBILE])
    )
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE}")
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()
    await clear_rental_data()
    state = PublicWorld()
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)

    def no_live_alerts():
        raise AssertionError("The live alert channel was reached.")

    monkeypatch.setattr(outbox, "_live_alerter", no_live_alerts)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()
    await clear_rental_data()


async def live() -> None:
    await store_settings(send_mode="test")


def lives_here(world: PublicWorld, mobile: str = RESIDENT, name: str = "Morgan Ellis"):
    return world.residents.add(name, mobile, unit=UNIT)


def short_notice(world: PublicWorld, hours: int = 1) -> None:
    world.rules = ShowingRules(showing_notice_hours=hours)


async def book(world: PublicWorld, body: dict | None = None) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=body or booking())
    assert answer.status_code == 201, answer.text
    return answer.json()


def robin(start=None) -> dict:
    return booking(start, name="Robin Lee", mobile=ROBIN_MOBILE, email=ROBIN_EMAIL)


def casey(start=None) -> dict:
    return booking(start, name="Casey Morgan", mobile=CASEY_MOBILE, email=CASEY_EMAIL)


async def cancel(world: PublicWorld, token: str) -> None:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token})
    assert answer.status_code == 200, answer.text


async def run(world: PublicWorld, now, alerter: RecordingAlerter | None = None):
    world.now = now
    return await holds.send_pending(
        now=now, texts=world.texts, calendar=world.calendar, alerter=alerter
    )


async def of_kind(kind: str) -> list[ShowingNotice]:
    _, _, notices = await rows()
    return [notice for notice in notices if notice.kind == kind]


def reminders_to(world: PublicWorld, number: str) -> list[str]:
    return [body for body in world.texts.to(number) if "reminder" in body]


def residents_reminders(world: PublicWorld, number: str = RESIDENT) -> list[str]:
    return [body for body in world.texts.to(number) if "Reminder:" in body]


def cancel_link(token: str) -> str:
    return f"https://serniacapital.com/showings/cancel/{token}"


ALEX_REMINDER = (
    "Sernia Capital: a reminder of your in-person showing today at 4:00 PM at "
    "320 Example St (unit 320-03).\nTo cancel: {link}\nReply STOP to opt out."
)


# ---------------------------------------------------------------------------
# The prospect's reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reminder_goes_at_8_am_on_the_day_and_never_twice(world):
    await live()
    alex = await book(world)
    await run(world, NOW + timedelta(minutes=1))

    report = await run(world, MORNING - timedelta(minutes=1))
    assert report.prospect_reminders == 0
    assert await of_kind(PROSPECT_REMINDER) == []

    report = await run(world, MORNING)
    assert report.prospect_reminders == 1
    assert reminders_to(world, ALEX_MOBILE) == [
        ALEX_REMINDER.format(link=cancel_link(alex["cancel_token"]))
    ]
    (notice,) = await of_kind(PROSPECT_REMINDER)
    assert (notice.status, notice.recipient_key, notice.guard_reason) == (
        "sent",
        f"attendee:{notice.attendee_id}",
        "test_recipient",
    )

    for minutes in (1, 2, 60, 600):
        report = await run(world, MORNING + timedelta(minutes=minutes))
        assert report.prospect_reminders == 0
    assert len(reminders_to(world, ALEX_MOBILE)) == 1
    assert len(await of_kind(PROSPECT_REMINDER)) == 1


@pytest.mark.asyncio
async def test_no_reminder_is_queued_the_evening_before_it_waits_for_8_am(world):
    await live()
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    for moment in (at(18, day=TUESDAY), at(23, day=TUESDAY), at(7, 59)):
        await run(world, moment)
    assert await of_kind(PROSPECT_REMINDER) == []
    assert reminders_to(world, ALEX_MOBILE) == []

    await run(world, at(8))
    (notice,) = await of_kind(PROSPECT_REMINDER)
    assert notice.status == "sent"
    assert len(reminders_to(world, ALEX_MOBILE)) == 1


@pytest.mark.asyncio
async def test_after_a_missed_8_am_the_reminder_still_goes_before_the_start(world):
    await live()
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    await run(world, at(9))
    assert len(reminders_to(world, ALEX_MOBILE)) == 1

    await run(world, at(16, 5))  # after the start: nothing new
    assert len(reminders_to(world, ALEX_MOBILE)) == 1


@pytest.mark.asyncio
async def test_no_reminder_once_the_booking_is_cancelled(world):
    await live()
    alex = await book(world)
    await run(world, NOW + timedelta(minutes=1))
    world.now = at(17, day=TUESDAY)
    await cancel(world, alex["cancel_token"])

    await run(world, MORNING)
    assert await of_kind(PROSPECT_REMINDER) == []
    assert reminders_to(world, ALEX_MOBILE) == []


@pytest.mark.asyncio
async def test_a_reminder_queued_then_cancelled_before_it_goes_is_never_sent(world):
    await live()
    alex = await book(world)
    await run(world, NOW + timedelta(minutes=1))
    await reminders.queue_due(now=at(8))  # queued, not delivered yet
    world.now = at(8)
    await cancel(world, alex["cancel_token"])

    await run(world, at(8, 1))
    (notice,) = await of_kind(PROSPECT_REMINDER)
    assert (notice.status, notice.last_error) == ("cancelled", cancelling.STOPPED_NOTE)
    assert reminders_to(world, ALEX_MOBILE) == []


@pytest.mark.asyncio
async def test_no_reminder_for_a_withdrawn_booking(world):
    await live()
    world.texts.refuse(ALEX_MOBILE)
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking())
    assert answer.status_code == 422

    await run(world, MORNING)
    assert await of_kind(PROSPECT_REMINDER) == []


@pytest.mark.asyncio
async def test_no_reminder_for_a_booking_made_at_8_am_on_the_day(world):
    await live()
    lives_here(world)
    short_notice(world)
    world.now = at(8)
    await book(world)
    await run(world, world.now + timedelta(minutes=1))

    for moment in (at(9), at(15)):
        report = await run(world, moment)
        assert (report.prospect_reminders, report.resident_reminders) == (0, 0)
    assert reminders_to(world, ALEX_MOBILE) == []
    assert await of_kind(RESIDENT_REMINDER) == []


@pytest.mark.asyncio
async def test_a_booking_made_at_7_59_on_the_day_gets_the_reminder_at_8(world):
    await live()
    short_notice(world)
    world.now = at(7, 59)
    await book(world)
    await run(world, world.now)

    report = await run(world, at(8))
    assert report.prospect_reminders == 1
    assert len(reminders_to(world, ALEX_MOBILE)) == 1


@pytest.mark.asyncio
async def test_no_reminder_for_a_caller_who_declined_texts(world):
    await live()
    answer = await team_book(world, caller(mobile="412-555-0119", email=CASEY_EMAIL))
    assert answer.status_code == 201, answer.text

    await run(world, MORNING)
    assert await of_kind(PROSPECT_REMINDER) == []
    assert world.texts.to(CASEY_MOBILE) == []


@pytest.mark.asyncio
async def test_a_caller_who_takes_texts_gets_the_reminder(world):
    await live()
    answer = await team_book(world, caller(mobile="412-555-0119", email=CASEY_EMAIL, texts=True))
    assert answer.status_code == 201, answer.text
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    assert len(reminders_to(world, CASEY_MOBILE)) == 1


@pytest.mark.asyncio
async def test_no_reminder_to_a_number_reported_undelivered(world):
    await live()
    world.texts.report(ALEX_MOBILE, "undelivered")
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    assert await of_kind(PROSPECT_REMINDER) == []


@pytest.mark.asyncio
async def test_capture_mode_keeps_the_reminders_and_sends_nothing(world):
    lives_here(world)
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    (prospect,) = await of_kind(PROSPECT_REMINDER)
    (resident,) = await of_kind(RESIDENT_REMINDER)
    assert (prospect.status, resident.status) == ("captured", "captured")
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_live_mode_sends_only_to_test_recipients(world, monkeypatch):
    await live()
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{RESIDENT}")
    await book(world)
    await book(world, robin(at(17)))
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    sent = {n.recipient: n.status for n in await of_kind(PROSPECT_REMINDER)}
    assert sent == {ALEX_MOBILE: "sent", ROBIN_MOBILE: "captured"}
    assert reminders_to(world, ROBIN_MOBILE) == []


# ---------------------------------------------------------------------------
# The residents' reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_residents_get_one_reminder_for_one_showing(world):
    await live()
    lives_here(world)
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING - timedelta(minutes=1))
    assert await of_kind(RESIDENT_REMINDER) == []
    report = await run(world, MORNING)
    assert report.resident_reminders == 1

    assert residents_reminders(world) == [
        "Automated showing alert: Reminder: an in-person showing of your unit is scheduled "
        "today at 4:00 PM. A Sernia agent will be there the whole time. You may be present "
        "or we can let ourselves in if you are not home."
    ]
    (notice,) = await of_kind(RESIDENT_REMINDER)
    assert notice.recipient_key == "residents:2027-04-14"
    for minutes in (1, 30, 600):
        await run(world, MORNING + timedelta(minutes=minutes))
    assert len(residents_reminders(world)) == 1


@pytest.mark.asyncio
async def test_three_showings_at_one_unit_are_one_text_in_order(world):
    await live()
    short_notice(world, 12)
    lives_here(world)
    lives_here(world, ROOMMATE, "Riley Chen")
    await book(world, casey(at(16)))
    await book(world, robin(at(13)))
    await book(world, booking(at(10)))
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    (text,) = [t for t in world.texts.groups() if "Reminder:" in t.body]
    assert set(text.numbers) == {RESIDENT, ROOMMATE}
    assert text.body == (
        "Automated showing alert: Reminder: in-person showings of your unit are scheduled "
        "today at 10:00 AM, 1:00 PM and 4:00 PM. A Sernia agent will be there the whole "
        "time. You may be present or we can let ourselves in if you are not home."
    )
    (notice,) = await of_kind(RESIDENT_REMINDER)
    showings, _, _ = await rows()
    first = min(showings, key=lambda showing: showing.starts_at)
    assert notice.showing_id == first.id
    assert len(notice.payload["showing_ids"]) == 3


@pytest.mark.asyncio
async def test_a_vacant_unit_gets_no_residents_reminder(world):
    await live()
    await book(world)
    await run(world, NOW + timedelta(minutes=1))

    report = await run(world, MORNING)
    assert (report.prospect_reminders, report.resident_reminders) == (1, 0)
    assert await of_kind(RESIDENT_REMINDER) == []
    reads = len(world.residents.reads)
    await run(world, MORNING + timedelta(minutes=1))
    assert len(world.residents.reads) == reads  # not looked up every minute


@pytest.mark.asyncio
async def test_a_lookup_problem_marks_the_showings_and_sends_nothing(world):
    await live()
    short_notice(world, 12)
    lives_here(world)
    await book(world)
    await book(world, robin(at(13)))
    await run(world, NOW + timedelta(minutes=1))
    world.residents.fail()

    await run(world, MORNING)
    assert await of_kind(RESIDENT_REMINDER) == []
    showings, _, _ = await rows()
    assert {showing.resident_attention for showing in showings} == {"lookup_failed"}
    # The prospects still get theirs.
    assert len(await of_kind(PROSPECT_REMINDER)) == 2


@pytest.mark.asyncio
async def test_a_showing_cancelled_after_the_reminder_was_queued_is_left_out(world):
    """The cancellation, due at the same moment, goes in the same text."""
    await live()
    short_notice(world, 12)
    lives_here(world)
    alex = await book(world, booking(at(10)))
    await book(world, robin(at(16)))
    await run(world, NOW + timedelta(minutes=1))
    await reminders.queue_due(now=at(8))  # queued, not delivered yet
    (notice,) = await of_kind(RESIDENT_REMINDER)
    assert notice.status == "pending"

    world.now = at(8)
    await cancel(world, alex["cancel_token"])  # the first showing, which the notice belongs to
    await run(world, at(8, 1))

    (notice,) = await of_kind(RESIDENT_REMINDER)
    assert notice.status == "sent"
    assert residents_reminders(world) == [
        "Automated showing alert: The in-person showing of your unit on Wed, Apr 14 at "
        "10:00 AM is cancelled. Reminder: an in-person showing of your unit is scheduled "
        "today at 4:00 PM. A Sernia agent will be there the whole time. You may be present "
        "or we can let ourselves in if you are not home."
    ]
    (cancellation,) = await of_kind(residents.CANCELLED_KIND)
    assert (cancellation.status, cancellation.last_error) == (
        "cancelled",
        f"Not sent by itself: it went in the same text as notice {notice.id}.",
    )


@pytest.mark.asyncio
async def test_every_listed_showing_cancelled_means_no_residents_reminder(world):
    await live()
    lives_here(world)
    alex = await book(world)
    await run(world, NOW + timedelta(minutes=1))
    await reminders.queue_due(now=at(8))
    world.now = at(8)
    await cancel(world, alex["cancel_token"])

    await run(world, at(8, 1))
    (notice,) = await of_kind(RESIDENT_REMINDER)
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.NOTHING_LEFT)
    assert residents_reminders(world) == []
    # The cancellation goes by itself.
    assert world.texts.to(RESIDENT)[-1] == (
        "Automated showing alert: The in-person showing of your unit on Wed, Apr 14 at "
        "4:00 PM is cancelled."
    )


@pytest.mark.asyncio
async def test_a_showing_booked_after_the_reminder_is_covered_by_its_announcement(world):
    await live()
    lives_here(world)
    short_notice(world)
    await book(world, booking(at(10)))
    await run(world, NOW + timedelta(minutes=1))
    await run(world, MORNING)
    assert len(residents_reminders(world)) == 1

    world.now = at(9)
    await book(world, robin(at(16)))
    await run(world, at(9, 1))
    await run(world, at(9, 10))

    assert len(await of_kind(RESIDENT_REMINDER)) == 1
    assert len(residents_reminders(world)) == 1
    announcements = [b for b in world.texts.to(RESIDENT) if "We've scheduled" in b]
    assert len(announcements) == 2
    # The new one lists the showing the residents were already told of.
    assert announcements[-1] == (
        "Automated showing alert: We've scheduled another in-person showing of your unit. "
        "Upcoming showings:\n"
        "- Wed, Apr 14 at 10:00 AM\n"
        "- Wed, Apr 14 at 4:00 PM (new)\n"
        "A Sernia agent will be there the whole time. You may be present or we can let "
        "ourselves in if you are not home."
    )


@pytest.mark.asyncio
async def test_reminders_for_two_days_are_separate(world):
    await live()
    lives_here(world)
    await book(world)
    await book(world, robin(at(16, day=WEDNESDAY + timedelta(days=1))))
    await run(world, NOW + timedelta(minutes=1))

    await run(world, MORNING)
    await run(world, at(8, day=WEDNESDAY + timedelta(days=1)))
    keys = sorted(n.recipient_key for n in await of_kind(RESIDENT_REMINDER))
    assert keys == ["residents:2027-04-14", "residents:2027-04-15"]
    assert len(await of_kind(PROSPECT_REMINDER)) == 2


# ---------------------------------------------------------------------------
# The host's texts
# ---------------------------------------------------------------------------


def host_texts(world: PublicWorld, number: str = JANE_MOBILE) -> list[str]:
    return world.texts.to(number)


def showing_at(clock: str, people: int) -> str:
    """One line of a host's upcoming showings, at the unit on Wednesday."""
    return (
        f"- Wed, Apr 14 at {clock} at 320 Example St (unit 320-03), "
        f"{people} {'person' if people == 1 else 'people'}"
    )


def to_host(*changes: str, upcoming: tuple[str, ...] = ()) -> str:
    """A host's text as it goes: what changed, then their upcoming showings."""
    head = "\n".join([f"Sernia Capital: {changes[0]}", *changes[1:]])
    if not upcoming:
        return f"{head}\n\nYou have no upcoming showings."
    return "\n".join([head, "", "Your upcoming showings:", *upcoming])


FOUR_PM = "320 Example St (unit 320-03) on Wed, Apr 14 at 4:00 PM"
TEN_AM = "320 Example St (unit 320-03) on Wed, Apr 14 at 10:00 AM"
ALEX_BOOKED = to_host(
    f"Alex Parker booked your showing of {FOUR_PM}.", upcoming=(showing_at("4:00 PM", 1),)
)
ROBIN_JOINED = to_host(
    f"Robin Lee booked your showing of {FOUR_PM}.", upcoming=(showing_at("4:00 PM", 2),)
)


@pytest.mark.asyncio
async def test_a_booking_and_a_join_each_text_the_host_at_once(world):
    await live()
    short_notice(world)
    world.now = at(11)
    await book(world)  # 5 hours ahead
    assert host_texts(world) == [ALEX_BOOKED]

    world.now = at(14, 30)
    await book(world, robin())
    assert host_texts(world) == [ALEX_BOOKED, ROBIN_JOINED]
    notices = await of_kind(delivery_checks.HOST_JOIN_KIND)
    assert [(n.status, n.recipient) for n in notices] == [("sent", JANE_MOBILE)] * 2


@pytest.mark.asyncio
async def test_a_booking_after_9_pm_texts_the_host_at_8_am_with_the_days_showings(world):
    await live()
    short_notice(world)
    world.now = at(20, 59, day=TUESDAY)
    await book(world)
    assert host_texts(world) == [ALEX_BOOKED]  # before 9 PM: at once

    world.now = at(21, day=TUESDAY)
    await book(world, robin())
    (_, waiting) = await of_kind(delivery_checks.HOST_JOIN_KIND)
    assert (waiting.status, waiting.not_before) == ("pending", at(8))
    await run(world, at(7, 59))
    assert host_texts(world) == [ALEX_BOOKED]

    await run(world, at(8))
    assert host_texts(world) == [
        ALEX_BOOKED,
        f"Sernia Capital: Robin Lee booked your showing of {FOUR_PM}.\n\n"
        "Your showings today, Wed, Apr 14:\n"
        "- 4:00 PM at 320 Example St (unit 320-03), 2 people",
    ]
    (morning,) = await of_kind(delivery_checks.HOST_MORNING_KIND)
    assert (morning.status, morning.last_error) == (
        "cancelled",
        f"Not sent by itself: it went in the same text as notice {waiting.id}.",
    )


@pytest.mark.asyncio
async def test_a_team_booking_texts_the_host(world):
    await live()
    short_notice(world)
    world.now = at(11)
    answer = await team_book(world, caller())
    assert answer.status_code == 201, answer.text
    assert host_texts(world) == [
        to_host(
            f"Casey Morgan booked your showing of {FOUR_PM}.",
            upcoming=(showing_at("4:00 PM", 1),),
        )
    ]
    assert delivery_checks.HOST_JOIN_KIND in {n["kind"] for n in answer.json()["notices"]}


@pytest.mark.asyncio
async def test_a_withdrawn_late_booking_never_texts_the_host(world):
    await live()
    short_notice(world)
    world.now = at(15)
    world.texts.refuse(ALEX_MOBILE)
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking())
    assert answer.status_code == 422
    await run(world, at(15, 1))
    assert host_texts(world) == []
    (notice,) = await of_kind(delivery_checks.HOST_JOIN_KIND)
    assert notice.status == "cancelled"


@pytest.mark.asyncio
async def test_a_prospect_cancelling_texts_the_host(world):
    await live()
    alex = await book(world)
    robin_booking = await book(world, robin())
    await run(world, NOW + timedelta(minutes=10))
    assert host_texts(world) == [ALEX_BOOKED, ROBIN_JOINED]

    world.now = at(11)  # 5 hours ahead
    await cancel(world, alex["cancel_token"])
    world.now = at(14, 30)
    await cancel(world, robin_booking["cancel_token"])
    assert host_texts(world)[2:] == [
        to_host(
            f"Alex Parker cancelled your showing of {FOUR_PM}.",
            upcoming=(showing_at("4:00 PM", 1),),
        ),
        to_host(
            f"Robin Lee cancelled your showing of {FOUR_PM}. No one is booked now, so the "
            "showing is cancelled."
        ),
    ]


@pytest.mark.asyncio
async def test_a_prospect_cancelling_with_others_booked_shows_how_many_are_left(world):
    await live()
    alex = await book(world)
    await book(world, robin())
    await run(world, NOW + timedelta(minutes=10))
    world.now = at(15)
    await cancel(world, alex["cancel_token"])
    assert host_texts(world) == [
        ALEX_BOOKED,
        ROBIN_JOINED,
        to_host(
            f"Alex Parker cancelled your showing of {FOUR_PM}.",
            upcoming=(showing_at("4:00 PM", 1),),
        ),
    ]


@pytest.mark.asyncio
async def test_the_team_removing_someone_texts_the_host(world):
    await live()
    await book(world)
    await book(world, robin())
    await run(world, NOW + timedelta(minutes=10))
    showings, attendees, _ = await rows()
    world.now = at(11)
    answer = await team(world, f"/showings/{showings[0].id}/attendees/{attendees[0].id}/remove")
    assert answer.status_code == 200, answer.text
    assert answer.json()["texts_queued"] == 1  # the prospect's; the host's is not counted
    assert host_texts(world)[2:] == [
        to_host(
            f"The team removed Alex Parker from your showing of {FOUR_PM}.",
            upcoming=(showing_at("4:00 PM", 1),),
        )
    ]


@pytest.mark.asyncio
async def test_the_team_cancelling_a_showing_texts_the_host_at_once_or_at_8_am(world):
    await live()
    short_notice(world, 12)
    await book(world)
    await book(world, robin(at(10)))
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()
    four, ten = sorted(showings, key=lambda s: s.starts_at, reverse=True)
    assert len(host_texts(world)) == 2  # each booking

    world.now = at(7, 59)
    answer = await team(world, f"/showings/{ten.id}/cancel")  # before 8 AM: waits for 8
    assert answer.status_code == 200
    (waiting,) = await of_kind(delivery_checks.HOST_SHOWING_CANCELLED_KIND)
    assert (waiting.status, waiting.not_before) == ("pending", at(8))
    assert len(host_texts(world)) == 2

    await run(world, at(8))  # with the morning text, in one
    assert host_texts(world)[2:] == [
        f"Sernia Capital: Your showing of {TEN_AM} is cancelled. The team cancelled it.\n\n"
        "Your showings today, Wed, Apr 14:\n"
        "- 4:00 PM at 320 Example St (unit 320-03), 1 person"
    ]

    world.now = at(14)
    answer = await team(world, f"/showings/{four.id}/cancel")
    assert answer.status_code == 200
    assert answer.json()["texts_queued"] == 1
    assert host_texts(world)[3:] == [
        to_host(f"Your showing of {FOUR_PM} is cancelled. The team cancelled it.")
    ]


@pytest.mark.asyncio
async def test_an_agent_without_a_mobile_gets_no_text(world):
    await live()
    async with AsyncSessionFactory() as session:
        await session.execute(update(ShowingAgentRecord).values(mobile=None))
        await session.commit()
    alex = await book(world)
    await book(world, robin())
    await run(world, NOW + timedelta(minutes=10))
    world.now = at(15)
    await cancel(world, alex["cancel_token"])
    assert await of_kind(delivery_checks.HOST_LEFT_KIND) == []
    assert host_texts(world) == []


@pytest.mark.asyncio
async def test_a_change_of_host_texts_both_hosts(world):
    await live()
    short_notice(world, 12)
    sam = await add_sam()
    await book(world)
    await book(world, robin(at(10)))
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()
    four, ten = sorted(showings, key=lambda s: s.starts_at, reverse=True)
    assert len(host_texts(world)) == 2  # each booking

    world.now = at(7)
    assert (await move(world, ten.id, sam)).status_code == 200  # before 8 AM: both wait
    assert len(host_texts(world)) == 2 and host_texts(world, SAM_MOBILE) == []

    await run(world, at(8))  # each with their morning text
    assert host_texts(world, SAM_MOBILE) == [
        f"Sernia Capital: The showing of {TEN_AM} is now yours to host.\n\n"
        "Your showings today, Wed, Apr 14:\n"
        "- 10:00 AM at 320 Example St (unit 320-03), 1 person"
    ]
    assert host_texts(world)[2:] == [
        f"Sernia Capital: The showing of {TEN_AM} has moved to another host. You are no "
        "longer hosting it.\n\n"
        "Your showings today, Wed, Apr 14:\n"
        "- 4:00 PM at 320 Example St (unit 320-03), 1 person"
    ]

    world.now = at(15)
    assert (await move(world, four.id, sam)).status_code == 200  # at once
    assert host_texts(world, SAM_MOBILE)[1:] == [
        to_host(
            f"The showing of {FOUR_PM} is now yours to host.",
            upcoming=(showing_at("4:00 PM", 1),),
        )
    ]
    assert host_texts(world)[3:] == [
        to_host(
            f"The showing of {FOUR_PM} has moved to another host. You are no longer hosting it."
        )
    ]


MOVED_AWAY = to_host(
    f"The showing of {FOUR_PM} has moved to another host. You are no longer hosting it."
)
NOW_YOURS = to_host(
    f"The showing of {FOUR_PM} is now yours to host.", upcoming=(showing_at("4:00 PM", 1),)
)
LEE_MOBILE = "+14125550103"


async def add_lee() -> int:
    async with AsyncSessionFactory() as session:
        agent = ShowingAgentRecord(
            name="Lee Park",
            email="lee.park@example.com",
            mobile=LEE_MOBILE,
            busy_calendar_ids=["lee.park@example.com"],
            priority=3,
        )
        session.add(agent)
        await session.commit()
        return agent.id


async def moved_texts() -> list[tuple[str, str, str, str | None]]:
    _, _, notices = await rows()
    return [
        (n.kind, n.recipient, n.status, n.last_error)
        for n in notices
        if n.kind in delivery_checks.HOST_MOVED_KINDS
    ]


@pytest.mark.asyncio
async def test_a_move_back_stops_the_old_hosts_waiting_no_longer_yours_text(world):
    """A to B to A while A's first "no longer yours" waits for a retry: it never goes."""
    await live()
    sam = await add_sam()
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()
    jane = showings[0].agent_id

    world.now = at(15)
    world.texts.refuse(JANE_MOBILE, "unavailable", retryable=True)  # Quo out of reach
    assert (await move(world, showings[0].id, sam)).status_code == 200
    assert host_texts(world) == [ALEX_BOOKED]
    assert host_texts(world, SAM_MOBILE) == [NOW_YOURS]

    world.texts.accept(JANE_MOBILE)
    world.now = at(15, 1)
    assert (await move(world, showings[0].id, jane)).status_code == 200
    await run(world, at(15, 10))

    assert host_texts(world) == [ALEX_BOOKED, NOW_YOURS]  # Jane hosts it, and was told so last
    assert host_texts(world, SAM_MOBILE) == [NOW_YOURS, MOVED_AWAY]
    assert sorted(await moved_texts()) == sorted(
        [
            (delivery_checks.HOST_MOVED_TO_KIND, SAM_MOBILE, "sent", None),
            (
                delivery_checks.HOST_MOVED_FROM_KIND,
                JANE_MOBILE,
                "cancelled",
                delivery_checks.HOSTING_AGAIN,
            ),
            (delivery_checks.HOST_MOVED_TO_KIND, JANE_MOBILE, "sent", None),
            (delivery_checks.HOST_MOVED_FROM_KIND, SAM_MOBILE, "sent", None),
        ]
    )


@pytest.mark.asyncio
async def test_a_move_back_after_the_texts_went_leaves_each_host_told_the_truth_last(world):
    """A to B to A with the first texts sent: each host's last text is true."""
    await live()
    sam = await add_sam()
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()
    jane = showings[0].agent_id
    world.now = at(15)
    assert (await move(world, showings[0].id, sam)).status_code == 200
    world.now = at(15, 1)
    assert (await move(world, showings[0].id, jane)).status_code == 200

    assert host_texts(world) == [ALEX_BOOKED, MOVED_AWAY, NOW_YOURS]
    assert host_texts(world, SAM_MOBILE) == [NOW_YOURS, MOVED_AWAY]


@pytest.mark.asyncio
async def test_two_quick_moves_never_tell_a_host_something_untrue(world, monkeypatch):
    """A to B to C while B's "now yours" waits for a retry: B never hears it is theirs."""
    await live()
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES",
        ",".join([*EVERY_TEST_PHONE, JANE_MOBILE, SAM_MOBILE, LEE_MOBILE]),
    )
    sam = await add_sam()
    lee = await add_lee()
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()

    world.now = at(15)
    world.texts.refuse(SAM_MOBILE, "unavailable", retryable=True)
    assert (await move(world, showings[0].id, sam)).status_code == 200
    world.texts.accept(SAM_MOBILE)
    world.now = at(15, 1)
    assert (await move(world, showings[0].id, lee)).status_code == 200
    await run(world, at(15, 10))

    assert host_texts(world) == [ALEX_BOOKED, MOVED_AWAY]
    # Sam's event and text never went: nothing told Sam of it, so nothing tells Sam it moved.
    assert host_texts(world, SAM_MOBILE) == []
    assert host_texts(world, LEE_MOBILE) == [NOW_YOURS]
    stopped = [m for m in await moved_texts() if m[2] == "cancelled"]
    assert stopped == [
        (
            delivery_checks.HOST_MOVED_TO_KIND,
            SAM_MOBILE,
            "cancelled",
            delivery_checks.MOVED_AGAIN,
        )
    ]


@pytest.mark.asyncio
async def test_after_a_change_of_host_a_cancellation_texts_the_new_host(world):
    await live()
    sam = await add_sam()
    alex = await book(world)
    await book(world, robin())
    await run(world, NOW + timedelta(minutes=10))
    showings, _, _ = await rows()
    assert (await move(world, showings[0].id, sam)).status_code == 200
    jane_before = host_texts(world)
    assert jane_before[-1] == to_host(
        f"The showing of {FOUR_PM} has moved to another host. You are no longer hosting it."
    )
    world.now = at(15)
    await cancel(world, alex["cancel_token"])
    assert host_texts(world, SAM_MOBILE) == [
        to_host(
            f"The showing of {FOUR_PM} is now yours to host.",
            upcoming=(showing_at("4:00 PM", 2),),
        ),
        to_host(
            f"Alex Parker cancelled your showing of {FOUR_PM}.",
            upcoming=(showing_at("4:00 PM", 1),),
        ),
    ]
    assert host_texts(world) == jane_before


@pytest.mark.asyncio
async def test_an_off_market_date_queues_the_host_text_for_the_job(world):
    await live()
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    world.now = at(11)
    async with AsyncSessionFactory() as session, session.begin():
        await cancelling.cancel_off_market(session, UNIT, WEDNESDAY, now=world.now)
    (notice,) = await of_kind(delivery_checks.HOST_SHOWING_CANCELLED_KIND)
    assert notice.status == "pending"
    await run(world, at(11, 1))
    assert host_texts(world) == [
        ALEX_BOOKED,
        to_host(f"Your showing of {FOUR_PM} is cancelled. The unit went off the market."),
    ]


# ---------------------------------------------------------------------------
# Alerts to the team
# ---------------------------------------------------------------------------


async def notices_with_alerts() -> list[ShowingNotice]:
    _, _, notices = await rows()
    return [notice for notice in notices if notice.alerted_at is not None]


@pytest.mark.asyncio
async def test_a_failed_send_alerts_the_team_once(world):
    await live()
    alerter = RecordingAlerter()
    await book(world)
    await run(world, NOW + timedelta(minutes=10), alerter)
    world.texts.refuse(ALEX_MOBILE, "opted_out")

    report = await run(world, MORNING, alerter)
    assert report.alerts == 1
    (alert,) = alerter.alerts
    assert alert.title == "Showing message failed"
    assert alert.body == (
        "Prospect's reminder: 8 AM on the day for unit 320-03, showing Wed, Apr 14 at "
        "4:00 PM. It failed and will not be tried again. Check it under Messages on the "
        "Booking settings tab."
    )
    assert alert.url == "/team/leads/bookings"
    for number in (ALEX_MOBILE, JANE_MOBILE, "555"):
        assert number not in alert.body + alert.title
    (notice,) = await notices_with_alerts()
    assert (notice.kind, notice.alert_reason, notice.alerted_at) == (
        PROSPECT_REMINDER,
        "failed",
        MORNING,
    )

    for minutes in (1, 10, 60):
        assert (await run(world, MORNING + timedelta(minutes=minutes), alerter)).alerts == 0
    assert len(alerter.alerts) == 1


@pytest.mark.asyncio
async def test_a_send_that_may_have_gone_out_alerts_the_team(world):
    await live()
    alerter = RecordingAlerter()
    await book(world)
    await run(world, NOW + timedelta(minutes=10), alerter)
    world.texts.lose_answer(ALEX_MOBILE)

    await run(world, MORNING, alerter)
    (alert,) = alerter.alerts
    assert alert.title == "Showing message failed"
    (notice,) = await notices_with_alerts()
    assert notice.status == "unknown"


@pytest.mark.asyncio
async def test_a_notice_not_out_ten_minutes_after_it_was_due_is_stuck(world):
    alerter = RecordingAlerter()
    await book(world)
    _, _, notices = await rows()
    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind="prospect_reminder",
            channel="sms",
            showing_id=notices[0].showing_id,
            attendee_id=notices[0].attendee_id,
            recipient=ALEX_MOBILE,
            recipient_key="attendee:stuck",
            payload=TextPayload(body="A reminder."),
            now=NOW,
        )
        await session.commit()
        stuck_id = notice.id

    assert await alerts.check(now=NOW + timedelta(minutes=9, seconds=59), alerter=alerter) == 0
    assert await alerts.check(now=NOW + timedelta(minutes=10), alerter=alerter) == 1
    assert await alerts.check(now=NOW + timedelta(minutes=11), alerter=alerter) == 0
    (alert,) = alerter.alerts
    assert alert.title == "Showing message stuck"
    assert "has waited more than 10 minutes" in alert.body
    async with AsyncSessionFactory() as session:
        stuck = await session.get(ShowingNotice, stuck_id)
    assert stuck.alert_reason == "stuck"


@pytest.mark.asyncio
async def test_a_held_notice_unanswered_15_minutes_after_its_confirmation_is_stuck(world):
    await live()
    alerter = RecordingAlerter()
    await book(world)  # the confirmation is sent at NOW; the event and invite wait for it
    _, _, notices = await rows()
    held = [n for n in notices if n.status == "held"]
    assert len(held) == 2

    assert await alerts.check(now=NOW + timedelta(minutes=14), alerter=alerter) == 0
    assert await alerts.check(now=NOW + timedelta(minutes=15), alerter=alerter) == 2
    assert await alerts.check(now=NOW + timedelta(minutes=16), alerter=alerter) == 0
    assert {a.title for a in alerter.alerts} == {"Showing message stuck"}
    assert {n.id for n in await notices_with_alerts()} == {n.id for n in held}


@pytest.mark.asyncio
async def test_released_on_time_nothing_is_alerted(world):
    await live()
    alerter = RecordingAlerter()
    world.texts.report(ALEX_MOBILE, "sent")  # no final report
    await book(world)
    await run(world, NOW + timedelta(minutes=5), alerter)
    await run(world, NOW + timedelta(minutes=30), alerter)
    assert alerter.alerts == []


@pytest.mark.asyncio
async def test_captured_notices_are_never_alerted_about(world, monkeypatch):
    alerter = RecordingAlerter()
    lives_here(world)
    await book(world)
    # The announcement is captured by itself, not in the same text as the reminder.
    await run(world, NOW + timedelta(minutes=1), alerter)
    await run(world, MORNING, alerter)
    await run(world, MORNING + timedelta(hours=2), alerter)
    _, _, notices = await rows()
    assert {n.status for n in notices} == {"captured"}

    await store_settings(send_mode="test")
    monkeypatch.setenv("SHOWING_TEST_PHONES", "")
    await book(world, robin(at(17, day=WEDNESDAY + timedelta(days=1))))
    await run(world, MORNING + timedelta(hours=3), alerter)
    assert alerter.alerts == []
    assert await notices_with_alerts() == []


@pytest.mark.asyncio
async def test_a_refused_confirmation_the_person_was_told_of_is_not_alerted(world):
    await live()
    alerter = RecordingAlerter()
    world.texts.refuse(ALEX_MOBILE)
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking())
    assert answer.status_code == 422
    await run(world, NOW + timedelta(minutes=1), alerter)
    assert alerter.alerts == []


@pytest.mark.asyncio
async def test_an_alert_that_fails_is_kept_and_never_repeated(world):
    await live()
    alerter = RecordingAlerter()
    alerter.failing = True
    await book(world)
    await run(world, NOW + timedelta(minutes=10), alerter)
    world.texts.refuse(ALEX_MOBILE, "opted_out")
    await run(world, MORNING, alerter)
    alerter.failing = False
    await run(world, MORNING + timedelta(minutes=1), alerter)
    assert alerter.alerts == []
    assert len(await notices_with_alerts()) == 1


@pytest.mark.asyncio
async def test_outside_production_an_alert_is_kept_and_never_pushed(world):
    await live()
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    world.texts.refuse(ALEX_MOBILE, "opted_out")
    # No stand-in: the fixture fails the test if the live channel is reached.
    report = await run(world, MORNING)
    assert report.alerts == 1
    assert len(await notices_with_alerts()) == 1


@pytest.mark.asyncio
async def test_in_production_the_alert_goes_to_the_live_channel(monkeypatch):
    pushed = RecordingAlerter()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setattr(outbox, "_live_alerter", lambda: pushed)
    assert await outbox.send_team_alert("Title", "Body", url="/x") is True
    assert [(a.title, a.body, a.url) for a in pushed.alerts] == [("Title", "Body", "/x")]

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "preview")
    assert await outbox.send_team_alert("Title", "Body", url="/x") is False
    assert len(pushed.alerts) == 1


def test_the_live_channel_refuses_to_run_in_tests():
    with pytest.raises(RuntimeError):
        outbox._live_alerter()


@pytest.mark.asyncio
async def test_old_failures_are_not_alerted_about(world):
    await live()
    alerter = RecordingAlerter()
    await book(world)
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingNotice).values(status="failed", delivered_at=NOW - timedelta(days=3))
        )
        await session.commit()
    assert await alerts.check(now=NOW, alerter=alerter) == 0


# ---------------------------------------------------------------------------
# The tab and the public routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_messages_list_names_each_kind_and_marks_what_needs_the_team(world):
    await live()
    lives_here(world)
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    world.texts.refuse(ALEX_MOBILE, "opted_out")
    await run(world, MORNING)

    async with world.client(world.team_app()) as client:
        listed = (await client.get(f"{TEAM}/notices")).json()
    by_kind = {n["kind"]: n for n in listed}
    assert by_kind[PROSPECT_REMINDER]["kind_label"] == "Prospect's reminder: 8 AM on the day"
    assert by_kind[RESIDENT_REMINDER]["kind_label"] == "Residents' reminder: 8 AM on the day"
    assert by_kind[PROSPECT_REMINDER]["attention"] == "failed"
    assert by_kind[PROSPECT_REMINDER]["alerted_at"] is not None
    assert by_kind[RESIDENT_REMINDER]["attention"] is None
    assert by_kind["prospect_confirmation"]["kind_label"] == "Prospect's confirmation"


@pytest.mark.asyncio
async def test_no_public_answer_changes_or_mentions_reminders_or_hosts(world):
    await live()
    lives_here(world)
    alex = await book(world)
    await run(world, MORNING)
    async with world.client() as client:
        looked = await client.post(
            f"{PUBLIC}/bookings/lookup", json={"cancel_token": alex["cancel_token"]}
        )
        page = await client.get(f"{PUBLIC}/units/{UNIT}")
    assert looked.status_code == 200
    assert set(looked.json()) == {
        "status",
        "unit",
        "address",
        "starts_at",
        "ends_at",
        "timezone",
        "mobile",
        "texted",
        "corrections_left",
        "started",
        "cancelled_by_team",
    }
    for text in (looked.text, page.text, str(alex)):
        for word in ("reminder", "alert", JANE_MOBILE, RESIDENT, "resident"):
            assert word not in text


@pytest.mark.asyncio
async def test_the_job_has_work_when_a_showing_is_coming_up(world):
    assert await holds.anything_to_do(NOW) is False
    await book(world)
    await run(world, NOW + timedelta(minutes=10))
    assert await holds.anything_to_do(NOW + timedelta(minutes=11)) is True
    assert await holds.anything_to_do(at(15)) is True
    assert await holds.anything_to_do(at(16, 1)) is False


@pytest.mark.asyncio
async def test_due_at_is_8_am_local_on_the_day_across_a_clock_change():
    zone = "America/New_York"
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    # The clocks go forward at 2 AM on Sunday Mar 14, 2027: 8 AM that day is 12:00 UTC.
    start = datetime(2027, 3, 14, 10, tzinfo=ZoneInfo(zone))
    due = reminders.due_at(start, zone)
    assert due == datetime(2027, 3, 14, 8, tzinfo=ZoneInfo(zone))
    assert due.astimezone(UTC) == datetime(2027, 3, 14, 12, tzinfo=UTC)
    assert reminders.due_at(at(9), zone) == MORNING
    assert reminders.due_at(at(18), zone) == MORNING


@pytest.mark.asyncio
async def test_the_resident_lookup_is_the_announcements_own(world, monkeypatch):
    lives_here(world)
    await book(world)
    seen = []
    real = residents.find

    async def find(unit, day, **kwargs):
        seen.append((unit, day))
        return await real(unit, day, **kwargs)

    monkeypatch.setattr(residents, "find", find)
    await run(world, MORNING)
    assert seen == [(UNIT, WEDNESDAY)]
    async with AsyncSessionFactory() as session:
        showing = await session.scalar(select(Showing))
    assert showing.resident_attention is None
