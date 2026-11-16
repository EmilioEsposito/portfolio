"""Bookings that race, and the host's event built when it is delivered.

- Every public booking takes one shared lock first, so two bookings at
  different buildings at adjacent times never both get one host inside the
  travel gap.
- The host's event is rendered from the showing as it is when its notice is
  delivered, and created or updated then: one event per showing whatever
  order the notices are released in, listing the people booked now, with
  their current mobiles.

Uses the test database and the stand-ins in ``showing_public.py``. Nothing is sent.
"""

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import bookings, open_times
from api.src.showing_bookings.fakes import InMemoryCalendar, RecordingTextSender
from api.src.showing_bookings.models import ShowingAgentRecord, ShowingNotice
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
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
)

PUBLIC = "/api/showing-bookings/public"
OTHER_UNIT = "412-01"  # another building
SAM = "sam.rivera@example.com"


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    state = PublicWorld()
    state.other_listings = [RentalListing(unit=OTHER_UNIT, address="9 Other Rd")]
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def book(world: PublicWorld, body: dict, unit: str = UNIT):
    async with world.client() as client:
        return await client.post(f"{PUBLIC}/units/{unit}/bookings", json=body)


async def correct(world: PublicWorld, token: str, mobile: str):
    async with world.client() as client:
        return await client.post(
            f"{PUBLIC}/bookings/mobile", json={"cancel_token": token, "mobile": mobile}
        )


def robin(**changes) -> dict:
    return booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee", **changes)


def host_events(world: PublicWorld) -> list:
    return [event for event in world.calendar.events.values() if event.draft.attendees == [JANE]]


async def live() -> None:
    await store_settings(send_mode="live", test_sends_enabled=True)


# One booking at a time


@pytest.mark.parametrize("second_agent", [False, True])
@pytest.mark.asyncio
async def test_two_buildings_at_adjacent_times_never_share_a_host_inside_the_travel_gap(
    world, second_agent
):
    if second_agent:
        async with AsyncSessionFactory() as session:
            session.add(
                ShowingAgentRecord(
                    name="Sam Rivera", email=SAM, busy_calendar_ids=[SAM], priority=2
                )
            )
            await session.commit()

    answers = await asyncio.gather(
        book(world, booking(at(16))),
        book(world, robin(start=at(16, 30).isoformat()), unit=OTHER_UNIT),
    )

    showings, _, _ = await rows()
    hosts = {(showing.unit, showing.agent_id) for showing in showings}
    if second_agent:
        assert [answer.status_code for answer in answers] == [201, 201]
        assert len({agent for _, agent in hosts}) == 2
    else:
        assert sorted(answer.status_code for answer in answers) == [201, 409]
        refused = next(answer for answer in answers if answer.status_code == 409)
        assert refused.json()["detail"]["code"] == "time_gone"
        assert len(showings) == 1 and showings[0].agent_id == world.agent_id


# The host's event, rendered when delivered


@pytest.mark.asyncio
async def test_a_join_released_before_the_first_create_still_makes_one_event(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")  # no report yet for Alex's text
    await book(world, booking())
    await book(world, robin())

    # Robin's text is delivered first: the update is released before Alex's create.
    await world.send_pending(NOW + timedelta(minutes=1))
    (event,) = host_events(world)
    assert "Alex Parker" in event.draft.description and "Robin Lee" in event.draft.description
    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert kinds["host_event"][0].status == "held"
    assert kinds["host_event_update"][0].payload["operation"] == "create"

    # Five minutes on, Alex's create goes out as an update of the same event.
    await world.send_pending(NOW + timedelta(minutes=5))
    assert host_events(world) == [event]
    _, _, notices = await rows()
    create = by_kind(notices)["host_event"][0]
    assert (create.status, create.payload["operation"]) == ("sent", "update")
    assert ("update", event.calendar_id, event.event_id) in world.calendar.log


@pytest.mark.asyncio
async def test_an_undelivered_first_text_then_a_join_gives_the_host_one_event(world):
    await live()
    world.texts.report(ALEX_MOBILE, "undelivered")
    await book(world, booking())
    await world.send_pending(NOW + timedelta(minutes=1))
    assert host_events(world) == []

    await book(world, robin())
    await world.send_pending(NOW + timedelta(minutes=2))

    (event,) = host_events(world)
    # Alex is still booked (and marked for the team), so the host sees both.
    assert "Alex Parker" in event.draft.description and "Robin Lee" in event.draft.description


@pytest.mark.asyncio
async def test_a_corrected_mobile_shows_in_a_host_event_still_held(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")
    token = (await book(world, booking())).json()["cancel_token"]

    assert (await correct(world, token, CASEY_MOBILE)).status_code == 200
    await world.send_pending(NOW + timedelta(minutes=1))

    (event,) = host_events(world)
    assert "412-555-0119" in event.draft.description
    assert "412-555-0117" not in event.draft.description


@pytest.mark.asyncio
async def test_a_corrected_mobile_shows_in_a_host_event_already_sent(world):
    await live()
    token = (await book(world, booking())).json()["cancel_token"]
    await world.send_pending(NOW + timedelta(minutes=1))
    (event,) = host_events(world)
    assert "412-555-0117" in event.draft.description

    assert (await correct(world, token, CASEY_MOBILE)).status_code == 200
    _, _, notices = await rows()
    (queued,) = [n for n in notices if n.kind == "host_event_update"]
    assert queued.status == "held"
    await world.send_pending(NOW + timedelta(minutes=2))

    assert host_events(world) == [event]
    assert "412-555-0119" in event.draft.description
    assert "412-555-0117" not in event.draft.description


@pytest.mark.asyncio
async def test_a_withdrawn_first_attendee_is_not_listed(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")
    await book(world, booking())
    await book(world, robin())
    _, attendees, notices = await rows()
    alex_text = next(n for n in notices if n.recipient == ALEX_MOBILE)
    await bookings.withdraw(attendees[0].id, alex_text.id, now=NOW)

    await world.send_pending(NOW + timedelta(minutes=10))

    (event,) = host_events(world)
    assert "Robin Lee" in event.draft.description
    assert "Alex Parker" not in event.draft.description


@pytest.mark.asyncio
async def test_a_host_notice_for_a_showing_with_no_one_left_is_not_sent(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")
    await book(world, booking())
    _, attendees, notices = await rows()
    # Released, then the booking is withdrawn before the drain reaches it.
    async with AsyncSessionFactory() as session:
        for notice in await session.scalars(select(ShowingNotice)):
            if notice.status == "held":
                notice.status = "pending"
        await session.commit()
    await bookings.withdraw(attendees[0].id, -1, now=NOW)

    await world.send_pending(NOW + timedelta(minutes=1))

    assert host_events(world) == []
    _, _, notices = await rows()
    host = by_kind(notices)["host_event"][0]
    assert host.status == "cancelled" and "no one booked" in host.last_error


# Corrections that race


@pytest.mark.asyncio
async def test_two_corrections_to_one_number_at_the_same_moment(world):
    await store_settings(public_limits={"upcoming_per_mobile": 1})
    alex = (await book(world, booking(at(16)))).json()["cancel_token"]
    robin_token = (await book(world, robin(start=at(17).isoformat()))).json()["cancel_token"]

    answers = await asyncio.gather(
        correct(world, alex, CASEY_MOBILE), correct(world, robin_token, CASEY_MOBILE)
    )

    assert sorted(answer.status_code for answer in answers) == [200, 429]
    refused = next(answer for answer in answers if answer.status_code == 429)
    assert refused.json()["detail"]["code"] == "limit_mobile"
    _, attendees, _ = await rows()
    # One took the number; the other kept its own.
    assert [a.mobile for a in attendees].count(CASEY_MOBILE) == 1
    assert {a.mobile for a in attendees} - {CASEY_MOBILE} <= {ALEX_MOBILE, ROBIN_MOBILE}


@pytest.mark.asyncio
async def test_two_corrections_into_one_showing_to_one_number(world):
    alex = (await book(world, booking())).json()["cancel_token"]
    robin_token = (await book(world, robin())).json()["cancel_token"]

    answers = await asyncio.gather(
        correct(world, alex, CASEY_MOBILE), correct(world, robin_token, CASEY_MOBILE)
    )

    assert sorted(answer.status_code for answer in answers) == [200, 409]
    _, attendees, _ = await rows()
    assert [a.mobile for a in attendees].count(CASEY_MOBILE) == 1


# A withdrawal after the host's event listed the withdrawn person


class SenderWithAHook(RecordingTextSender):
    """Runs ``hook`` once, just before the first text to ``number`` is answered."""

    def __init__(self, number: str, hook) -> None:
        super().__init__()
        self.number, self.hook = number, hook

    async def send_text(self, to: str, body: str):
        if to == self.number and self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()
        return await super().send_text(to, body)


class CalendarWithAHook(InMemoryCalendar):
    """Runs ``hook`` once, in the middle of the first event it creates."""

    def __init__(self, hook=None) -> None:
        super().__init__()
        self.hook = hook

    async def create_event(self, calendar_id, event):
        if self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()
        return await super().create_event(calendar_id, event)


@pytest.mark.asyncio
async def test_a_number_refused_after_the_event_listed_it_updates_the_event(world):
    """Codex's sequence: the first attendee's event goes out while the second
    one's text is being sent, listing both; then Quo refuses the second number."""
    await live()

    async def deliver_the_first_event():
        await world.send_pending(NOW + timedelta(minutes=1))

    world.texts = SenderWithAHook(ROBIN_MOBILE, deliver_the_first_event)
    world.texts.refuse(ROBIN_MOBILE, "invalid_number")
    await book(world, booking())
    assert host_events(world) == []  # Alex's event waits for the next run

    refused = await book(world, robin())

    assert refused.status_code == 422
    (event,) = host_events(world)
    _, attendees, notices = await rows()
    assert [a.status for a in attendees] == ["booked", "cancelled"]
    (queued,) = [n for n in notices if n.recipient_key.endswith(":withdrawn")]
    assert (queued.kind, queued.status, queued.waits_for_notice_id) == (
        "host_event_update",
        "pending",
        None,
    )
    assert "Robin Lee" in event.draft.description  # what the host saw first

    await world.send_pending(NOW + timedelta(minutes=2))

    assert host_events(world) == [event]
    assert "Alex Parker" in event.draft.description
    assert "Robin Lee" not in event.draft.description
    _, _, notices = await rows()
    updates = [n for n in notices if n.kind == "host_event_update" and n.status == "sent"]
    assert len(updates) == 1 and updates[0].payload["operation"] == "update"


@pytest.mark.asyncio
async def test_a_withdrawal_while_the_event_is_being_sent_updates_it_afterwards(world):
    await live()
    world.texts.report(ROBIN_MOBILE, "sent")  # Robin's own notices stay held
    await book(world, booking())
    await book(world, robin())
    _, attendees, notices = await rows()
    robin_text = next(n for n in notices if n.recipient == ROBIN_MOBILE)

    async def withdraw_robin():
        await bookings.withdraw(attendees[1].id, robin_text.id, now=NOW + timedelta(minutes=1))

    world.calendar = CalendarWithAHook(withdraw_robin)

    # Alex's event is created listing both; Robin is withdrawn mid-send.
    await world.send_pending(NOW + timedelta(minutes=1))
    (event,) = host_events(world)
    assert "Robin Lee" in event.draft.description
    _, _, notices = await rows()
    (queued,) = [n for n in notices if n.recipient_key.endswith(":withdrawn")]
    assert queued.status == "pending"

    await world.send_pending(NOW + timedelta(minutes=2))

    assert host_events(world) == [event]
    assert "Robin Lee" not in event.draft.description
    assert "Alex Parker" in event.draft.description


@pytest.mark.asyncio
async def test_a_withdrawal_before_any_host_notice_went_out_queues_nothing(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")  # Alex's notices stay held
    await book(world, booking())
    world.texts.refuse(ROBIN_MOBILE, "invalid_number")

    assert (await book(world, robin())).status_code == 422

    _, _, notices = await rows()
    assert not [n for n in notices if n.recipient_key.endswith(":withdrawn")]
    await world.send_pending(NOW + timedelta(minutes=5))
    (event,) = host_events(world)
    assert "Robin Lee" not in event.draft.description
