"""When each party is told, and what texts due together say as one.

Bookings and cancellations go through the public routes, and the job's steps
run as a moved clock goes by, against the test database with the stand-ins of
``showing_public.py``: Quo is a ``RecordingTextSender``, the calendars an
``InMemoryCalendar``. Test mode: the prospects, the resident and the host are
on the test list. Nothing is sent anywhere. Names and numbers are made up.

The rules (``api/src/showing_bookings/README.md``):

- The prospect: confirmation and cancellation texts at once, any hour; a
  reminder at 8 AM on the day for a booking made before then, unless the text
  1 hour before comes less than 90 minutes later.
- The host: a text on every change, at once from 8 AM to 9 PM, else at 8 AM,
  where it opens the morning text.
- The residents: at once from 8 AM to 9 PM, else at 8 AM; every text lists the
  unit's upcoming showings they were told of; texts due together are one.
"""

from datetime import date, datetime, time, timedelta

import pytest
import pytest_asyncio

from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import cancelling, holds, open_times, outbox, reminders
from api.src.tests.showing_helpers import LOCAL, clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    TUESDAY,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
)
from api.src.tests.test_rental_listings import clear_rental_data

PUBLIC = "/api/showing-bookings/public"
RESIDENT = "+14125550142"
JANE_MOBILE = "+14125550101"
THURSDAY = date(2027, 4, 15)
PLACE = "320 Example St (unit 320-03)"
CLOSING = (
    "A Sernia agent will be there the whole time. You may be present or we can let "
    "ourselves in if you are not home."
)


def on(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL)


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES",
        ",".join([ALEX_MOBILE, ROBIN_MOBILE, CASEY_MOBILE, RESIDENT, JANE_MOBILE]),
    )
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL}")
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()
    await clear_rental_data()
    state = PublicWorld()
    # Bookings an hour ahead, so a booking at night or early on the day is offered.
    state.rules = ShowingRules(showing_notice_hours=1)
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)

    def no_live_alerts():
        raise AssertionError("The live alert channel was reached.")

    monkeypatch.setattr(outbox, "_live_alerter", no_live_alerts)
    await add_world_rows(state)
    state.residents.add("Morgan Ellis", RESIDENT, unit=UNIT)
    await store_settings(send_mode="test")
    yield state
    open_times.forget_free_busy()
    reminders.forget_lookups()
    await clear_showing_data()


async def book(world: PublicWorld, body: dict) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=body)
    assert answer.status_code == 201, answer.text
    return answer.json()


async def cancel(world: PublicWorld, token: str) -> None:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token})
    assert answer.status_code == 200, answer.text


async def run(world: PublicWorld, now: datetime) -> None:
    world.now = now
    await holds.send_pending(
        now=now, texts=world.texts, calendar=world.calendar, emails=world.emails
    )


async def booked_at(world: PublicWorld, now: datetime, body: dict) -> dict:
    """Book at ``now``, its confirmation reported delivered, and the job run once."""
    world.now = now
    result = await book(world, body)
    world.texts.report("+1" + body["mobile"].replace("-", ""), "delivered")
    await run(world, now + timedelta(minutes=1))
    return result


def to(world: PublicWorld, number: str) -> list[str]:
    return world.texts.to(number)


def robin(start: datetime) -> dict:
    return booking(start, name="Robin Lee", mobile=ROBIN_MOBILE, email=ROBIN_EMAIL)


def casey(start: datetime) -> dict:
    return booking(start, name="Casey Morgan", mobile=CASEY_MOBILE, email=CASEY_EMAIL)


def host_text(*lines: str, upcoming: list[str]) -> str:
    return "\n".join(
        [f"Sernia Capital: {lines[0]}", *lines[1:], "", "Your upcoming showings:", *upcoming]
    )


def morning(*lines: str, today: list[str], day: str = "Wed, Apr 14") -> str:
    if not lines:
        return "\n".join([f"Sernia Capital: your showings today, {day}:", *today])
    return "\n".join(
        [f"Sernia Capital: {lines[0]}", *lines[1:], "", f"Your showings today, {day}:", *today]
    )


ALEX_BOOKED = f"Alex Parker booked your showing of {PLACE} on Wed, Apr 14 at 4:00 PM."
ANNOUNCEMENT = (
    "Automated showing alert: We've scheduled an in-person showing of your unit on "
    f"Wed, Apr 14 at 4:00 PM. {CLOSING}"
)


# ---------------------------------------------------------------------------
# Each party, booked at 7 PM, 10 PM and 7 AM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booked_at_7_pm_everyone_is_told_at_once(world):
    await booked_at(world, on(TUESDAY, 19), booking(at(16)))

    assert to(world, ALEX_MOBILE)[0].startswith("Sernia Capital: your in-person showing is booked")
    assert to(world, JANE_MOBILE) == [
        host_text(
            ALEX_BOOKED,
            upcoming=[f"- Wed, Apr 14 at 4:00 PM at {PLACE}, 1 person"],
        )
    ]
    assert to(world, RESIDENT) == [ANNOUNCEMENT]

    # At 8 AM the host's morning text and Alex's reminder; nothing more for the residents
    # beyond the day's reminder, as they were told already.
    await run(world, at(8))
    assert to(world, JANE_MOBILE)[1:] == [morning(today=[f"- 4:00 PM at {PLACE}, 1 person"])]
    assert to(world, ALEX_MOBILE)[-1].startswith(
        f"Sernia Capital: a reminder of your in-person showing today at 4:00 PM at {PLACE}."
    )
    assert to(world, RESIDENT)[1:] == [
        "Automated showing alert: Reminder: an in-person showing of your unit is scheduled "
        f"today at 4:00 PM. {CLOSING}"
    ]


@pytest.mark.asyncio
async def test_booked_at_10_pm_the_host_and_residents_hear_at_8_am_in_one_text_each(world):
    await booked_at(world, on(TUESDAY, 22), booking(at(16)))

    assert len(to(world, ALEX_MOBILE)) == 1  # the confirmation, at once
    assert to(world, JANE_MOBILE) == [] and to(world, RESIDENT) == []

    await run(world, at(7, 59))
    assert to(world, JANE_MOBILE) == [] and to(world, RESIDENT) == []

    await run(world, at(8))
    # The change opens the morning text: one text.
    assert to(world, JANE_MOBILE) == [
        morning(ALEX_BOOKED, today=[f"- 4:00 PM at {PLACE}, 1 person"])
    ]
    # The announcement and the day's reminder: one text, the announcement alone.
    assert to(world, RESIDENT) == [ANNOUNCEMENT]
    _, _, notices = await rows()
    kinds = by_kind(notices)
    (change,) = kinds["host_late_join"]
    (host_morning,) = kinds["host_morning"]
    went_alone = {change.status, host_morning.status}
    assert went_alone == {"sent", "cancelled"}
    (taken,) = [n for n in (change, host_morning) if n.status == "cancelled"]
    assert taken.last_error.startswith("Not sent by itself: it went in the same text as notice")
    # Booked before 8 AM, so Alex is reminded at 8 AM too.
    assert to(world, ALEX_MOBILE)[-1].startswith("Sernia Capital: a reminder of your")


@pytest.mark.asyncio
async def test_booked_at_7_am_the_host_and_residents_wait_for_8_am(world):
    await booked_at(world, at(7), booking(at(16)))

    assert len(to(world, ALEX_MOBILE)) == 1
    assert to(world, JANE_MOBILE) == [] and to(world, RESIDENT) == []

    await run(world, at(8))

    assert to(world, JANE_MOBILE) == [
        morning(ALEX_BOOKED, today=[f"- 4:00 PM at {PLACE}, 1 person"])
    ]
    assert to(world, RESIDENT) == [ANNOUNCEMENT]
    # Booked before 8 AM: the day's reminder.
    assert [body for body in to(world, ALEX_MOBILE) if "a reminder of" in body]


@pytest.mark.asyncio
async def test_a_booking_after_8_am_on_the_day_gets_no_reminder_but_the_hour_text(world):
    await booked_at(world, at(9), booking(at(16)))

    assert to(world, JANE_MOBILE) == [
        host_text(ALEX_BOOKED, upcoming=[f"- Wed, Apr 14 at 4:00 PM at {PLACE}, 1 person"])
    ]
    assert to(world, RESIDENT) == [ANNOUNCEMENT]

    for moment in (at(9, 30), at(12), at(15)):
        await run(world, moment)

    assert not [body for body in to(world, ALEX_MOBILE) if "a reminder of" in body]
    assert to(world, ALEX_MOBILE)[-1] == (
        "Hi Alex. Reminder: You have an in-person showing at 320 Example St in 1 hour at 4:00 PM"
    )


@pytest.mark.asyncio
async def test_for_an_early_showing_the_hour_text_wins_over_the_8_am_reminder(world):
    await booked_at(world, on(TUESDAY, 14), booking(at(9, 30)))

    await run(world, at(8))
    assert not [body for body in to(world, ALEX_MOBILE) if "a reminder of" in body]
    _, _, notices = await rows()
    assert "prospect_reminder" not in by_kind(notices)

    await run(world, at(8, 30))
    assert to(world, ALEX_MOBILE)[-1].startswith("Hi Alex. Reminder: You have an in-person")


@pytest.mark.asyncio
async def test_a_late_showing_keeps_both_reminders(world):
    await booked_at(world, on(TUESDAY, 14), booking(at(11)))

    await run(world, at(8))
    await run(world, at(10))

    reminders_sent = [
        body for body in to(world, ALEX_MOBILE) if "a reminder of" in body or "in 1 hour" in body
    ]
    assert len(reminders_sent) == 2


# ---------------------------------------------------------------------------
# The host: every change, merged when due together
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_changes_at_night_open_the_hosts_morning_text_in_order(world):
    alex = await booked_at(world, on(TUESDAY, 20), booking(at(16)))
    await booked_at(world, on(TUESDAY, 22), robin(at(17)))
    world.now = on(TUESDAY, 22, 30)
    await cancel(world, alex["cancel_token"])

    await run(world, at(8))

    robin_booked = f"Robin Lee booked your showing of {PLACE} on Wed, Apr 14 at 5:00 PM."
    alex_left = (
        f"Alex Parker cancelled your showing of {PLACE} on Wed, Apr 14 at 4:00 PM. "
        "No one is booked now, so the showing is cancelled."
    )
    assert to(world, JANE_MOBILE)[1:] == [
        morning(robin_booked, alex_left, today=[f"- 5:00 PM at {PLACE}, 1 person"])
    ]


@pytest.mark.asyncio
async def test_a_change_in_the_daytime_lists_every_upcoming_showing(world):
    await booked_at(world, at(9), booking(at(16)))
    await booked_at(world, at(10), robin(on(THURSDAY, 16)))

    assert to(world, JANE_MOBILE)[-1] == host_text(
        f"Robin Lee booked your showing of {PLACE} on Thu, Apr 15 at 4:00 PM.",
        upcoming=[
            f"- Wed, Apr 14 at 4:00 PM at {PLACE}, 1 person",
            f"- Thu, Apr 15 at 4:00 PM at {PLACE}, 1 person",
        ],
    )


# ---------------------------------------------------------------------------
# The residents: the unit's upcoming showings, merged when due together
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_showing_lists_both_and_marks_the_new_one(world):
    await booked_at(world, at(9), booking(at(16)))
    await booked_at(world, at(10), robin(on(THURSDAY, 16)))

    assert to(world, RESIDENT) == [
        ANNOUNCEMENT,
        "\n".join(
            [
                "Automated showing alert: We've scheduled another in-person showing of your "
                "unit. Upcoming showings:",
                "- Wed, Apr 14 at 4:00 PM",
                "- Thu, Apr 15 at 4:00 PM (new)",
                CLOSING,
            ]
        ),
    ]


@pytest.mark.asyncio
async def test_two_showings_booked_at_night_are_one_text_at_8_am(world):
    await booked_at(world, on(TUESDAY, 21, 30), booking(at(16)))
    await booked_at(world, on(TUESDAY, 22), robin(on(THURSDAY, 16)))

    await run(world, at(8))

    assert to(world, RESIDENT) == [
        "\n".join(
            [
                "Automated showing alert: We've scheduled in-person showings of your unit. "
                "Upcoming showings:",
                "- Wed, Apr 14 at 4:00 PM",
                "- Thu, Apr 15 at 4:00 PM",
                CLOSING,
            ]
        )
    ]
    _, _, notices = await rows()
    went = [n for n in by_kind(notices)["resident_new_showing"] if n.status == "sent"]
    (text,) = went
    assert sorted(text.payload["showing_ids"]) == sorted({n.showing_id for n in notices})


@pytest.mark.asyncio
async def test_a_cancellation_with_another_showing_left_lists_it(world):
    alex = await booked_at(world, at(9), booking(at(16)))
    await booked_at(world, at(10), robin(on(THURSDAY, 16)))

    world.now = at(11)
    await cancel(world, alex["cancel_token"])
    await run(world, at(11, 1))

    assert to(world, RESIDENT)[-1] == "\n".join(
        [
            "Automated showing alert: The in-person showing of your unit on Wed, Apr 14 at "
            "4:00 PM is cancelled. Still scheduled:",
            "- Thu, Apr 15 at 4:00 PM",
        ]
    )


@pytest.mark.asyncio
async def test_a_cancellation_with_nothing_left_just_says_so(world):
    alex = await booked_at(world, at(9), booking(at(16)))

    world.now = at(11)
    await cancel(world, alex["cancel_token"])
    await run(world, at(11, 1))

    assert to(world, RESIDENT) == [
        ANNOUNCEMENT,
        "Automated showing alert: The in-person showing of your unit on Wed, Apr 14 at "
        "4:00 PM is cancelled.",
    ]


@pytest.mark.asyncio
async def test_a_cancellation_at_night_waits_for_8_am_and_goes_with_the_reminder(world):
    alex = await booked_at(world, on(TUESDAY, 15), booking(at(16)))
    await booked_at(world, on(TUESDAY, 16), robin(at(17)))
    world.now = on(TUESDAY, 22)
    await cancel(world, alex["cancel_token"])
    await run(world, on(TUESDAY, 22, 1))
    before = len(to(world, RESIDENT))

    await run(world, at(8))

    assert to(world, RESIDENT)[before:] == [
        "Automated showing alert: The in-person showing of your unit on Wed, Apr 14 at "
        "4:00 PM is cancelled. Reminder: an in-person showing of your unit is scheduled "
        f"today at 5:00 PM. {CLOSING}"
    ]


@pytest.mark.asyncio
async def test_a_showing_whose_announcement_is_held_is_left_out_until_it_goes(world):
    await booked_at(world, at(9), booking(at(16)))
    # Robin's confirmation is not reported yet (and has not been out five minutes), so
    # the announcement of 5 PM is held.
    world.now = at(9, 57)
    world.texts.report(ROBIN_MOBILE, "sent")
    await book(world, robin(at(17)))
    await booked_at(world, at(10), casey(on(THURSDAY, 16)))

    assert to(world, RESIDENT)[-1] == "\n".join(
        [
            "Automated showing alert: We've scheduled another in-person showing of your unit. "
            "Upcoming showings:",
            "- Wed, Apr 14 at 4:00 PM",
            "- Thu, Apr 15 at 4:00 PM (new)",
            CLOSING,
        ]
    )

    world.texts.report(ROBIN_MOBILE, "delivered")
    await run(world, at(10, 2))

    assert to(world, RESIDENT)[-1] == "\n".join(
        [
            "Automated showing alert: We've scheduled another in-person showing of your unit. "
            "Upcoming showings:",
            "- Wed, Apr 14 at 4:00 PM",
            "- Wed, Apr 14 at 5:00 PM (new)",
            "- Thu, Apr 15 at 4:00 PM",
            CLOSING,
        ]
    )


@pytest.mark.asyncio
async def test_told_stays_true_for_every_showing_a_merged_text_covered(world):
    """Two announcements went as one; cancelling the one whose own row was taken in
    still texts the residents, as they were told of it."""
    await booked_at(world, on(TUESDAY, 21, 30), booking(at(16)))
    robin_booking = await booked_at(world, on(TUESDAY, 22), robin(on(THURSDAY, 16)))
    await run(world, at(8))

    world.now = at(9)
    await cancel(world, robin_booking["cancel_token"])
    await run(world, at(9, 1))

    assert to(world, RESIDENT)[-1] == "\n".join(
        [
            "Automated showing alert: The in-person showing of your unit on Thu, Apr 15 at "
            "4:00 PM is cancelled. Still scheduled:",
            "- Wed, Apr 14 at 4:00 PM",
        ]
    )


@pytest.mark.asyncio
async def test_capture_mode_keeps_one_merged_text_with_the_others_noted(world):
    await store_settings(send_mode="capture")
    await booked_at(world, on(TUESDAY, 21, 30), booking(at(16)))
    await booked_at(world, on(TUESDAY, 22), robin(on(THURSDAY, 16)))

    await run(world, at(8))

    _, _, notices = await rows()
    announcements = by_kind(notices)["resident_new_showing"]
    assert sorted(n.status for n in announcements) == ["cancelled", "captured"]
    (kept,) = [n for n in announcements if n.status == "captured"]
    assert "We've scheduled in-person showings of your unit." in kept.payload["body"]
    # The other announcement and the day's reminder went in it.
    (other,) = [n for n in announcements if n.id != kept.id]
    (reminder,) = by_kind(notices)["resident_reminder"]
    assert kept.payload["merged_ids"] == sorted([other.id, reminder.id])
    assert (
        other.last_error
        == reminder.last_error
        == (f"Not sent by itself: it went in the same text as notice {kept.id}.")
    )
    assert world.texts.to(RESIDENT) == []
