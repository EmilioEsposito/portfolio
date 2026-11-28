"""The prospect's invite wording, and the prospect's text 1 hour before the showing.

The words without a database, then bookings through the public routes and the
job's steps against the test database with the reminders' stand-ins (Quo is a
``RecordingTextSender``, the calendars an ``InMemoryCalendar``) and a moved
clock. Nothing is sent anywhere. Names, numbers and addresses are made up.
"""

# The ``world`` fixture is imported, so each test's parameter of the same name
# is that fixture, not a redefinition.
# ruff: noqa: F811

from datetime import UTC, datetime

import pytest

from api.src.showing_bookings import delivery_checks, wording
from api.src.tests.showing_public import ALEX_EMAIL, ALEX_MOBILE, JANE, at, booking, by_kind, rows
from api.src.tests.test_showing_reminders import (
    book,
    cancel,
    live,
    run,
    short_notice,
)
from api.src.tests.test_showing_reminders import world as reminder_world
from api.src.tests.test_showing_team_actions import caller, team_book

world = reminder_world

HOUR = delivery_checks.HOUR_KIND
VISIT = wording.Visit(
    unit="12-03",
    address="12 Example Ave Apt 3, Pittsburgh, PA 15201",
    starts_at=datetime(2026, 12, 1, 18, 30, tzinfo=UTC),
    ends_at=datetime(2026, 12, 1, 19, 0, tzinfo=UTC),
    timezone="America/New_York",
)
CANCEL = "https://serniacapital.com/showings/cancel/tok-123"
ALEX_HOUR_TEXT = (
    "Hi Alex. Reminder: You have an in-person showing at 320 Example St in 1 hour at 4:00 PM"
)


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_the_invite_is_titled_with_their_name_and_the_street():
    assert (
        wording.invite_summary(VISIT, "Pat  Example")
        == "Showing: Pat Example - 12 Example Ave Apt 3"
    )
    assert wording.invite_cancelled_summary(VISIT, "Pat Example") == (
        "Cancelled: Showing: Pat Example - 12 Example Ave Apt 3"
    )


def test_the_invite_says_who_meets_them_how_to_cancel_and_whom_to_ask():
    assert wording.invite_description(VISIT, CANCEL, "+14125550100") == (
        "A Sernia agent will meet you there.\n"
        f"To cancel, use this link: {CANCEL}\n"
        "Declining this invite does not cancel the showing. Only the link does.\n"
        "Questions? Text or call us at 412-555-0100."
    )
    # With no team number configured the line is left out, nothing else.
    assert "Questions" not in wording.invite_description(VISIT, CANCEL, None)


def test_a_moved_invite_says_what_it_replaces_and_a_cancelled_one_says_so():
    moved = wording.invite_description(
        VISIT, CANCEL, "+14125550100", replaces=[datetime(2026, 11, 30, 19, 0, tzinfo=UTC)]
    )
    assert moved.splitlines()[0] == (
        "Your showing has moved to this time. It replaces your showing on Mon, Nov 30 at 2:00 PM."
    )
    assert wording.invite_cancelled_description(VISIT, "+14125550100") == (
        "Your in-person showing on Tue, Dec 1 at 1:30 PM at 12 Example Ave Apt 3 is cancelled.\n"
        "Questions? Text or call us at 412-555-0100."
    )


@pytest.mark.parametrize(
    "name, hello", [("Pat Example", "Hi Pat."), ("pat", "Hi pat."), ("  ", "Hi.")]
)
def test_the_text_1_hour_before_in_emilios_words(name, hello):
    assert wording.hour_reminder_text(VISIT, name) == (
        f"{hello} Reminder: You have an in-person showing at 12 Example Ave Apt 3 in 1 hour "
        "at 1:30 PM"
    )


# ---------------------------------------------------------------------------
# The invite a booking sends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_booking_invites_the_prospect_with_the_new_title_and_description(world):
    result = await book(world)

    _, _, notices = await rows()
    (invite,) = by_kind(notices)["prospect_invite"]
    assert invite.payload["summary"] == "Showing: Alex Parker - 320 Example St"
    assert invite.payload["location"] == "320 Example St"
    assert invite.payload["attendees"] == [ALEX_EMAIL]
    description = invite.payload["description"]
    assert f"/showings/cancel/{result['cancel_token']}" in description
    assert "Only the link does." in description and "412-555-0100" in description


@pytest.mark.asyncio
async def test_a_cancelled_booking_withdraws_its_invite_in_the_same_words(world):
    await live()
    result = await book(world)
    await run(world, world.now)

    await cancel(world, result["cancel_token"])

    _, _, notices = await rows()
    (withdrawn,) = by_kind(notices)["prospect_invite_cancel"]
    assert withdrawn.payload["summary"] == "Cancelled: Showing: Alex Parker - 320 Example St"
    assert withdrawn.payload["description"].startswith(
        "Your in-person showing on Wed, Apr 14 at 4:00 PM at 320 Example St is cancelled."
    )


# ---------------------------------------------------------------------------
# The text 1 hour before
# ---------------------------------------------------------------------------


def hour_texts(world) -> list[str]:
    return [body for body in world.texts.to(ALEX_MOBILE) if "in 1 hour" in body]


@pytest.mark.asyncio
async def test_it_goes_1_hour_before_from_the_team_line_once(world):
    await live()
    await book(world)
    await run(world, world.now)

    await run(world, at(14, 59))
    assert hour_texts(world) == []

    report = await run(world, at(15, 0))
    assert report.hour_texts == 1
    assert hour_texts(world) == [ALEX_HOUR_TEXT]
    (sent,) = [text for text in world.texts.sent if "in 1 hour" in text.body]
    assert sent.line == "team"

    await run(world, at(15, 10))
    assert hour_texts(world) == [ALEX_HOUR_TEXT]


@pytest.mark.asyncio
async def test_in_capture_mode_it_is_kept_not_sent(world):
    await book(world)

    await run(world, at(15, 0))

    (notice,) = by_kind((await rows())[2])[HOUR]
    assert (notice.status, notice.payload["body"]) == ("captured", ALEX_HOUR_TEXT)
    assert hour_texts(world) == []


@pytest.mark.asyncio
async def test_none_for_someone_who_booked_less_than_75_minutes_before(world):
    await live()
    short_notice(world)
    world.now = at(14, 50)
    await book(world)
    await run(world, at(14, 50))

    await run(world, at(15, 0))

    assert HOUR not in by_kind((await rows())[2])
    assert hour_texts(world) == []


@pytest.mark.asyncio
async def test_75_minutes_before_is_still_early_enough(world):
    await live()
    short_notice(world)
    world.now = at(14, 45)
    await book(world)
    await run(world, at(14, 45))

    await run(world, at(15, 0))

    assert hour_texts(world) == [ALEX_HOUR_TEXT]


@pytest.mark.asyncio
async def test_none_after_a_cancellation(world):
    await live()
    result = await book(world)
    await cancel(world, result["cancel_token"])

    await run(world, at(15, 0))

    assert HOUR not in by_kind((await rows())[2])
    assert hour_texts(world) == []


@pytest.mark.asyncio
async def test_a_queued_one_is_stopped_by_a_cancellation(world):
    await book(world)  # capture mode: queued and kept
    await run(world, world.now)
    world.now = at(15, 0)
    from api.src.showing_bookings import daily_texts

    assert (await daily_texts.queue_due(now=world.now)).hour_texts == 1
    (notice,) = by_kind((await rows())[2])[HOUR]
    assert notice.status == "pending"

    result = (await rows())[1][0]
    await cancel(world, result.cancel_token)

    (notice,) = by_kind((await rows())[2])[HOUR]
    assert notice.status == "cancelled"


@pytest.mark.asyncio
async def test_not_once_less_than_45_minutes_are_left(world):
    await live()
    await book(world)
    await run(world, world.now)

    await run(world, at(15, 16))

    assert HOUR not in by_kind((await rows())[2])
    assert hour_texts(world) == []


@pytest.mark.asyncio
async def test_none_for_someone_who_takes_no_texts(world):
    await live()
    answer = await team_book(world, caller(email=ALEX_EMAIL.replace("alex", "casey")))
    assert answer.status_code == 201, answer.text

    await run(world, at(15, 0))

    assert HOUR not in by_kind((await rows())[2])


@pytest.mark.asyncio
async def test_too_late_to_send_by_hand_45_minutes_before(world):
    await book(world)
    await run(world, at(15, 0))
    (notice,) = by_kind((await rows())[2])[HOUR]

    from api.src.database.database import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        assert await delivery_checks.too_late(session, notice, at(15, 15)) is None
        assert await delivery_checks.too_late(session, notice, at(15, 16)) == (
            delivery_checks.HOUR_MISSED
        )


@pytest.mark.asyncio
async def test_the_days_reminder_and_the_hosts_event_still_go(world):
    await live()
    await book(world, booking())
    await run(world, world.now)

    await run(world, at(8))

    reminders = [body for body in world.texts.to(ALEX_MOBILE) if "a reminder of" in body]
    assert len(reminders) == 1
    events = [event.draft for event in world.calendar.events.values()]
    assert any(draft.attendees == [JANE] for draft in events)
