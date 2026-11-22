"""The residents' texts, from the routes to the rows they leave: a new showing at their
home, and its cancellation.

Each scenario books through the public routes, cancels through the public or
team routes (or a listing's off-market date through the rental listing
routes), runs the job's two steps as the clock moves, and checks the rows in
``showings`` and ``showing_notices`` and what the stand-in for Quo recorded.
Residents come from ``StandInContacts``: made-up Quo contacts, never the
real ones. Nothing is sent anywhere.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import RentalListing
from api.src.sernia_ai.config import QUO_INTERNAL_COMPANY
from api.src.showing_bookings import (
    bookings,
    cancelling,
    holds,
    open_times,
    outbox,
    quo_contacts,
    residents,
)
from api.src.showing_bookings.fakes import (
    RecordedText,
    RecordingTextSender,
    StandInContacts,
    lease_day,
    sample_contacts,
)
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import NotSent
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
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
    by_kind,
    install,
    rows,
)
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.tests.test_showing_cancelling import listing_app, team, team_app

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
THURSDAY = WEDNESDAY + timedelta(days=1)
RESIDENT = "+14125550142"
ROOMMATE = "+14125550143"
OUTSIDER = "+14125550144"  # a roommate who is not a test recipient
NEW_NUMBER = "+14125550146"  # a corrected mobile
NEW = residents.NEW_SHOWING_KIND
CANCELLED = residents.CANCELLED_KIND
CONFIRMATION = bookings.CONFIRMATION_KIND


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "SHOWING_TEST_PHONES", ",".join([ALEX_MOBILE, ROBIN_MOBILE, NEW_NUMBER, RESIDENT, ROOMMATE])
    )
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()
    state = PublicWorld()
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()


async def live() -> None:
    await store_settings(send_mode="test")


def lives_here(world: PublicWorld, name: str = "Morgan Ellis", mobile: str = RESIDENT, **fields):
    return world.residents.add(name, mobile, unit=UNIT, **fields)


async def book(world: PublicWorld, body: dict | None = None) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=body or booking())
    assert answer.status_code == 201, answer.text
    return answer.json()


def robin(start=None) -> dict:
    return booking(start, mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee")


async def cancel(world: PublicWorld, token: str) -> None:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token})
    assert answer.status_code == 200, answer.text


async def resident_notices() -> list[ShowingNotice]:
    _, _, notices = await rows()
    return [notice for notice in notices if notice.kind in residents.TEXT_KINDS]


async def one(kind: str) -> ShowingNotice:
    (notice,) = [notice for notice in await resident_notices() if notice.kind == kind]
    return notice


async def showing_row() -> Showing:
    showings, _, _ = await rows()
    (showing,) = showings
    return showing


# A new showing


@pytest.mark.asyncio
async def test_a_new_showing_texts_the_resident_once_the_confirmation_is_delivered(world):
    await live()
    lives_here(world)

    await book(world)

    _, _, notices = await rows()
    confirmation = by_kind(notices)[CONFIRMATION][0]
    text = await one(NEW)
    assert (text.status, text.recipient, text.recipient_key) == ("held", RESIDENT, "residents")
    assert (text.waits_for_notice_id, text.attendee_id) == (confirmation.id, None)
    assert text.payload["daytime_timezone"] == "America/New_York"
    assert "also_to" not in text.payload  # one resident: an ordinary text
    assert world.texts.to(RESIDENT) == []

    await world.send_pending()  # Quo reports the confirmation delivered

    (body,) = world.texts.to(RESIDENT)
    assert body == (
        "Hi, this is Sernia Capital. We've scheduled an in-person showing of your home at "
        "320 Example St (unit 320-03) on Wed, Apr 14 at 4:00 PM. It will take about 30 "
        "minutes, and a Sernia agent will be there the whole time. Questions? Reply to "
        "this number."
    )
    assert world.texts.groups() == []
    text = await one(NEW)
    assert (text.status, text.guard_reason) == ("sent", "test_recipient")


@pytest.mark.asyncio
async def test_an_undelivered_confirmation_stops_the_resident_text(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")

    await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))

    text = await one(NEW)
    assert (text.status, text.last_error) == ("cancelled", holds.UNDELIVERED_NOTE)
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_no_report_after_five_minutes_lets_the_resident_text_go(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "sent")

    await book(world)
    await world.send_pending(NOW + timedelta(minutes=4))
    assert (await one(NEW)).status == "held"

    await world.send_pending(NOW + timedelta(minutes=5))
    assert (await one(NEW)).status == "sent"
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_withdrawn_booking_stops_the_resident_text(world):
    await live()
    lives_here(world)
    world.texts.refuse(ALEX_MOBILE)

    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking())

    assert answer.status_code == 422
    showing = await showing_row()
    assert (showing.status, showing.cancel_reason) == ("cancelled", "no_attendees")
    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "cancelled")]
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_someone_joining_texts_the_resident_nothing_more(world):
    await live()
    lives_here(world)

    first = await book(world)
    joined = await book(world, robin())
    await world.send_pending()

    assert (first["kind"], joined["kind"]) == ("new", "join")
    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_the_resident_text_waits_for_someone_else_when_the_first_confirmation_fails(world):
    """Alex's confirmation is undelivered but Robin joined: the showing goes ahead, and
    the residents' text waits for Robin's confirmation instead."""
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    world.texts.report(ROBIN_MOBILE, "sent")

    await book(world)
    await book(world, robin())
    await world.send_pending(NOW + timedelta(minutes=1))

    _, attendees, notices = await rows()
    robin_confirmation = next(
        n for n in by_kind(notices)[CONFIRMATION] if n.attendee_id == attendees[1].id
    )
    text = await one(NEW)
    assert (text.status, text.waits_for_notice_id) == ("held", robin_confirmation.id)

    await world.send_pending(NOW + timedelta(minutes=6))  # no report for Robin's text

    assert (await one(NEW)).status == "sent"
    assert len(world.texts.to(RESIDENT)) == 1


# Daytime only: 8 AM to 9 PM in the showing rules' time zone


async def book_thursday_at(world: PublicWorld, moment: datetime) -> None:
    """Book Thursday 4:00 PM at ``moment``, in capture mode (the confirmation is
    captured at once), and run the job then."""
    lives_here(world)
    world.now = moment
    await book(world, booking(at(16, day=THURSDAY)))
    await world.send_pending(moment)


@pytest.mark.asyncio
async def test_a_resident_text_due_just_before_9_pm_goes(world):
    await book_thursday_at(world, at(20, 59, day=TUESDAY))

    assert (await one(NEW)).status == "captured"


@pytest.mark.asyncio
async def test_a_resident_text_due_at_9_pm_waits_for_8_am(world):
    await book_thursday_at(world, at(21, 0, day=TUESDAY))

    text = await one(NEW)
    assert (text.status, text.not_before) == ("pending", at(8, 0, day=WEDNESDAY))

    await world.send_pending(at(7, 59, day=WEDNESDAY))
    assert (await one(NEW)).status == "pending"

    await world.send_pending(at(8, 0, day=WEDNESDAY))
    assert (await one(NEW)).status == "captured"


@pytest.mark.asyncio
async def test_a_resident_text_due_after_midnight_waits_for_8_am_that_day(world):
    await book_thursday_at(world, at(23, 30, day=TUESDAY))
    assert (await one(NEW)).not_before == at(8, 0, day=WEDNESDAY)

    await world.send_pending(at(0, 30, day=WEDNESDAY))

    text = await one(NEW)
    assert (text.status, text.not_before) == ("pending", at(8, 0, day=WEDNESDAY))


@pytest.mark.asyncio
async def test_a_resident_text_due_before_8_am_waits_until_8(world):
    await book_thursday_at(world, at(7, 59, day=WEDNESDAY))

    text = await one(NEW)
    assert (text.status, text.not_before) == ("pending", at(8, 0, day=WEDNESDAY))


def test_daytime_edges_in_the_showing_time_zone():
    zone = "America/New_York"
    assert residents.next_daytime(at(8, 0), zone) is None
    assert residents.next_daytime(at(20, 59) + timedelta(seconds=59), zone) is None
    assert residents.next_daytime(at(21, 0), zone) == at(8, 0, day=THURSDAY)
    assert residents.next_daytime(at(7, 59), zone) == at(8, 0)
    # 2:30 AM UTC on Wednesday is 10:30 PM on Tuesday in New York: Wednesday 8 AM there.
    assert residents.next_daytime(datetime(2027, 4, 14, 2, 30, tzinfo=UTC), zone) == at(8)


# Who the residents are


@pytest.mark.asyncio
async def test_a_vacant_unit_gets_no_resident_text_and_no_mark(world):
    await book(world)
    await world.send_pending()

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention is None


@pytest.mark.asyncio
async def test_roommates_get_one_group_text(world):
    await live()
    lives_here(world, "Morgan Ellis", ROOMMATE)
    lives_here(world, "Riley Chen", RESIDENT)

    await book(world)
    await world.send_pending()

    text = await one(NEW)
    # One notice, the numbers in order: the first is the recipient, the rest share the thread.
    assert (text.recipient, text.payload["also_to"], text.status) == (
        RESIDENT,
        [ROOMMATE],
        "sent",
    )
    (group,) = world.texts.groups()
    assert group.group == (RESIDENT, ROOMMATE)
    assert len(world.texts.sent) == 2  # Alex's confirmation and the group text


@pytest.mark.asyncio
async def test_a_lease_that_ends_before_the_showing_day_is_not_a_resident(world):
    await live()
    lives_here(world, "Morgan Ellis", ROOMMATE, lease_end=lease_day(TUESDAY))
    lives_here(world, "Riley Chen", RESIDENT)

    await book(world)  # Wednesday

    text = await one(NEW)
    assert (text.recipient, text.payload.get("also_to")) == (RESIDENT, None)


@pytest.mark.asyncio
async def test_a_unit_whose_only_lease_ended_before_the_showing_day_is_vacant(world):
    lives_here(world, lease_start=lease_day(date(2026, 4, 15)), lease_end=lease_day(TUESDAY))

    await book(world)

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention is None


@pytest.mark.asyncio
async def test_a_unit_whose_only_tenant_moves_in_after_the_showing_day_is_vacant(world):
    lives_here(world, lease_start=lease_day(THURSDAY), lease_end=lease_day(date(2028, 4, 30)))

    await book(world)  # Wednesday

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dates",
    [
        {"lease_end": None},
        {"lease_start": None, "lease_end": None},
        {"lease_start": "not a date"},
    ],
    ids=["no end date", "no dates", "an unreadable start date"],
)
async def test_a_lone_contact_without_lease_dates_is_a_problem_not_a_vacancy(world, dates):
    lives_here(world, **dates)

    answer = await book(world)

    assert answer["kind"] == "new"
    assert await resident_notices() == []
    assert (await showing_row()).resident_attention == "lease_dates_missing"
    assert residents.PROBLEMS["lease_dates_missing"] == (
        "A contact at this unit has no lease dates: check who lives here and tell them yourself"
    )


@pytest.mark.asyncio
async def test_a_contact_without_lease_dates_beside_a_current_tenant_is_left_out(world):
    lives_here(world, "Morgan Ellis", ROOMMATE, lease_end=None)
    lives_here(world, "Riley Chen", RESIDENT)

    await book(world)

    text = await one(NEW)
    assert (text.recipient, text.payload.get("also_to")) == (RESIDENT, None)
    assert (await showing_row()).resident_attention is None


@pytest.mark.asyncio
async def test_an_internal_contact_without_lease_dates_does_not_mark_the_unit(world):
    lives_here(world, "Pat Example", ROOMMATE, company=QUO_INTERNAL_COMPANY, lease_end=None)

    await book(world)

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention is None


@pytest.mark.asyncio
async def test_a_lease_starting_on_the_showing_day_counts(world):
    lives_here(world, lease_start=lease_day(WEDNESDAY), lease_end=lease_day(date(2028, 4, 30)))

    await book(world)

    assert (await one(NEW)).recipient == RESIDENT


@pytest.mark.asyncio
async def test_an_internal_contact_at_the_unit_is_left_out(world):
    lives_here(world, "Pat Example", ROOMMATE, company=QUO_INTERNAL_COMPANY)
    lives_here(world, "Riley Chen", RESIDENT)

    await book(world)

    text = await one(NEW)
    assert (text.recipient, text.payload.get("also_to")) == (RESIDENT, None)


@pytest.mark.asyncio
async def test_a_contact_at_another_unit_of_the_building_is_left_out(world):
    world.residents.add("Avery Quinn", ROOMMATE, unit="320-05")

    await book(world)

    assert await resident_notices() == []


# A lookup that goes wrong marks the showing; the booking stands


async def showings_view(world: PublicWorld) -> dict:
    async with world.client(team_app(world)) as client:
        answer = await client.get(f"{TEAM}/showings")
    assert answer.status_code == 200, answer.text
    (showing,) = answer.json()["booked"]
    return showing


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arrange", "problem"),
    [
        (lambda world: world.residents.fail(), "lookup_failed"),
        (
            lambda world: world.residents.add("Morgan Ellis", RESIDENT, unit="320-3"),
            "unit_spelled_differently",
        ),
        (lambda world: lives_here(world, mobile=None), "resident_without_mobile"),
    ],
    ids=["quo out of reach", "unit spelled differently", "no mobile"],
)
async def test_a_lookup_that_goes_wrong_marks_the_showing_and_the_booking_stands(
    world, arrange, problem
):
    arrange(world)

    answer = await book(world)

    assert answer["kind"] == "new"
    assert await resident_notices() == []
    assert (await showing_row()).resident_attention == problem
    assert (await showings_view(world))["resident_attention"] == problem
    await world.send_pending()
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_too_many_residents_for_one_group_text_marks_the_showing(world):
    for index in range(11):
        lives_here(world, f"Resident {index}", f"+1412555{160 + index:04d}")

    await book(world)

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention == "too_many_residents"


@pytest.mark.asyncio
async def test_a_unit_key_without_a_property_marks_the_showing(world):
    world.listing = RentalListing(unit="LOFT", address="1 Loft Way")

    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/LOFT/bookings", json=booking())

    assert answer.status_code == 201, answer.text
    assert (await showing_row()).resident_attention == "unit_key_unreadable"


@pytest.mark.asyncio
async def test_a_joined_showing_keeps_its_mark_and_texts_no_one(world):
    world.residents.fail()
    await book(world)
    world.residents = StandInContacts()
    lives_here(world)

    await book(world, robin())

    assert await resident_notices() == []
    assert (await showing_row()).resident_attention == "lookup_failed"


# Cancelling


async def sent_first(world: PublicWorld) -> dict:
    """Alex books with a resident at the unit, and the residents' text goes out."""
    await live()
    lives_here(world)
    alex = await book(world)
    await world.send_pending()
    assert (await one(NEW)).status == "sent"
    return alex


async def told_of_the_cancellation(world: PublicWorld) -> None:
    text = await one(CANCELLED)
    assert (text.status, text.recipient, text.attendee_id) == ("pending", RESIDENT, None)
    await world.send_pending()
    first, second = world.texts.to(RESIDENT)
    assert second == (
        "Hi, this is Sernia Capital. The showing of your home at 320 Example St (unit "
        "320-03) on Wed, Apr 14 at 4:00 PM is cancelled, so no one will come by for it. "
        "Questions? Reply to this number."
    )
    assert (await one(CANCELLED)).status == "sent"


@pytest.mark.asyncio
async def test_the_last_prospect_cancelling_texts_the_resident(world):
    alex = await sent_first(world)

    await cancel(world, alex["cancel_token"])

    await told_of_the_cancellation(world)


@pytest.mark.asyncio
async def test_a_prospect_cancelling_with_others_still_booked_texts_the_resident_nothing(world):
    alex = await sent_first(world)
    await book(world, robin())

    await cancel(world, alex["cancel_token"])
    await world.send_pending()

    assert [n.kind for n in await resident_notices()] == [NEW]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_the_team_cancelling_texts_the_resident(world):
    await sent_first(world)
    showing = await showing_row()

    assert (await team(world, f"/showings/{showing.id}/cancel")).status_code == 200

    await told_of_the_cancellation(world)


@pytest.mark.asyncio
async def test_the_team_removing_the_last_person_texts_the_resident(world):
    await sent_first(world)
    showing = await showing_row()
    _, attendees, _ = await rows()

    answer = await team(world, f"/showings/{showing.id}/attendees/{attendees[0].id}/remove")

    assert answer.status_code == 200
    await told_of_the_cancellation(world)


@pytest.mark.asyncio
async def test_the_unit_going_off_the_market_texts_the_resident(world):
    async with AsyncSessionFactory() as session:
        await listings_service.save_listing(session, world.listing, changed_by="test")
    await sent_first(world)

    changed = world.listing.model_copy(update={"off_market_on": WEDNESDAY})
    agreed = {"showings": 1, "people": 1, "people_texted": 1}
    async with world.client(listing_app()) as client:
        saved = await client.put(
            f"/api/rental-listings/listings/{UNIT}",
            json={"listing": changed.model_dump(mode="json"), "confirm_cancellations": agreed},
        )

    assert saved.status_code == 200, saved.text
    assert (await showing_row()).cancel_reason == "off_market"
    await told_of_the_cancellation(world)


@pytest.mark.asyncio
async def test_a_withdrawn_booking_that_empties_a_told_showing_texts_the_resident(world):
    """Robin's number is refused after Alex cancelled in between: the showing is left
    empty, and the residents were already told of it."""
    alex = await sent_first(world)
    await book(world, robin())
    await cancel(world, alex["cancel_token"])
    _, attendees, notices = await rows()
    robin_row = attendees[1]
    robin_confirmation = next(
        n for n in by_kind(notices)[CONFIRMATION] if n.attendee_id == robin_row.id
    )

    await bookings.withdraw(robin_row.id, robin_confirmation.id, now=world.now)

    showing = await showing_row()
    assert (showing.status, showing.cancel_reason) == ("cancelled", "no_attendees")
    await told_of_the_cancellation(world)


@pytest.mark.asyncio
async def test_a_cancellation_while_the_resident_text_is_held_stops_it_and_says_nothing(world):
    await live()
    lives_here(world)
    alex = await book(world)

    await cancel(world, alex["cancel_token"])
    await world.send_pending()

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "cancelled")]
    assert world.texts.to(RESIDENT) == []


@pytest.mark.asyncio
async def test_a_cancellation_while_the_resident_text_waits_for_8_am_stops_it(world):
    lives_here(world)
    world.now = at(22, 0, day=TUESDAY)
    alex = await book(world, booking(at(16, day=THURSDAY)))
    await world.send_pending()
    assert (await one(NEW)).status == "pending"

    await cancel(world, alex["cancel_token"])
    await world.send_pending(at(8, 0, day=WEDNESDAY))

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "cancelled")]


@pytest.mark.asyncio
async def test_a_cancellation_after_the_resident_text_was_stopped_says_nothing(world):
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    alex = await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    showing = await showing_row()

    await team(world, f"/showings/{showing.id}/cancel")
    await world.send_pending()

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "cancelled")]
    assert world.texts.to(RESIDENT) == []
    assert alex["kind"] == "new"


@pytest.mark.asyncio
async def test_a_cancellation_while_the_resident_text_is_being_sent_tells_them(world):
    await live()
    lives_here(world)
    alex = await book(world)
    await holds.release_held(now=world.now, texts=world.texts)
    text = await one(NEW)
    async with AsyncSessionFactory() as session, session.begin():
        # A run claimed it and is calling Quo now.
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id == text.id)
            .values(sending_started_at=world.now)
        )

    await cancel(world, alex["cancel_token"])

    assert (await one(CANCELLED)).status == "pending"
    assert (await one(NEW)).status == "pending"  # left to finish


class GatedTexts(RecordingTextSender):
    """Holds a text to the resident inside the provider call until the test lets it go,
    then sends it or has Quo refuse it."""

    def __init__(self) -> None:
        super().__init__()
        self.inside = asyncio.Event()
        self.go = asyncio.Event()
        self.refuse_resident = False

    async def send_text(self, to: str, body: str):
        if to == RESIDENT:
            if self.inside.is_set() and not self.go.is_set():
                # Recorded, so the test sees the order a resident would have.
                self.sent.append(RecordedText(to=to, body=body, provider_id="ACoverlap"))
                raise AssertionError("A second text reached the resident during the first.")
            self.inside.set()
            await self.go.wait()
            if self.refuse_resident:
                raise NotSent("opted_out", "User Opted Out")
        return await super().send_text(to, body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refused", [False, True], ids=["announcement sent", "announcement refused"]
)
async def test_the_cancellation_never_overtakes_an_announcement_being_sent(world, refused):
    """Run 1 is calling Quo with the announcement when the showing is cancelled; run 2
    (the job twice at once) must not send the cancellation first."""
    gated = GatedTexts()
    gated.refuse_resident = refused
    world.texts = gated
    await live()
    lives_here(world)
    alex = await book(world)
    await holds.release_held(now=world.now, texts=gated)  # the announcement is due

    first_run = asyncio.create_task(
        outbox.drain(now=world.now, texts=gated, calendar=world.calendar)
    )
    await asyncio.wait_for(gated.inside.wait(), timeout=10)
    await cancel(world, alex["cancel_token"])
    assert (await one(CANCELLED)).status == "pending"

    # The second run, while run 1 waits on Quo.
    await asyncio.wait_for(
        outbox.drain(now=world.now, texts=gated, calendar=world.calendar), timeout=10
    )

    waiting = await one(CANCELLED)
    assert (waiting.status, waiting.not_before) == ("pending", world.now + timedelta(minutes=1))
    assert gated.to(RESIDENT) == []

    gated.go.set()
    await first_run
    await world.send_pending(world.now + timedelta(minutes=1))

    told, cancelled = await one(NEW), await one(CANCELLED)
    if refused:
        assert told.status == "failed"
        assert (cancelled.status, cancelled.last_error) == (
            "cancelled",
            residents.NEVER_TOLD_NOTE,
        )
        assert gated.to(RESIDENT) == []
    else:
        assert (told.status, cancelled.status) == ("sent", "sent")
        scheduled, then_cancelled = gated.to(RESIDENT)
        assert "We've scheduled" in scheduled and "is cancelled" in then_cancelled


@pytest.mark.asyncio
async def test_the_hand_over_picks_the_current_confirmation_after_a_corrected_mobile(world):
    """Robin's first text was undelivered and Robin corrected the number; when Alex's
    text then fails, the residents' text waits for Robin's replacement, not the
    undelivered first one."""
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "sent")  # no final report yet
    world.texts.report(ROBIN_MOBILE, "undelivered")
    await book(world)
    robin_booking = await book(world, robin())
    await world.send_pending(NOW + timedelta(minutes=1))
    _, attendees, _ = await rows()
    assert attendees[1].attention_reason == "confirmation_undelivered"

    world.now = NOW + timedelta(minutes=2)
    async with world.client() as client:
        corrected = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": robin_booking["cancel_token"], "mobile": NEW_NUMBER},
        )
    assert corrected.status_code == 200, corrected.text
    _, attendees, notices = await rows()
    assert (attendees[1].mobile, attendees[1].attention_reason) == (NEW_NUMBER, None)
    replacement = next(n for n in by_kind(notices)[CONFIRMATION] if n.recipient == NEW_NUMBER)

    world.texts.report(ALEX_MOBILE, "undelivered")
    await world.send_pending(NOW + timedelta(minutes=3))

    assert (await one(NEW)).waits_for_notice_id == replacement.id
    await world.send_pending(NOW + timedelta(minutes=4))

    _, attendees, _ = await rows()
    assert attendees[1].attention_reason is None
    assert (await one(NEW)).status == "sent"
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_the_hand_over_skips_a_correction_still_being_sent(world):
    """A correction whose text has not come back yet is not the person's number yet:
    the residents' text waits for the text to the number on the booking."""
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    await book(world)
    robin_booking = await book(world, robin())
    _, attendees, notices = await rows()
    robin_first = next(
        n for n in by_kind(notices)[CONFIRMATION] if n.attendee_id == attendees[1].id
    )
    async with AsyncSessionFactory() as session, session.begin():
        # A correction under way: its confirmation is queued, the booking keeps its number.
        session.add(
            ShowingNotice(
                kind=CONFIRMATION,
                channel="sms",
                showing_id=robin_first.showing_id,
                attendee_id=attendees[1].id,
                recipient=NEW_NUMBER,
                recipient_key=f"attendee:{attendees[1].id}:correction:1",
                payload={"body": "Booked."},
                status="pending",
                not_before=world.now,
                sending_started_at=world.now,
            )
        )
    assert robin_booking["kind"] == "join"

    await holds.release_held(now=NOW + timedelta(minutes=1), texts=world.texts)

    assert (await one(NEW)).waits_for_notice_id == robin_first.id


# An announcement stopped with a failed confirmation comes back


async def stopped_alone(world: PublicWorld) -> dict:
    """Alex books alone, the confirmation is reported undelivered, and the residents'
    text is stopped while the showing stays scheduled."""
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    alex = await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    text = await one(NEW)
    assert (text.status, text.last_error) == ("cancelled", holds.UNDELIVERED_NOTE)
    assert (await showing_row()).status == "scheduled"
    world.now = NOW + timedelta(minutes=2)
    return alex


@pytest.mark.asyncio
async def test_a_join_brings_back_an_announcement_stopped_by_an_undelivered_confirmation(world):
    await stopped_alone(world)

    await book(world, robin())

    _, attendees, notices = await rows()
    robin_confirmation = next(
        n for n in by_kind(notices)[CONFIRMATION] if n.attendee_id == attendees[1].id
    )
    text = await one(NEW)
    assert (text.status, text.waits_for_notice_id, text.last_error) == (
        "held",
        robin_confirmation.id,
        None,
    )

    await world.send_pending(NOW + timedelta(minutes=3))

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_corrected_mobile_brings_back_an_announcement_stopped_by_its_first_text(world):
    alex = await stopped_alone(world)

    async with world.client() as client:
        corrected = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": alex["cancel_token"], "mobile": NEW_NUMBER},
        )

    assert corrected.status_code == 200, corrected.text
    _, _, notices = await rows()
    replacement = next(n for n in by_kind(notices)[CONFIRMATION] if n.recipient == NEW_NUMBER)
    text = await one(NEW)
    assert (text.status, text.waits_for_notice_id) == ("held", replacement.id)

    await world.send_pending(NOW + timedelta(minutes=3))

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_correction_after_a_join_brought_it_back_makes_no_second_announcement(world):
    alex = await stopped_alone(world)
    await book(world, robin())
    async with world.client() as client:
        corrected = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": alex["cancel_token"], "mobile": NEW_NUMBER},
        )
    assert corrected.status_code == 200, corrected.text

    await world.send_pending(NOW + timedelta(minutes=3))
    await world.send_pending(NOW + timedelta(minutes=4))

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_correction_brings_back_an_announcement_handed_over_and_stopped(world):
    """Alex's text fails and the announcement is handed to Robin; Robin's then fails
    too, so it is stopped behind Robin's text. Alex corrects their mobile: the
    announcement comes back behind Alex's new text, though it last waited on Robin's."""
    await live()
    lives_here(world)
    world.texts.report(ALEX_MOBILE, "undelivered")
    world.texts.report(ROBIN_MOBILE, "sent")  # no final report yet
    alex = await book(world)
    await book(world, robin())
    await world.send_pending(NOW + timedelta(minutes=1))
    _, attendees, notices = await rows()
    robin_confirmation = next(
        n for n in by_kind(notices)[CONFIRMATION] if n.attendee_id == attendees[1].id
    )
    assert (await one(NEW)).waits_for_notice_id == robin_confirmation.id

    world.texts.report(ROBIN_MOBILE, "undelivered")
    await world.send_pending(NOW + timedelta(minutes=2))
    stopped = await one(NEW)
    assert (stopped.status, stopped.waits_for_notice_id) == ("cancelled", robin_confirmation.id)
    assert (await showing_row()).status == "scheduled"

    world.now = NOW + timedelta(minutes=3)
    async with world.client() as client:
        corrected = await client.post(
            f"{PUBLIC}/bookings/mobile",
            json={"cancel_token": alex["cancel_token"], "mobile": NEW_NUMBER},
        )
    assert corrected.status_code == 200, corrected.text
    _, _, notices = await rows()
    replacement = next(n for n in by_kind(notices)[CONFIRMATION] if n.recipient == NEW_NUMBER)
    text = await one(NEW)
    assert (text.status, text.waits_for_notice_id) == ("held", replacement.id)

    await world.send_pending(NOW + timedelta(minutes=4))

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_join_after_the_announcement_went_out_sends_nothing_new(world):
    await sent_first(world)

    await book(world, robin())
    await world.send_pending()

    assert [(n.kind, n.status) for n in await resident_notices()] == [(NEW, "sent")]
    assert len(world.texts.to(RESIDENT)) == 1


@pytest.mark.asyncio
async def test_a_join_does_not_bring_back_an_announcement_stopped_for_another_reason(world):
    """An announcement cancelled at delivery (here: the showing's time had passed) is
    not one stopped with a failed confirmation, so a join leaves it alone."""
    await live()
    lives_here(world)
    await book(world)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == NEW)
            .values(status="cancelled", last_error=residents.TOO_LATE_NOTE)
        )

    await book(world, robin())

    text = await one(NEW)
    assert (text.status, text.last_error) == ("cancelled", residents.TOO_LATE_NOTE)


@pytest.mark.asyncio
async def test_a_cancellation_text_due_at_night_waits_for_8_am(world):
    alex = await sent_first(world)
    world.now = at(22, 0, day=TUESDAY)

    await cancel(world, alex["cancel_token"])
    await world.send_pending()

    text = await one(CANCELLED)
    assert (text.status, text.not_before) == ("pending", at(8, 0, day=WEDNESDAY))
    await world.send_pending(at(8, 0, day=WEDNESDAY))
    assert (await one(CANCELLED)).status == "sent"


@pytest.mark.asyncio
async def test_roommates_get_the_cancellation_in_their_group_thread(world):
    await live()
    lives_here(world, "Morgan Ellis", ROOMMATE)
    lives_here(world, "Riley Chen", RESIDENT)
    alex = await book(world)
    await world.send_pending()

    await cancel(world, alex["cancel_token"])
    await world.send_pending()

    first, second = world.texts.groups()
    assert first.group == second.group == (RESIDENT, ROOMMATE)
    assert "is cancelled" in second.body


# Send modes


@pytest.mark.asyncio
async def test_capture_mode_keeps_the_resident_texts_and_sends_nothing(world):
    lives_here(world, "Morgan Ellis", ROOMMATE)
    lives_here(world, "Riley Chen", RESIDENT)
    alex = await book(world)
    await world.send_pending()
    await cancel(world, alex["cancel_token"])
    await world.send_pending()

    assert [(n.kind, n.status, n.guard_reason) for n in await resident_notices()] == [
        (NEW, "captured", "send_mode_capture"),
        (CANCELLED, "captured", "send_mode_capture"),
    ]
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_test_mode_captures_a_group_text_with_one_roommate_not_a_test_recipient(world):
    await live()
    lives_here(world, "Morgan Ellis", OUTSIDER)
    lives_here(world, "Riley Chen", RESIDENT)

    await book(world)
    await world.send_pending()

    text = await one(NEW)
    assert (text.status, text.guard_reason) == ("captured", "not_test_recipient")
    assert world.texts.to(RESIDENT) == world.texts.to(OUTSIDER) == []
    assert len(world.texts.to(ALEX_MOBILE)) == 1  # the confirmation, to a test recipient


@pytest.mark.asyncio
async def test_a_resident_text_whose_showing_was_cancelled_is_never_sent(world):
    """A released text whose showing is cancelled by a path that leaves it pending."""
    await live()
    lives_here(world)
    await book(world)
    await holds.release_held(now=world.now, texts=world.texts)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(Showing).values(status="cancelled", cancel_reason="team"))

    await world.send_pending()

    text = await one(NEW)
    assert (text.status, text.last_error) == ("cancelled", residents.SHOWING_CANCELLED_NOTE)
    assert world.texts.to(RESIDENT) == []


# The team's lists, and nothing public


@pytest.mark.asyncio
async def test_the_messages_list_shows_the_resident_texts(world):
    lives_here(world, "Morgan Ellis", ROOMMATE)
    lives_here(world, "Riley Chen", RESIDENT)
    await book(world)
    await world.send_pending()

    async with world.client(team_app(world)) as client:
        answer = await client.get(f"{TEAM}/notices")

    found = [notice for notice in answer.json() if notice["kind"] == NEW]
    (notice,) = found
    assert notice["recipient"] == RESIDENT
    assert notice["payload"]["also_to"] == [ROOMMATE]
    assert "Sernia agent will be there" in notice["wording"]
    assert (await showings_view(world))["resident_attention"] is None


@pytest.mark.asyncio
async def test_no_public_answer_says_anything_about_residents(world):
    await live()
    lives_here(world, "Morgan Ellis", ROOMMATE)
    lives_here(world, "Riley Chen", RESIDENT)

    async with world.client() as client:
        answers = [
            await client.get(f"{PUBLIC}/units"),
            await client.get(f"{PUBLIC}/units/{UNIT}"),
            await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking()),
        ]
        token = answers[-1].json()["cancel_token"]
        await world.send_pending()
        answers.append(await client.post(f"{PUBLIC}/bookings/lookup", json={"cancel_token": token}))
        answers.append(await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token}))

    for answer in answers:
        assert answer.status_code in (200, 201), answer.text
        text = answer.text.lower()
        for secret in ("4125550142", "4125550143", "0142", "0143", "morgan", "riley", "resident"):
            assert secret not in text, (answer.request.url, secret)


# The lookup itself


def test_the_lookup_reads_the_shared_tenant_rules():
    day = WEDNESDAY
    world = StandInContacts()
    world.add("Riley Chen", "(412) 555-0143", unit=UNIT)
    world.add("Morgan Ellis", "+14125550142", unit=UNIT)
    world.add("Morgan Ellis", "+14125550142", unit=UNIT)  # the same person twice
    world.add("Pat Example", "+14125550144", unit=UNIT, company=QUO_INTERNAL_COMPANY)
    world.add("Avery Quinn", "+14125550145", unit=UNIT, lease_start=None)

    found = residents.residents_in(world.added, UNIT, day)

    assert found == residents.Found(mobiles=(RESIDENT, ROOMMATE))


@pytest.mark.asyncio
async def test_outside_production_the_lookup_answers_made_up_residents(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    source = residents.contact_source()

    found = await residents.find(UNIT, WEDNESDAY, source=source)

    assert isinstance(source, StandInContacts)
    assert found.problem is None
    assert len(found.mobiles) == len(sample_contacts(UNIT)) == 2
    assert all(m.startswith("+1412555") and 160 <= int(m[-4:]) <= 199 for m in found.mobiles)
    # The same unit always has the same made-up residents; some units are vacant.
    assert sample_contacts(UNIT) == sample_contacts(UNIT)
    counts = {len(sample_contacts(f"{n}-01")) for n in range(300, 310)}
    assert counts == {0, 1, 2}


@pytest.mark.asyncio
async def test_in_production_the_lookup_reads_quo_and_never_in_tests(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    source = residents.contact_source()

    assert isinstance(source, quo_contacts.QuoContacts)
    with pytest.raises(quo_contacts.LiveQuoInTests):
        await residents.find(UNIT, WEDNESDAY, source=source)


@pytest.mark.asyncio
async def test_no_resident_text_reaches_the_attendee_rows(world):
    """Residents live on the notice row only, never on a booking."""
    lives_here(world)
    await book(world)

    async with AsyncSessionFactory() as session:
        mobiles = list(await session.scalars(select(ShowingAttendee.mobile)))
    assert mobiles == [ALEX_MOBILE]
