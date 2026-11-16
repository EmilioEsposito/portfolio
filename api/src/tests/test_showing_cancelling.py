"""Cancelling, from the routes to the rows it leaves: a prospect from their link, the team
from the tab, and a unit going off the market.

Each scenario books through the public routes, cancels through the public or
team routes (or saves a listing's off-market date through the rental listing
routes), runs the job's two steps as the clock moves, and checks
``showings``, ``showing_attendees`` and ``showing_notices``. Quo is a
``RecordingTextSender``, the calendars an ``InMemoryCalendar``, Turnstile a
mock transport. Nothing is sent anywhere.
"""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import routes as listing_routes
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import cancelling, open_times, routes
from api.src.showing_bookings.fakes import InMemoryCalendar, RecordingTextSender
from api.src.showing_bookings.providers import NotSent
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

PUBLIC = "/api/showing-bookings/public"
TEAM = "/api/showing-bookings"
OTHER_UNIT = "412-01"  # another building
THURSDAY = WEDNESDAY + timedelta(days=1)
UNIT_LINK = f"https://serniacapital.com/showings/{UNIT}"
UNITS_LINK = "https://serniacapital.com/showings"


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE}")
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
    await store_settings(send_mode="live", test_sends_enabled=True)


def robin_body(start=None) -> dict:
    return booking(start, mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee")


async def book(world: PublicWorld, body: dict | None = None, unit: str = UNIT) -> dict:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{unit}/bookings", json=body or booking())
    assert answer.status_code == 201, answer.text
    return answer.json()


async def cancel(world: PublicWorld, token) -> httpx.Response:
    async with world.client() as client:
        return await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token})


async def lookup(world: PublicWorld, token: str) -> httpx.Response:
    async with world.client() as client:
        return await client.post(f"{PUBLIC}/bookings/lookup", json={"cancel_token": token})


def team_app(world: PublicWorld) -> FastAPI:
    application = world.team_app()
    application.dependency_overrides[routes.text_sender] = lambda: world.texts
    application.dependency_overrides[routes.clock] = lambda: world.now
    return application


async def team(world: PublicWorld, path: str) -> httpx.Response:
    async with world.client(team_app(world)) as client:
        return await client.post(f"{TEAM}{path}")


async def offers(world: PublicWorld, unit: str = UNIT) -> dict[str, dict]:
    open_times.forget_free_busy()
    async with world.client() as client:
        answer = await client.get(f"{PUBLIC}/units/{unit}")
    return {offer["start"]: offer for offer in answer.json()["offers"]}


def refusal(answer: httpx.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["detail"]["code"]


# A prospect cancels


@pytest.mark.asyncio
async def test_a_prospect_cancels_with_others_still_booked(world):
    alex = await book(world)
    await book(world, robin_body())
    await world.send_pending()  # capture mode: everything captured

    answer = await cancel(world, alex["cancel_token"])

    assert answer.status_code == 200, answer.text
    view = answer.json()
    assert (view["status"], view["started"], view["cancelled_by_team"]) == (
        "cancelled",
        False,
        False,
    )
    showings, attendees, notices = await rows()
    assert [s.status for s in showings] == ["scheduled"]
    assert [(a.status, a.cancel_reason, a.cancelled_at) for a in attendees] == [
        ("cancelled", "prospect", NOW),
        ("booked", None, None),
    ]
    kinds = by_kind(notices)
    (text,) = kinds["prospect_cancelled"]
    # Delivered at once, as the confirmation is: captured in capture mode.
    assert (text.status, text.recipient) == ("captured", ALEX_MOBILE)
    body = text.payload["body"]
    assert body.startswith("Sernia Capital:")
    assert "Wed, Apr 14 at 4:00 PM" in body and ADDRESS in body and UNIT_LINK in body
    (invite,) = kinds["prospect_invite_cancel"]
    assert invite.status == "pending"
    assert (invite.payload["operation"], invite.payload["attendees"]) == ("cancel", [ALEX_EMAIL])
    (update,) = [n for n in notices if n.recipient_key.endswith(":cancelled")]
    assert (update.kind, update.status) == ("host_event_update", "pending")
    assert "host_event_cancel" not in kinds

    await world.send_pending()

    _, _, notices = await rows()
    assert (invite.id, "captured") in [(n.id, n.status) for n in notices]
    update = next(n for n in notices if n.id == update.id)
    assert update.status == "captured"
    assert "Robin Lee" in update.payload["description"]
    assert "Alex Parker" not in update.payload["description"]
    assert world.texts.sent == [] and world.calendar.events == {}


@pytest.mark.asyncio
async def test_the_last_prospect_cancels_after_the_event_and_invite_went_out(world):
    await live()
    alex = await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    assert len(world.calendar.events) == 2  # the host's event and Alex's invite

    answer = await cancel(world, alex["cancel_token"])

    assert answer.json()["status"] == "cancelled"
    showings, attendees, notices = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "prospect")
    assert (attendees[0].status, attendees[0].cancel_reason) == ("cancelled", "prospect")
    # The text went to Alex at once; it is a test recipient in live mode.
    texts = world.texts.to(ALEX_MOBILE)
    assert len(texts) == 2 and "is cancelled" in texts[-1] and UNIT_LINK in texts[-1]
    kinds = by_kind(notices)
    assert [n.status for n in kinds["host_event_cancel"]] == ["pending"]
    assert [n.status for n in kinds["prospect_invite_cancel"]] == ["pending"]

    await world.send_pending(NOW + timedelta(minutes=2))

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.status for n in kinds["host_event_cancel"]] == ["sent"]
    assert [n.status for n in kinds["prospect_invite_cancel"]] == ["sent"]
    assert all(event.cancelled for event in world.calendar.events.values())
    assert [entry[0] for entry in world.calendar.log] == ["create", "create", "cancel", "cancel"]
    assert {entry[1] for entry in world.calendar.log} == {SHOWINGS_CALENDAR}


@pytest.mark.asyncio
async def test_cancelling_twice_answers_cancelled_and_sends_nothing_more(world):
    await live()
    alex = await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    first = await cancel(world, alex["cancel_token"])
    await world.send_pending(NOW + timedelta(minutes=2))
    _, _, before = await rows()
    texts_before, log_before = list(world.texts.sent), list(world.calendar.log)

    second = await cancel(world, alex["cancel_token"])
    await world.send_pending(NOW + timedelta(minutes=3))

    assert second.status_code == 200
    assert second.json() == first.json()
    _, _, after = await rows()
    assert statuses(after) == statuses(before)
    assert world.texts.sent == texts_before and world.calendar.log == log_before


@pytest.mark.asyncio
async def test_a_showing_that_has_started_cannot_be_cancelled(world):
    alex = await book(world)
    world.now = at(16)

    answer = await cancel(world, alex["cancel_token"])

    assert refusal(answer) == (409, "started")
    _, attendees, notices = await rows()
    assert attendees[0].status == "booked"
    assert "prospect_cancelled" not in by_kind(notices)
    seen = (await lookup(world, alex["cancel_token"])).json()
    assert (seen["status"], seen["started"]) == ("booked", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["nope", "", "x" * 101])
async def test_an_unknown_link_is_404(world, token):
    await book(world)

    answer = await cancel(world, token)

    assert refusal(answer) == (404, "unknown_booking")
    _, attendees, _ = await rows()
    assert attendees[0].status == "booked"


@pytest.mark.asyncio
async def test_a_cancellation_while_the_notices_are_still_held(world):
    await live()
    world.texts.report(ALEX_MOBILE, "sent")  # no final report yet: the rest stay held
    alex = await book(world)

    await cancel(world, alex["cancel_token"])

    _, _, notices = await rows()
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "cancelled"),
        ("prospect_invite", "cancelled"),
        ("prospect_cancelled", "sent"),
    ]
    assert notices[1].last_error == cancelling.STOPPED_NOTE
    # Nothing was created, so nothing is cancelled on a calendar.
    await world.send_pending(NOW + timedelta(minutes=10))
    assert world.calendar.events == {} and world.calendar.log == []


@pytest.mark.asyncio
async def test_a_cancellation_while_the_event_is_being_created_waits_for_it(world):
    await live()
    alex = await book(world)

    async def cancel_alex():
        await cancel(world, alex["cancel_token"])

    world.calendar = CalendarWithAHook(cancel_alex)
    # The host's event is being created when Alex cancels; the invite, not
    # started yet, is stopped.
    await world.send_pending(NOW + timedelta(minutes=1))

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.status for n in kinds["host_event"]] == ["sent"]
    assert [n.status for n in kinds["prospect_invite"]] == ["cancelled"]
    assert "prospect_invite_cancel" not in kinds
    assert [n.status for n in kinds["host_event_cancel"]] == ["pending"]

    await world.send_pending(NOW + timedelta(minutes=2))

    assert [entry[0] for entry in world.calendar.log] == ["create", "cancel"]
    (event,) = world.calendar.events.values()
    assert event.cancelled and event.draft.attendees == [JANE]


class CalendarWithAHook(InMemoryCalendar):
    """Runs ``hook`` once, in the middle of the first event it creates."""

    def __init__(self, hook) -> None:
        super().__init__()
        self.hook = hook

    async def create_event(self, calendar_id, event):
        if self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()
        return await super().create_event(calendar_id, event)


# The place is offered again


@pytest.mark.asyncio
async def test_a_place_freed_is_offered_again_and_a_cancelled_showing_frees_the_host(world):
    alex = await book(world)
    robin = await book(world, robin_body())
    start = at(16).isoformat()
    assert (await offers(world))[start]["places_left"] == 2
    # Jane hosts 320-03 at 4:00, so the other building has nothing then.
    assert start not in await offers(world, OTHER_UNIT)

    await cancel(world, alex["cancel_token"])
    assert (await offers(world))[start] == {
        "start": start,
        "join": True,
        "preferred": False,
        "places_left": 3,
    }

    await cancel(world, robin["cancel_token"])
    assert (await offers(world))[start]["join"] is False
    assert start in await offers(world, OTHER_UNIT)
    # A new time is a new booking.
    again = await book(world, booking(mobile=CASEY_MOBILE, email=CASEY_EMAIL, name="Casey Kim"))
    assert again["kind"] == "new"
    showings, _, _ = await rows()
    assert [s.status for s in showings] == ["cancelled", "scheduled"]


# What a public answer shows


@pytest.mark.asyncio
async def test_no_cancel_answer_shows_another_attendee(world):
    alex = await book(world)
    robin = await book(world, robin_body())

    cancelled = await cancel(world, alex["cancel_token"])
    seen = await lookup(world, alex["cancel_token"])

    for answer in (cancelled.text, seen.text):
        for private in ("Robin", ROBIN_MOBILE, ROBIN_EMAIL, robin["cancel_token"], "Jane", JANE):
            assert private not in answer
    view = seen.json()
    assert (view["unit"], view["address"], view["status"]) == (UNIT, ADDRESS, "cancelled")
    assert set(view) == {
        "status",
        "unit",
        "address",
        "starts_at",
        "ends_at",
        "timezone",
        "mobile",
        "corrections_left",
        "started",
        "cancelled_by_team",
    }


# The team cancels


@pytest.mark.asyncio
async def test_the_team_cancels_a_showing_after_everything_went_out(world):
    await live()
    alex = await book(world)
    await book(world, robin_body())
    await world.send_pending(NOW + timedelta(minutes=1))
    showings, _, _ = await rows()
    assert len(world.calendar.events) == 3

    answer = await team(world, f"/showings/{showings[0].id}/cancel")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "showing_id": showings[0].id,
        "showing_status": "cancelled",
        "attendees_cancelled": 2,
        "texts_queued": 2,
    }
    showings, attendees, _ = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "team")
    assert [(a.status, a.cancel_reason) for a in attendees] == [("cancelled", "team")] * 2
    for mobile in (ALEX_MOBILE, ROBIN_MOBILE):
        text = world.texts.to(mobile)[-1]
        assert "we had to cancel your showing on Wed, Apr 14 at 4:00 PM" in text
        assert text.endswith(UNITS_LINK)

    await world.send_pending(NOW + timedelta(minutes=2))

    assert all(event.cancelled for event in world.calendar.events.values())
    assert [entry[0] for entry in world.calendar.log].count("cancel") == 3
    seen = (await lookup(world, alex["cancel_token"])).json()
    assert (seen["status"], seen["cancelled_by_team"]) == ("cancelled", True)
    # A second press changes and sends nothing.
    sent = len(world.texts.sent)
    again = await team(world, f"/showings/{showings[0].id}/cancel")
    assert again.json()["attendees_cancelled"] == 0 and len(world.texts.sent) == sent


@pytest.mark.asyncio
async def test_the_team_removes_one_attendee_then_the_last(world):
    await book(world)
    await book(world, robin_body())
    await world.send_pending()
    showings, attendees, _ = await rows()
    showing_id, alex_id, robin_id = showings[0].id, attendees[0].id, attendees[1].id

    answer = await team(world, f"/showings/{showing_id}/attendees/{robin_id}/remove")

    assert answer.json()["attendees_cancelled"] == 1
    assert answer.json()["showing_status"] == "scheduled"
    _, attendees, notices = await rows()
    assert [(a.status, a.cancel_reason) for a in attendees] == [
        ("booked", None),
        ("cancelled", "team"),
    ]
    kinds = by_kind(notices)
    (text,) = kinds["prospect_cancelled_by_team"]
    assert text.recipient == ROBIN_MOBILE and text.status == "captured"
    assert UNITS_LINK in text.payload["body"]
    (robin_invite_cancel,) = kinds["prospect_invite_cancel"]
    assert [n.status for n in notices if n.recipient_key.endswith(":cancelled")] == ["pending"]

    answer = await team(world, f"/showings/{showing_id}/attendees/{alex_id}/remove")

    assert answer.json()["showing_status"] == "cancelled"
    showings, _, notices = await rows()
    assert (showings[0].status, showings[0].cancel_reason) == ("cancelled", "team")
    kinds = by_kind(notices)
    assert [n.recipient for n in kinds["prospect_cancelled_by_team"]] == [ROBIN_MOBILE, ALEX_MOBILE]
    assert [n.status for n in kinds["host_event_cancel"]] == ["pending"]
    # Robin's withdrawn invite, queued earlier, is not stopped by the second cancellation.
    assert next(n for n in notices if n.id == robin_invite_cancel.id).status == "pending"

    await world.send_pending()
    _, _, notices = await rows()
    assert [n.status for n in by_kind(notices)["host_event_cancel"]] == ["captured"]
    assert [n.status for n in by_kind(notices)["prospect_invite_cancel"]] == [
        "captured",
        "captured",
    ]


@pytest.mark.asyncio
async def test_the_team_routes_refuse_what_they_cannot_cancel(world):
    await book(world)
    showings, attendees, _ = await rows()

    assert (await team(world, "/showings/999999/cancel")).status_code == 404
    missing = f"/showings/{showings[0].id}/attendees/999999/remove"
    assert (await team(world, missing)).status_code == 404
    world.now = at(16, 10)
    assert (await team(world, f"/showings/{showings[0].id}/cancel")).status_code == 409
    removal = f"/showings/{showings[0].id}/attendees/{attendees[0].id}/remove"
    assert (await team(world, removal)).status_code == 409
    showings, attendees, _ = await rows()
    assert (showings[0].status, attendees[0].status) == ("scheduled", "booked")


@pytest.mark.asyncio
async def test_the_showings_list_names_the_people_and_who_needs_a_call(world):
    await live()
    world.texts.report(ROBIN_MOBILE, "undelivered")
    await book(world)
    await book(world, robin_body())
    await world.send_pending(NOW + timedelta(minutes=1))

    async with world.client(team_app(world)) as client:
        view = (await client.get(f"{TEAM}/showings")).json()

    (showing,) = view["booked"]
    assert showing["attendees"] == 2 and showing["started"] is False
    assert [
        (p["name"], p["mobile"], p["email"], p["texts_accepted"], p["attention_reason"])
        for p in showing["people"]
    ] == [
        ("Alex Parker", "412-555-0117", ALEX_EMAIL, True, None),
        ("Robin Lee", "412-555-0118", ROBIN_EMAIL, True, "confirmation_undelivered"),
    ]
    assert view["send_mode"] == "live"


# The unit goes off the market


async def _listed(world: PublicWorld) -> None:
    """Put the world's listing in the database, as the rental listing screen saved it."""
    async with AsyncSessionFactory() as session:
        await listings_service.save_listing(session, world.listing, changed_by="test")


def listing_app() -> FastAPI:
    application = FastAPI()
    application.include_router(listing_routes.router, prefix="/api")
    person = SimpleNamespace(
        id="user_test",
        first_name="Pat",
        last_name="Example",
        email_addresses=[SimpleNamespace(email_address="pat@serniacapital.com")],
    )
    application.dependency_overrides[listing_routes.rental_listing_caller] = lambda: (
        listing_routes.Caller(user=person, source=listing_routes.SOURCE)
    )
    return application


@pytest.mark.asyncio
async def test_saving_an_off_market_date_cancels_the_showings_from_that_day(world):
    await _listed(world)
    alex = await book(world)  # Wednesday 4:00
    await book(world, robin_body(at(16, day=THURSDAY)))
    await world.send_pending()

    changed = world.listing.model_copy(update={"off_market_on": THURSDAY})
    _, _, before = await rows()
    async with world.client(listing_app()) as client:
        path = f"/api/rental-listings/listings/{UNIT}"
        body = {"listing": changed.model_dump(mode="json")}
        # Unconfirmed, and as a dry run, it is refused with the counts and changes nothing.
        refused = await client.put(path, json=body)
        dry = await client.put(f"{path}?dry_run=true", json=body)
        assert [refused.status_code, dry.status_code] == [409, 409]
        assert refused.json()["detail"] == {
            "code": "cancels_showings",
            "message": "Not saved. This cancels 1 showing and texts 1 person. Confirm to save.",
            "problems": [],
            "showings": 1,
            "people": 1,
            "people_texted": 1,
        }
        showings, _, notices = await rows()
        assert [s.status for s in showings] == ["scheduled", "scheduled"]
        assert statuses(notices) == statuses(before)
        async with AsyncSessionFactory() as session:
            assert (await listings_service.read_listing(session, UNIT)).off_market_on is None

        saved = await client.put(path, json={**body, "confirm_cancellations": True})

    assert saved.status_code == 200, saved.text
    showings, attendees, notices = await rows()
    assert [(s.starts_at, s.status, s.cancel_reason) for s in showings] == [
        (at(16), "scheduled", None),
        (at(16, day=THURSDAY), "cancelled", "off_market"),
    ]
    assert [(a.status, a.cancel_reason) for a in attendees] == [
        ("booked", None),
        ("cancelled", "off_market"),
    ]
    kinds = by_kind(notices)
    (text,) = kinds["prospect_cancelled_by_team"]
    # Queued with the listing's save, for the job every minute.
    assert (text.recipient, text.status) == (ROBIN_MOBILE, "pending")
    assert "Thu, Apr 15 at 4:00 PM" in text.payload["body"]
    assert UNITS_LINK in text.payload["body"]
    assert [n.status for n in kinds["host_event_cancel"]] == ["pending"]

    await world.send_pending()
    _, _, notices = await rows()
    assert {n.status for n in notices} == {"captured"}
    assert (await lookup(world, alex["cancel_token"])).json()["status"] == "booked"


@pytest.mark.asyncio
async def test_an_off_market_date_leaves_started_and_past_showings(world):
    await _listed(world)
    await book(world)
    world.now = at(16, 5)

    changed = world.listing.model_copy(update={"off_market_on": date(2027, 4, 1)})
    async with world.client(listing_app()) as client:
        saved = await client.put(
            f"/api/rental-listings/listings/{UNIT}",
            json={"listing": changed.model_dump(mode="json")},
        )

    assert saved.status_code == 200
    showings, attendees, _ = await rows()
    assert (showings[0].status, attendees[0].status) == ("scheduled", "booked")


# A number known to be wrong


@pytest.mark.asyncio
async def test_a_number_whose_confirmation_was_undelivered_gets_no_cancellation_text(world):
    await live()
    world.texts.report(ALEX_MOBILE, "undelivered")
    alex = await book(world)
    await world.send_pending(NOW + timedelta(minutes=1))
    _, attendees, _ = await rows()
    assert attendees[0].attention_reason == "confirmation_undelivered"
    sent = len(world.texts.sent)

    await cancel(world, alex["cancel_token"])

    _, attendees, notices = await rows()
    assert attendees[0].status == "cancelled"
    assert "prospect_cancelled" not in by_kind(notices)
    assert len(world.texts.sent) == sent


# Booking switched off


@pytest.mark.asyncio
async def test_a_booked_person_can_read_and_cancel_after_booking_is_switched_off(world):
    alex = await book(world)
    await store_settings(public_booking_enabled=False)

    async with world.client() as client:
        unit_page = await client.get(f"{PUBLIC}/units/{UNIT}")
    seen = await lookup(world, alex["cancel_token"])
    cancelled = await cancel(world, alex["cancel_token"])
    unknown = await cancel(world, "nope")

    assert unit_page.status_code == 404
    assert seen.status_code == 200 and seen.json()["status"] == "booked"
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert refusal(unknown) == (404, "unknown_booking")


# An invite being sent when the booking is cancelled


class FailingOnceCalendar(InMemoryCalendar):
    """Runs ``hook`` in the middle of the first invite it is asked to create, then
    answers that create with a refusal worth retrying, as Google out of reach would."""

    def __init__(self, hook) -> None:
        super().__init__()
        self.hook = hook

    async def create_event(self, calendar_id, event):
        if self.hook is not None and event.attendees == [ALEX_EMAIL]:
            hook, self.hook = self.hook, None
            await hook()
            raise NotSent("google_unavailable", "Google could not be reached.", retryable=True)
        return await super().create_event(calendar_id, event)


@pytest.mark.asyncio
async def test_an_invite_retried_after_its_booking_was_cancelled_is_never_created(world):
    await live()
    await book(world, robin_body())
    alex = await book(world)

    async def cancel_alex():
        await cancel(world, alex["cancel_token"])

    world.calendar = FailingOnceCalendar(cancel_alex)
    await world.send_pending(NOW + timedelta(minutes=1))

    # Alex cancelled while the invite was being sent, and the send failed.
    for minutes in (2, 5, 30, 90):
        await world.send_pending(NOW + timedelta(minutes=minutes))

    _, _, notices = await rows()
    alex_invite = next(
        n for n in notices if n.kind == "prospect_invite" and n.recipient == ALEX_EMAIL
    )
    assert alex_invite.status == "cancelled"
    assert alex_invite.last_error == cancelling.STOPPED_NOTE
    assert not [n for n in notices if n.status in ("pending", "held")]
    invites = [e for e in world.calendar.events.values() if e.draft.attendees == [ALEX_EMAIL]]
    assert invites == []


class FailingOnceTexts(RecordingTextSender):
    """Runs ``hook`` in the middle of the first text to ``number``, then answers that
    text with a refusal worth retrying, as Quo out of reach would."""

    def __init__(self, number: str, hook) -> None:
        super().__init__()
        self.number, self.hook = number, hook

    async def send_text(self, to, body):
        if self.hook is not None and to == self.number:
            hook, self.hook = self.hook, None
            await hook()
            raise NotSent("quo_unavailable", "Quo could not be reached.", retryable=True)
        return await super().send_text(to, body)


@pytest.mark.asyncio
async def test_a_confirmation_retried_after_its_booking_was_cancelled_is_never_sent(world):
    await live()

    async def cancel_alex():
        _, attendees, _ = await rows()
        await cancel(world, attendees[0].cancel_token)

    world.texts = FailingOnceTexts(ALEX_MOBILE, cancel_alex)
    await book(world)

    for minutes in (2, 5, 30, 90):
        await world.send_pending(NOW + timedelta(minutes=minutes))

    _, _, notices = await rows()
    (confirmation,) = by_kind(notices)["prospect_confirmation"]
    assert (confirmation.status, confirmation.last_error) == ("cancelled", cancelling.STOPPED_NOTE)
    # Only the text confirming the cancellation reached Alex.
    assert [body.split(":")[1].strip()[:9] for body in world.texts.to(ALEX_MOBILE)] == ["your show"]
    assert not [n for n in notices if n.status in ("pending", "held")]


# A booking under way while an off-market date is saved


def pause_bookings(monkeypatch):
    """Make the next booking stop inside its transaction, holding the shared booking
    lock, until ``release`` is set. Answers (entered, release)."""
    entered, release = asyncio.Event(), asyncio.Event()
    original = open_times.recheck

    async def paused(*args, **kwargs):
        if not release.is_set():
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(open_times, "recheck", paused)
    return entered, release


async def _while_a_booking_is_under_way(world, monkeypatch, save):
    """Start Robin's booking, hold it mid-transaction, start ``save``, let both finish."""
    entered, release = pause_bookings(monkeypatch)
    booking = asyncio.create_task(book(world, robin_body()))
    await entered.wait()
    saving = asyncio.create_task(save())
    # Long enough for the save to count and save if nothing held it back.
    await asyncio.sleep(0.5)
    release.set()
    return await booking, await saving


@pytest.mark.asyncio
async def test_a_booking_under_way_makes_the_listing_save_ask_instead_of_cancelling_it(
    world, monkeypatch
):
    await _listed(world)
    changed = world.listing.model_copy(update={"off_market_on": WEDNESDAY})

    async def save():
        async with world.client(listing_app()) as client:
            return await client.put(
                f"/api/rental-listings/listings/{UNIT}",
                json={"listing": changed.model_dump(mode="json")},
            )

    robin, saved = await _while_a_booking_is_under_way(world, monkeypatch, save)

    # The save waited for the booking, counted it, and asked.
    assert robin["kind"] == "new"
    assert saved.status_code == 409
    assert saved.json()["detail"]["showings"] == 1
    showings, attendees, notices = await rows()
    assert [s.status for s in showings] == ["scheduled"]
    assert [a.status for a in attendees] == ["booked"]
    assert not [n for n in notices if n.kind in cancelling.CANCELLATION_KINDS]
    async with AsyncSessionFactory() as session:
        assert (await listings_service.read_listing(session, UNIT)).off_market_on is None


@pytest.mark.asyncio
async def test_a_booking_under_way_makes_the_agents_approved_save_ask_again(world, monkeypatch):
    from api.src.sernia_ai.tools import rental_listing_tools

    await _listed(world)
    await book(world)  # Alex, Wednesday 4:00
    approved = "This cancels 1 showing and texts 1 person."
    person = SimpleNamespace(
        deps=SimpleNamespace(user_name="Pat", conversation_id="conversation-1"),
        tool_call_approved=True,
    )

    async def save():
        return await rental_listing_tools.rental_listing_update(
            person, unit=UNIT, off_market_on=WEDNESDAY, off_market_effect=approved
        )

    robin, said = await _while_a_booking_is_under_way(world, monkeypatch, save)

    # Robin joined Alex's showing before the count, so what was approved no longer holds.
    assert robin["kind"] == "join"
    assert said.startswith("Not saved. The effect changed since it was asked.")
    assert "This cancels 1 showing and texts 2 people." in said
    showings, attendees, notices = await rows()
    assert [s.status for s in showings] == ["scheduled"]
    assert [a.status for a in attendees] == ["booked", "booked"]
    assert not [n for n in notices if n.kind in cancelling.CANCELLATION_KINDS]
