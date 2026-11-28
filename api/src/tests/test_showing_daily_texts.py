"""The host's morning text and the prospect's apply text.

Each scenario books through the public or team routes, cancels or marks
attendance through them, runs the job's steps (``holds.send_pending``) as the
clock moves, and checks the rows in ``showing_notices`` and what the stand-in
for Quo recorded. Test mode: every number here is a made-up one on the test list,
and nothing is sent anywhere.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import daily_texts, delivery_checks, holds, outbox, wording
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.schema import TextPayload
from api.src.tests.showing_helpers import store_settings
from api.src.tests.showing_public import (
    ALEX_MOBILE,
    CASEY_MOBILE,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    at,
    booking,
    rows,
)
from api.src.tests.test_showing_cancelling import team
from api.src.tests.test_showing_reminders import (
    JANE_MOBILE,
    SAM_MOBILE,
    book,
    cancel,
    casey,
    live,
    of_kind,
    robin,
    run,
    short_notice,
)
from api.src.tests.test_showing_reminders import world as reminder_world
from api.src.tests.test_showing_team_actions import add_sam, caller, mark, team_book

# The reminders' world: one listing, Jane hosting, the stand-ins, a moved clock.
world = reminder_world

MORNING = delivery_checks.HOST_MORNING_KIND
APPLY = delivery_checks.APPLY_KIND
THURSDAY = date(2027, 4, 15)


def morning_texts(world: PublicWorld, number: str = JANE_MOBILE) -> list[str]:
    """The texts listing the day's showings, alone or after changes due with them."""
    return [body for body in world.texts.to(number) if "your showings today" in body.lower()]


def apply_texts(world: PublicWorld, number: str = ALEX_MOBILE) -> list[str]:
    return [body for body in world.texts.to(number) if "/apply" in body]


async def showing_of(token: str) -> tuple[int, int]:
    """The showing id and attendee id of a booking, by its cancel token."""
    _, attendees, _ = await rows()
    (person,) = [a for a in attendees if a.cancel_token == token]
    return person.showing_id, person.id


# ---------------------------------------------------------------------------
# The host's morning text
# ---------------------------------------------------------------------------


ONE_SHOWING = (
    "Sernia Capital: your showings today, Wed, Apr 14:\n"
    "- 4:00 PM at 320 Example St (unit 320-03), 1 person"
)
# Robin's cancellation of the 10 AM showing, due with the morning text: one text.
ROBIN_LEFT_AND_ONE_SHOWING = (
    "Sernia Capital: Robin Lee cancelled your showing of 320 Example St (unit 320-03) on "
    "Wed, Apr 14 at 10:00 AM. No one is booked now, so the showing is cancelled.\n\n"
    "Your showings today, Wed, Apr 14:\n"
    "- 4:00 PM at 320 Example St (unit 320-03), 1 person"
)


@pytest.mark.asyncio
async def test_the_morning_text_goes_at_8_am_not_at_7_59_and_never_twice(world):
    await live()
    await book(world)
    await run(world, at(7, 59))
    assert await of_kind(MORNING) == []
    assert morning_texts(world) == []

    report = await run(world, at(8))
    assert report.host_mornings == 1
    assert morning_texts(world) == [ONE_SHOWING]
    (notice,) = await of_kind(MORNING)
    assert (notice.status, notice.recipient, notice.recipient_key, notice.guard_reason) == (
        "sent",
        JANE_MOBILE,
        f"agent:{world.agent_id}:morning:2027-04-14",
        "test_recipient",
    )

    for minutes in (1, 2, 60, 239):
        report = await run(world, at(8) + timedelta(minutes=minutes))
        assert report.host_mornings == 0
    assert len(morning_texts(world)) == 1
    assert len(await of_kind(MORNING)) == 1


@pytest.mark.asyncio
async def test_three_showings_are_listed_in_order_with_how_many_people(world):
    await live()
    short_notice(world, 12)
    await book(world)  # Alex at 4 PM
    await book(world, robin(at(10)))
    await book(world, casey(at(10)))  # joins Robin at 10 AM
    await book(world, casey(at(13)))

    await run(world, at(8))
    assert morning_texts(world) == [
        "Sernia Capital: your showings today, Wed, Apr 14:\n"
        "- 10:00 AM at 320 Example St (unit 320-03), 2 people\n"
        "- 1:00 PM at 320 Example St (unit 320-03), 1 person\n"
        "- 4:00 PM at 320 Example St (unit 320-03), 1 person"
    ]
    (notice,) = await of_kind(MORNING)
    # It belongs to the day's first showing and names every one it lists.
    showings, _, _ = await rows()
    by_start = {s.starts_at: s.id for s in showings}
    assert notice.showing_id == by_start[at(10)]
    assert sorted(notice.payload["showing_ids"]) == sorted(by_start.values())
    # No prospect's name, number or email in it.
    for secret in ("Alex", "Robin", "Casey", ALEX_MOBILE, ROBIN_MOBILE, CASEY_MOBILE, "@"):
        assert secret not in notice.payload["body"]


@pytest.mark.asyncio
async def test_after_a_missed_morning_it_goes_late_with_the_showings_still_ahead(world):
    await live()
    short_notice(world, 12)
    await book(world)  # 4 PM
    await book(world, robin(at(9)))

    # The job did not run until 10:30: the 9 AM showing has started.
    await run(world, at(10, 30))
    assert morning_texts(world) == [ONE_SHOWING]


@pytest.mark.asyncio
async def test_a_morning_missed_past_noon_is_not_sent(world):
    await live()
    await book(world)
    await run(world, at(12))
    await run(world, at(13))
    assert await of_kind(MORNING) == []
    assert morning_texts(world) == []


@pytest.mark.asyncio
async def test_queued_before_noon_and_delivered_after_it_is_not_sent(world):
    await live()
    await book(world)
    world.now = at(11, 59)
    await daily_texts.queue_due(now=at(11, 59))
    await outbox.drain(now=at(12), texts=world.texts, calendar=world.calendar)
    (notice,) = await of_kind(MORNING)
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.MORNING_MISSED)
    assert morning_texts(world) == []


@pytest.mark.asyncio
async def test_an_agent_hosting_nothing_that_day_gets_nothing(world):
    await live()
    sam = await add_sam()
    await book(world)  # Jane hosts it
    await run(world, at(8))
    assert len(morning_texts(world)) == 1
    assert morning_texts(world, SAM_MOBILE) == []
    keys = {notice.recipient_key for notice in await of_kind(MORNING)}
    assert keys == {f"agent:{world.agent_id}:morning:2027-04-14"}
    assert f"agent:{sam}:morning:2027-04-14" not in keys


@pytest.mark.asyncio
async def test_an_agent_without_a_mobile_gets_nothing(world):
    await live()
    await book(world)
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAgentRecord)
            .where(ShowingAgentRecord.id == world.agent_id)
            .values(mobile=None)
        )
        await session.commit()
    for hour in (8, 9, 11):
        await run(world, at(hour))
    assert await of_kind(MORNING) == []
    assert morning_texts(world) == []


@pytest.mark.asyncio
async def test_a_showing_cancelled_before_the_morning_is_left_out(world):
    await live()
    short_notice(world, 12)
    await book(world)  # 4 PM
    robin_booking = await book(world, robin(at(10)))
    world.now = at(7)
    await cancel(world, robin_booking["cancel_token"])  # the last one: the showing is cancelled

    await run(world, at(8))  # the host's text about it waited for 8 AM: one text
    assert morning_texts(world) == [ROBIN_LEFT_AND_ONE_SHOWING]


@pytest.mark.asyncio
async def test_a_day_whose_showings_are_all_cancelled_gets_no_morning_text(world):
    await live()
    alex = await book(world)
    world.now = at(7)
    await cancel(world, alex["cancel_token"])
    await run(world, at(8))
    assert await of_kind(MORNING) == []


@pytest.mark.asyncio
async def test_a_showing_cancelled_between_queueing_and_sending_is_left_out(world):
    await live()
    short_notice(world, 12)
    await book(world)  # 4 PM
    robin_booking = await book(world, robin(at(10)))

    world.now = at(8)
    await daily_texts.queue_due(now=at(8))
    (notice,) = await of_kind(MORNING)
    assert notice.status == "pending" and "10:00 AM" in notice.payload["body"]

    await cancel(world, robin_booking["cancel_token"])  # its text takes the morning text in
    await run(world, at(8, 1))
    assert morning_texts(world) == [ROBIN_LEFT_AND_ONE_SHOWING]
    (morning,) = await of_kind(MORNING)
    assert morning.status == "cancelled"
    assert morning.last_error.startswith("Not sent by itself: it went in the same text as notice")


@pytest.mark.asyncio
async def test_a_morning_text_with_nothing_left_when_it_goes_is_cancelled(world):
    await live()
    alex = await book(world)
    world.now = at(8)
    await daily_texts.queue_due(now=at(8))
    await cancel(world, alex["cancel_token"])
    await run(world, at(8, 1))
    (notice,) = await of_kind(MORNING)
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.NOTHING_TODAY)
    assert morning_texts(world) == []


# ---------------------------------------------------------------------------
# The apply text
# ---------------------------------------------------------------------------


APPLY_TEXT = (
    "Sernia Capital: thanks for coming to see 320 Example St (unit 320-03). If you'd like "
    "to rent it, you can apply here: https://serniacapital.com/apply\nReply STOP to opt out."
)


@pytest.mark.asyncio
async def test_the_apply_text_goes_two_hours_after_the_start_and_once_only(world):
    await live()
    await book(world)  # 4 PM
    await run(world, at(17, 59))
    assert await of_kind(APPLY) == []

    report = await run(world, at(18))
    assert report.apply_texts == 1
    assert apply_texts(world) == [APPLY_TEXT]
    (notice,) = await of_kind(APPLY)
    assert (notice.status, notice.recipient, notice.recipient_key) == (
        "sent",
        ALEX_MOBILE,
        f"attendee:{notice.attendee_id}",
    )
    for minutes in (1, 60, 600, 60 * 30):
        report = await run(world, at(18) + timedelta(minutes=minutes))
        assert report.apply_texts == 0
    assert len(apply_texts(world)) == 1
    assert len(await of_kind(APPLY)) == 1


@pytest.mark.asyncio
async def test_the_apply_text_waits_for_8_am_when_it_falls_at_night(world):
    await live()
    await book(world)
    # The job did not run until 10 PM.
    await run(world, at(22))
    (notice,) = await of_kind(APPLY)
    assert (notice.status, notice.not_before) == ("pending", at(8, day=THURSDAY))
    assert apply_texts(world) == []
    await run(world, at(7, 59, day=THURSDAY))
    assert apply_texts(world) == []
    await run(world, at(8, day=THURSDAY))
    assert apply_texts(world) == [APPLY_TEXT]


@pytest.mark.asyncio
async def test_a_no_show_marked_before_it_is_queued_gets_nothing(world):
    await live()
    alex = await book(world)
    showing_id, attendee_id = await showing_of(alex["cancel_token"])
    world.now = at(16, 40)
    answer = await mark(world, showing_id, attendee_id, "no_show")
    assert answer.status_code == 200, answer.text

    await run(world, at(18))
    await run(world, at(19))
    assert await of_kind(APPLY) == []
    assert apply_texts(world) == []


@pytest.mark.asyncio
async def test_a_no_show_marked_between_queueing_and_sending_gets_nothing(world):
    await live()
    alex = await book(world)
    showing_id, attendee_id = await showing_of(alex["cancel_token"])
    await run(world, at(22))  # queued, waiting for 8 AM
    world.now = at(23)
    assert (await mark(world, showing_id, attendee_id, "no_show")).status_code == 200

    await run(world, at(8, day=THURSDAY))
    (notice,) = await of_kind(APPLY)
    assert (notice.status, notice.last_error) == ("cancelled", delivery_checks.NO_SHOW)
    assert apply_texts(world) == []


@pytest.mark.asyncio
async def test_someone_marked_as_attended_or_left_unmarked_gets_it(world):
    await live()
    alex = await book(world)
    await book(world, robin())
    showing_id, attendee_id = await showing_of(alex["cancel_token"])
    world.now = at(16, 40)
    assert (await mark(world, showing_id, attendee_id, "attended")).status_code == 200
    await run(world, at(18))
    assert apply_texts(world) == [APPLY_TEXT]
    assert apply_texts(world, ROBIN_MOBILE) == [APPLY_TEXT]


@pytest.mark.asyncio
async def test_a_cancelled_attendee_gets_nothing(world):
    await live()
    await book(world)
    robin_booking = await book(world, robin())
    await cancel(world, robin_booking["cancel_token"])
    await run(world, at(18))
    assert apply_texts(world) == [APPLY_TEXT]
    assert apply_texts(world, ROBIN_MOBILE) == []
    assert {notice.recipient for notice in await of_kind(APPLY)} == {ALEX_MOBILE}


@pytest.mark.asyncio
async def test_a_cancelled_showing_gets_nothing(world):
    await live()
    alex = await book(world)
    showing_id, _ = await showing_of(alex["cancel_token"])
    world.now = at(12)
    assert (await team(world, f"/showings/{showing_id}/cancel")).status_code == 200
    await run(world, at(18))
    assert await of_kind(APPLY) == []
    assert apply_texts(world) == []


@pytest.mark.asyncio
async def test_no_apply_text_without_consent_or_a_mobile(world):
    await live()
    without_texts = await team_book(world, caller(mobile="412-555-0119", texts=False))
    assert without_texts.status_code == 201, without_texts.text
    name_only = await team_book(world, caller(name="Sky Rivers"))
    assert name_only.status_code == 201, name_only.text

    await run(world, at(18))
    assert await of_kind(APPLY) == []
    assert apply_texts(world, CASEY_MOBILE) == []


@pytest.mark.asyncio
async def test_no_apply_text_to_a_number_known_to_be_wrong(world):
    await live()
    alex = await book(world)
    _, attendee_id = await showing_of(alex["cancel_token"])
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == attendee_id)
            .values(attention_reason="confirmation_undelivered")
        )
        await session.commit()
    await run(world, at(18))
    assert await of_kind(APPLY) == []


@pytest.mark.asyncio
async def test_a_showing_long_past_owes_no_apply_text(world):
    """A job that was off for days does not text people about a showing long past."""
    await live()
    await book(world)
    await run(world, at(16) + daily_texts.APPLY_LOOKBACK + timedelta(minutes=1))
    assert await of_kind(APPLY) == []


@pytest.mark.asyncio
async def test_the_messages_list_names_the_new_kinds(world):
    await live()
    await book(world)
    await run(world, at(8))
    await run(world, at(18))
    from api.src.tests.test_showing_team_actions import team_app

    async with world.client(team_app(world)) as client:
        answer = await client.get("/api/showing-bookings/notices")
    labels = {item["kind"]: item["kind_label"] for item in answer.json()}
    assert labels[MORNING] == "Host's morning text: today's showings"
    assert labels[APPLY] == "Prospect's text: apply link"


@pytest.mark.asyncio
async def test_the_job_wakes_for_an_apply_text_owed_and_sleeps_once_it_is_sent(world):
    """The job every minute returns after one query when nothing is owed."""
    await live()
    await book(world)
    await run(world, at(16, 30))
    # Nothing held, due or coming up, and no apply text owed yet.
    assert await holds.anything_to_do(at(17, 59)) is False
    assert await holds.anything_to_do(at(18)) is True
    await run(world, at(18))
    assert await holds.anything_to_do(at(18, 1)) is False


# ---------------------------------------------------------------------------
# A morning text never longer than a text can be
# ---------------------------------------------------------------------------

LONG_ADDRESS = "1234 Example Boulevard Northeast, Building C, Apartment Complex Entrance " * 2


def day_showing(hour: int, minute: int, address: str, unit: str = "320-03", booked: int = 1):
    start = at(hour, minute)
    return wording.DayShowing(
        visit=wording.Visit(
            unit=unit,
            address=address,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            timezone="America/New_York",
        ),
        booked=booked,
    )


def full_day(address_of) -> list:
    return [
        day_showing(9 + index // 2, 30 * (index % 2), address_of(index), unit=f"320-{index:02d}")
        for index in range(19)
    ]


def test_a_long_day_at_one_address_names_it_once():
    text = wording.host_morning_text(full_day(lambda index: LONG_ADDRESS.strip()))
    assert len(text) <= wording.TEXT_LIMIT
    lines = text.splitlines()
    assert lines[0] == (
        f"Sernia Capital: your showings today, Wed, Apr 14, at {LONG_ADDRESS.strip()}:"
    )
    assert lines[1] == "- 9:00 AM, unit 320-00, 1 person"
    assert lines[-1] == "- 6:00 PM, unit 320-18, 1 person"
    assert len(lines) == 20
    assert text.count(LONG_ADDRESS.strip()) == 1


def test_a_long_day_at_many_addresses_lists_time_unit_and_count():
    text = wording.host_morning_text(full_day(lambda index: f"{index} {LONG_ADDRESS}"))
    assert len(text) <= wording.TEXT_LIMIT
    lines = text.splitlines()
    assert lines[0] == "Sernia Capital: your showings today, Wed, Apr 14:"
    assert len(lines) == 20
    assert "Example Boulevard" not in text


def test_more_than_fit_lists_as_many_as_fit_and_says_how_many_more():
    showings = full_day(lambda index: f"{index} {LONG_ADDRESS}")
    text = wording.host_morning_text(showings, limit=300)
    assert len(text) <= 300
    lines = text.splitlines()
    listed = [line for line in lines if line.startswith("- ")]
    assert lines[-1] == f"…and {19 - len(listed)} more showings today."
    assert listed[0] == "- 9:00 AM, unit 320-00, 1 person"
    # In order, none skipped.
    assert listed == [
        f"- {wording._clock(s.visit.starts_at)}, unit {s.visit.unit}, 1 person"
        for s in showings[: len(listed)]
    ]
    # Even with the 1,600 limit, 60 showings never overflow.
    many = [day_showing(9, 0, f"{i} {LONG_ADDRESS}", unit=f"U-{i:03d}") for i in range(60)]
    text = wording.host_morning_text(many)
    assert len(text) <= wording.TEXT_LIMIT
    assert text.splitlines()[-1].startswith("…and ")


@pytest.mark.asyncio
async def test_a_day_that_would_overflow_is_queued_and_sent_within_the_limit(world):
    """Nineteen showings for one host, each at its own long address: the full list
    would be far over 1,600 characters. Queued, then rendered again at delivery."""
    await live()
    await book(world)  # an apply-text-owing booking keeps the other steps busy too
    async with AsyncSessionFactory() as session:
        for index in range(18):
            start = at(9 + index // 2, 30 * (index % 2))
            if start == at(16):
                continue
            showing = Showing(
                unit=f"500-{index:02d}",
                address=f"{index} {LONG_ADDRESS}",
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                agent_id=world.agent_id,
            )
            session.add(showing)
            await session.flush()
            session.add(
                ShowingAttendee(
                    showing_id=showing.id, name="Sky Rivers", source="team", mobile=None
                )
            )
        await session.commit()

    full = sum(len(f"{index} {LONG_ADDRESS}") for index in range(18))
    assert full > TextPayload.model_fields["body"].metadata[1].max_length

    report = await run(world, at(8))
    assert report.host_mornings == 1
    (text,) = morning_texts(world)
    assert len(text) <= 1600
    assert len(text.splitlines()) == 19  # the header and all 18 showings
    (notice,) = await of_kind(MORNING)
    assert notice.status == "sent"


# ---------------------------------------------------------------------------
# The morning text and the guard: only units it would really send for
# ---------------------------------------------------------------------------

OTHER_UNIT = "412-01"


async def two_units_booked(world, monkeypatch) -> dict[str, int]:
    """Alex at 320-03 at 10 AM and Robin at 412-01 at 4 PM, both hosted by Jane,
    booked outside production; then the clock and the environment move to the
    morning in production."""
    world.other_listings.append(RentalListing(unit=OTHER_UNIT, address="412 Example St"))
    short_notice(world, 12)
    await book(world, booking_at(at(10)))
    async with world.client() as client:
        answer = await client.post(
            f"/api/showing-bookings/public/units/{OTHER_UNIT}/bookings", json=robin(at(16))
        )
    assert answer.status_code == 201, answer.text
    await run(world, world.now + timedelta(minutes=1))
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    showings, _, _ = await rows()
    return {showing.unit: showing.id for showing in showings}


def booking_at(start):
    return booking(start)


TEN_AT_320 = "- 10:00 AM at 320 Example St (unit 320-03), 1 person"
FOUR_AT_412 = "- 4:00 PM at 412 Example St (unit 412-01), 1 person"
HEADER = "Sernia Capital: your showings today, Wed, Apr 14:"


async def morning_notice():
    (notice,) = await of_kind(MORNING)
    return notice


@pytest.mark.asyncio
async def test_production_first_unit_not_live_a_later_one_live_lists_only_the_live_one(
    world, monkeypatch
):
    ids = await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="live", live_units=[OTHER_UNIT])

    await run(world, at(8))
    assert morning_texts(world) == [f"{HEADER}\n{FOUR_AT_412}"]
    notice = await morning_notice()
    assert (notice.status, notice.guard_reason, notice.showing_id) == (
        "sent",
        "live_unit",
        ids[OTHER_UNIT],
    )
    assert notice.payload["showing_ids"] == [ids[OTHER_UNIT]]


@pytest.mark.asyncio
async def test_production_first_unit_live_a_later_one_not_lists_only_the_live_one(
    world, monkeypatch
):
    ids = await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="live", live_units=[UNIT])

    await run(world, at(8))
    assert morning_texts(world) == [f"{HEADER}\n{TEN_AT_320}"]
    notice = await morning_notice()
    assert (notice.status, notice.showing_id) == ("sent", ids[UNIT])


@pytest.mark.asyncio
async def test_production_no_unit_live_captures_the_whole_list(world, monkeypatch):
    ids = await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="live", live_units=[])

    await run(world, at(8))
    assert morning_texts(world) == []
    notice = await morning_notice()
    assert (notice.status, notice.guard_reason, notice.showing_id) == (
        "captured",
        "unit_not_live",
        ids[UNIT],
    )
    assert notice.payload["body"] == f"{HEADER}\n{TEN_AT_320}\n{FOUR_AT_412}"


@pytest.mark.asyncio
async def test_capture_mode_lists_every_showing(world, monkeypatch):
    await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="capture", live_units=[UNIT, OTHER_UNIT])

    await run(world, at(8))
    assert morning_texts(world) == []
    notice = await morning_notice()
    assert (notice.status, notice.guard_reason) == ("captured", "send_mode_capture")
    assert notice.payload["body"] == f"{HEADER}\n{TEN_AT_320}\n{FOUR_AT_412}"


@pytest.mark.asyncio
async def test_a_unit_taken_off_live_between_queueing_and_delivery(world, monkeypatch):
    ids = await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="live", live_units=[OTHER_UNIT])
    world.now = at(8)
    await daily_texts.queue_due(now=at(8))
    queued = await morning_notice()
    assert (queued.showing_id, queued.payload["body"]) == (
        ids[OTHER_UNIT],
        f"{HEADER}\n{FOUR_AT_412}",
    )

    await store_settings(send_mode="live", live_units=[])
    await run(world, at(8, 1))
    assert morning_texts(world) == []
    notice = await morning_notice()
    # Nothing live now: it lists every showing, on the first, and is captured.
    assert (notice.status, notice.guard_reason, notice.showing_id) == (
        "captured",
        "unit_not_live",
        ids[UNIT],
    )
    assert notice.payload["body"] == f"{HEADER}\n{TEN_AT_320}\n{FOUR_AT_412}"


@pytest.mark.asyncio
async def test_the_first_unit_taken_off_live_moves_the_text_to_the_one_still_live(
    world, monkeypatch
):
    ids = await two_units_booked(world, monkeypatch)
    await store_settings(send_mode="live", live_units=[UNIT, OTHER_UNIT])
    world.now = at(8)
    await daily_texts.queue_due(now=at(8))
    queued = await morning_notice()
    assert queued.showing_id == ids[UNIT]

    await store_settings(send_mode="live", live_units=[OTHER_UNIT])
    await run(world, at(8, 1))
    assert morning_texts(world) == [f"{HEADER}\n{FOUR_AT_412}"]
    notice = await morning_notice()
    assert (notice.status, notice.guard_reason, notice.showing_id) == (
        "sent",
        "live_unit",
        ids[OTHER_UNIT],
    )
