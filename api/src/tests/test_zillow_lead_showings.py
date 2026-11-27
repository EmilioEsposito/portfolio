"""What the lead agent's case knows about showings, with the booking switch off and on.

With ``lead_agent_uses_open_times`` off the case is what it always was (see
also ``test_zillow_lead_case_unchanged.py``). With it on, the case carries the
thread's own booking links (``?t=<token>``), the thread's own showings, and
each unit's own tour times; a page booking made through a thread's link is
tied to that thread. Bookings go through the real public routes with the
stand-ins in ``showing_public.py``; the model, Gmail and notifications are
mocked. Names, numbers, threads and addresses are made up.
"""

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import TypeAdapter
from sqlalchemy import delete, select

from api.src.agent_conversations.models import AgentConversation, get_agent_conversation
from api.src.database.database import AsyncSessionFactory, Base, engine
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import lead_tours, open_times
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    JANE,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    WEDNESDAY,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    install,
    rows,
)
from api.src.tests.test_zillow_lead_agent import ET, check, decision
from api.src.tests.test_zillow_lead_agent import LISTING_DATA as WEEKLY_LISTING_DATA
from api.src.tests.test_zillow_lead_agent import message as weekly_message
from api.src.tests.test_zillow_lead_agent import thread as weekly_thread
from api.src.zillow_leads import handle, lead_links
from api.src.zillow_leads import listing_data as listing_data_module
from api.src.zillow_leads.agent import LeadOutcome
from api.src.zillow_leads.auto_reply import reply_problems
from api.src.zillow_leads.case import (
    OWN_SHOWINGS_HEADING,
    BookedTour,
    Listing,
    ListingData,
    OwnShowing,
    TourSlot,
    build_case,
    case_from_record,
    case_record,
    render_case,
    within_showing_rules,
)
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.models import ZillowLeadLink
from api.src.zillow_leads.thread import LeadMessage, LeadThread
from api.src.zillow_leads.tours import tours_for_case

PUBLIC = "/api/showing-bookings/public"
BOOK = f"{PUBLIC}/units/{UNIT}/bookings"
ALEX_THREAD = "thread-alex-0001"
ROBIN_THREAD = "thread-robin-0002"
LINK_LINE = f"- {UNIT}: https://serniacapital.com/showings/{UNIT}?t="
SIGNED = "\n\nSernia Capital Team"


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SHOWING_PUBLIC_BASE_URL", raising=False)
    open_times.forget_free_busy()
    await clear_showing_data()
    await _forget_links()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await _forget_links()


async def _forget_links() -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(ZillowLeadLink).where(ZillowLeadLink.thread_id.like("thread-%"))
        )
        await session.commit()


async def book(world: PublicWorld, body: dict):
    async with world.client() as client:
        return await client.post(BOOK, json=body)


def lead_thread(thread_id: str) -> LeadThread:
    return LeadThread(
        mailbox="all@serniacapital.com",
        thread_id=thread_id,
        messages=[
            LeadMessage(
                message_id=f"{thread_id}-m0",
                rfc_message_id=f"<{thread_id}-m0@example.com>",
                sent_at=NOW - timedelta(minutes=10),
                from_address="noreply@mail.zillow.com",
                reply_to=f"{thread_id}@convo.zillow.com",
                from_sernia=False,
                subject="320 Example St inquiry",
                body="Can I see it Wednesday?",
            )
        ],
    )


@pytest_asyncio.fixture
async def lead_case(world, monkeypatch):
    """Handle a thread as production does and return the case the agent read."""
    conversations: list[str] = []
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    no_reply = TypeAdapter(LeadOutcome).validate_python(
        {"lead": {}, "tour": {}, "action": "no_reply", "reason": "Test."}
    )
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=no_reply))
    monkeypatch.setattr(handle, "datetime", SimpleNamespace(now=lambda tz=None: world.now))

    async def run(thread_id: str):
        loaded = listing_data_module.Loaded(
            data=as_listing_data([world.listing, *world.other_listings], world.rules)
        )
        monkeypatch.setattr(listing_data_module, "load", AsyncMock(return_value=loaded))
        monkeypatch.setattr(
            handle, "load_lead_thread", AsyncMock(return_value=lead_thread(thread_id))
        )
        conversation_id = f"test-lead-{uuid.uuid4().hex}"
        conversations.append(conversation_id)
        await handle.handle_thread(thread_id, conversation_id=conversation_id, metadata={})
        (case,) = handle.respond.call_args.args
        async with AsyncSessionFactory() as session:
            row = await get_agent_conversation(session, conversation_id, retries=1)
        return case, row.metadata_

    yield run
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AgentConversation).where(AgentConversation.id.in_(conversations))
        )
        await session.commit()


async def token_of(thread_id: str) -> str | None:
    async with AsyncSessionFactory() as session:
        return await session.scalar(
            select(ZillowLeadLink.token).where(ZillowLeadLink.thread_id == thread_id)
        )


# The token


@pytest.mark.asyncio
async def test_a_thread_has_one_random_token_that_resolves_back_to_it(world):
    first = await lead_links.token_for_thread(ALEX_THREAD)
    again = await lead_links.token_for_thread(ALEX_THREAD)
    other = await lead_links.token_for_thread(ROBIN_THREAD)

    assert first == again != other
    assert lead_links.TOKEN.fullmatch(first) and ALEX_THREAD not in first
    assert await lead_links.thread_for_token(first) == ALEX_THREAD
    assert await lead_links.thread_for_token(other) == ROBIN_THREAD
    for unknown in (None, "", "no-such-token-0000000", "<script>alert(1)</script>", first[:8]):
        assert await lead_links.thread_for_token(unknown) is None


@pytest.mark.asyncio
async def test_the_token_table_matches_its_migration():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def only_this_table(obj, name, type_, reflected, compare_to):
        table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
        return table == "zillow_lead_links"

    def differences(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_object": only_this_table}
        )
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == []


# The switch off: nothing new reaches the case


@pytest.mark.asyncio
async def test_with_the_switch_off_the_case_is_as_before_and_no_token_is_made(world, lead_case):
    await store_settings(lead_agent_uses_open_times=False)
    token = await lead_links.token_for_thread(ROBIN_THREAD)
    # A booking through some thread's link still lands, but this thread's case
    # is untouched by it.
    assert (await book(world, booking(lead_token=token))).status_code == 201

    case, metadata = await lead_case(ALEX_THREAD)

    data = as_listing_data([world.listing], world.rules)
    before = build_case(lead_thread(ALEX_THREAD), data, now=NOW, slots=[], booked_tours=[])
    assert render_case(case) == render_case(before)
    assert case.own_showings is None and case.offerable_slots == []
    assert [listing.booking_url for listing in case.listings] == [""]
    assert OWN_SHOWINGS_HEADING not in render_case(case)
    assert await token_of(ALEX_THREAD) is None
    # The structured case is saved with the outcome, and rebuilds the same case.
    assert render_case(case_from_record(metadata["case"])) == render_case(case)


# The switch on


@pytest.mark.asyncio
async def test_a_booking_through_the_lead_link_shows_in_that_thread_only(world, lead_case):
    await store_settings(lead_agent_uses_open_times=True)

    case, _ = await lead_case(ALEX_THREAD)
    token = await token_of(ALEX_THREAD)
    link = f"https://serniacapital.com/showings/{UNIT}?t={token}"
    assert [listing.booking_url for listing in case.listings] == [link]
    text = render_case(case)
    assert f"## Booking links\n- {UNIT}: {link}\n" in text
    assert f"{OWN_SHOWINGS_HEADING}\n- (none)" in text
    assert f"- {UNIT}, Wednesday, April 14: " in text  # times listed under their unit
    assert all(slot.unit == UNIT and slot.agent == "" for slot in case.offerable_slots)

    # Alex books through the link; Robin books the same showing on their own.
    answer = await book(world, booking(at(16), lead_token=token))
    assert answer.status_code == 201, answer.text
    robin = booking(at(16), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
    assert (await book(world, robin)).status_code == 201
    _, attendees, _ = await rows()
    assert [(row.name, row.zillow_thread_id, row.lead_token) for row in attendees] == [
        ("Alex Parker", ALEX_THREAD, token),
        ("Robin Lee", None, None),
    ]

    alex_case, alex_meta = await lead_case(ALEX_THREAD)
    robin_case, robin_meta = await lead_case(ROBIN_THREAD)

    # Booked on the page with the details and the ticks: nothing left to add.
    assert alex_case.own_showings == [
        OwnShowing(unit=UNIT, start=at(16), status="booked", details_added=True)
    ]
    assert (
        f"{OWN_SHOWINGS_HEADING}\n- {UNIT}: Wednesday, April 14 at 4:00 PM, booked, "
        "details added (text reminders and invite set up)\n"
    ) in render_case(alex_case)
    assert robin_case.own_showings == []
    assert f"{OWN_SHOWINGS_HEADING}\n- (none)" in render_case(robin_case)
    # The showing both are in still has room, so it is offered to every other lead.
    assert BookedTour(unit=UNIT, start=at(16)) in robin_case.booked_tours
    assert BookedTour(unit=UNIT, start=at(16)) not in alex_case.booked_tours
    # Robin's own link is a different token.
    assert robin_case.listings[0].booking_url != alex_case.listings[0].booking_url

    # Nothing about another attendee or the host, in either case or what is saved.
    for case, metadata in ((alex_case, alex_meta), (robin_case, robin_meta)):
        saved = json.dumps(metadata["case"])
        for text in (render_case(case), saved, case.model_dump_json()):
            for private in ("Alex", "Robin", "Parker", "Lee", "Jane", "Doe", JANE):
                assert private not in text
            for private in (ALEX_MOBILE, ROBIN_MOBILE, ALEX_EMAIL, ROBIN_EMAIL, "555-01"):
                assert private not in text
        assert render_case(case_from_record(metadata["case"])) == render_case(case)


@pytest.mark.asyncio
async def test_an_unknown_token_is_kept_as_it_came_and_ties_nothing(world):
    await store_settings(lead_agent_uses_open_times=True)
    answer = await book(world, booking(lead_token="unknown-token-12345678"))
    assert answer.status_code == 201, answer.text
    _, (alex,), _ = await rows()
    assert alex.lead_token == "unknown-token-12345678" and alex.zillow_thread_id is None


@pytest.mark.asyncio
async def test_one_thread_cannot_hold_two_places_in_one_showing(world):
    token = await lead_links.token_for_thread(ALEX_THREAD)
    assert (await book(world, booking(at(16), lead_token=token))).status_code == 201

    second = booking(
        at(16), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL, lead_token=token
    )
    answer = await book(world, second)

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "already_booked"
    _, attendees, _ = await rows()
    assert [row.name for row in attendees] == ["Alex Parker"]
    # Another showing through the same link is a booking like any other.
    later = booking(at(17), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
    assert (await book(world, {**later, "lead_token": token})).status_code == 201
    _, attendees, _ = await rows()
    assert [row.zillow_thread_id for row in attendees] == [ALEX_THREAD, ALEX_THREAD]


@pytest.mark.asyncio
async def test_a_cancelled_own_showing_reads_as_cancelled(world, lead_case):
    await store_settings(lead_agent_uses_open_times=True)
    token = await lead_links.token_for_thread(ALEX_THREAD)
    answer = await book(world, booking(at(16), lead_token=token))
    async with world.client() as client:
        cancelled = await client.post(
            f"{PUBLIC}/bookings/cancel", json={"cancel_token": answer.json()["cancel_token"]}
        )
    assert cancelled.status_code == 200, cancelled.text

    case, _ = await lead_case(ALEX_THREAD)

    assert case.own_showings == [
        OwnShowing(unit=UNIT, start=at(16), status="cancelled", cancelled_by="prospect")
    ]
    assert (
        f"{OWN_SHOWINGS_HEADING}\n- {UNIT}: Wednesday, April 14 at 4:00 PM, cancelled by the lead"
        in render_case(case)
    )
    # A past showing is not the lead's upcoming showing.
    world.now = at(16, 30)
    later, _ = await lead_case(ALEX_THREAD)
    assert later.own_showings == []


@pytest.mark.asyncio
async def test_each_units_times_keep_the_lead_agents_fixed_rules(world):
    world.other_listings = [RentalListing(unit="412-01", address="412 Example St")]
    thursday = WEDNESDAY + timedelta(days=1)
    data = as_listing_data([world.listing, *world.other_listings], world.rules)
    data = data.model_copy(
        update={
            "blackout_dates": [thursday],
            "showing_window": data.showing_window.model_copy(
                update={"latest_start": at(15).time()}
            ),
        }
    )

    tours = await tours_for_case(lead_tours.BOOKING_TOUR_SOURCE, data, NOW)

    assert {slot.unit for slot in tours.open_times} == {UNIT, "412-01"}
    for slot in tours.open_times:
        assert within_showing_rules(slot.start, data, NOW)
        local = slot.start.astimezone(at(9).tzinfo)
        assert local.date() != thursday and local.time() <= at(15).time()
        assert slot.start >= NOW + timedelta(hours=24)


# The automatic-send check with the switch on


def booking_case() -> tuple:
    """A case as the booking service builds it: per-unit times, links and own showings."""
    listings = ListingData(
        timezone="America/New_York",
        listings=[Listing(unit=UNIT), Listing(unit="412-01")],
    )
    links = {
        UNIT: f"https://serniacapital.com/showings/{UNIT}?t=AbCdEfGhIjKlMnOpQrStUv",
        "412-01": "https://serniacapital.com/showings/412-01?t=AbCdEfGhIjKlMnOpQrStUv",
    }
    both = at(16)
    only_412 = at(17)
    case = build_case(
        lead_thread(ALEX_THREAD),
        listings,
        now=NOW,
        slots=[
            TourSlot(start=both, unit=UNIT),
            TourSlot(start=both, unit="412-01"),
            TourSlot(start=only_412, unit="412-01"),
        ],
        booked_tours=[BookedTour(unit=UNIT, start=at(15))],
        booking_links=links,
        own_showings=[OwnShowing(unit=UNIT, start=at(18), status="booked")],
    )
    return case, links, both, only_412


def test_the_send_check_accepts_the_lead_link_and_refuses_any_other():
    case, links, _, _ = booking_case()

    def problems(body: str) -> list[str]:
        return reply_problems(case, decision(reply_body=f"Hello,\n\n{body}{SIGNED}"))

    assert problems(f"Book here: {links[UNIT]}.") == []
    assert problems(f"Or here: {links['412-01']}") == []
    link_problem = ["The reply contains a link that was not supplied in the case or policy."]
    assert problems(f"https://serniacapital.com/showings/{UNIT}") == link_problem
    assert problems(f"https://serniacapital.com/showings/{UNIT}?t=SomeoneElsesToken000") == (
        link_problem
    )
    assert problems("https://attacker.example/collect") == link_problem


def test_the_send_check_accepts_a_units_own_time_and_refuses_a_time_not_in_the_case():
    case, _, both, only_412 = booking_case()
    time_problem = ["The reply offers a time outside the supplied availability."]

    def problems(unit: str | None, *starts: datetime) -> list[str]:
        return reply_problems(
            case,
            decision(lead={"unit": unit}, offered_slots=[start.isoformat() for start in starts]),
        )

    assert problems(UNIT, both, at(15), at(18)) == []  # open, a join, the lead's own
    assert problems("412-01", both, only_412) == []
    # 5:00 is open at 412-01 only, so it cannot be offered for 320-03.
    assert problems(UNIT, only_412) == time_problem
    assert problems("412-01", at(15)) == time_problem  # a join at the other unit
    # A reply that names no unit (or one not listed) may offer any time the case offers.
    assert problems(None, only_412, at(15)) == []
    assert problems("Somewhere else", only_412) == []
    assert problems(None, at(19)) == time_problem
    assert problems(UNIT, (both + timedelta(days=1))) == time_problem


def test_the_evals_and_the_send_check_agree_on_tour_times():
    """One calculation (``case.allowed_tour_times``) behind both, so they cannot drift."""
    case, _, both, only_412 = booking_case()
    own = at(18)  # the lead's own showing, full: not among the showings with room
    assert own not in {tour.start for tour in case.booked_tours}

    def evals_allow(unit: str | None, *starts: datetime) -> bool:
        reply = decision(lead={"unit": unit}, offered_slots=[s.isoformat() for s in starts])
        return check(case, reply)["offered_slots_allowed"]

    def send_allows(unit: str | None, *starts: datetime) -> bool:
        reply = decision(lead={"unit": unit}, offered_slots=[s.isoformat() for s in starts])
        return not reply_problems(case, reply)

    # Unit 412-01's time offered for 320-03 fails both.
    assert evals_allow(UNIT, only_412) is False
    # Repeating the lead's own booked showing, which has no room left, passes both.
    assert evals_allow(UNIT, own) is True
    combos = [
        (unit, starts)
        for unit in (UNIT, "412-01", None, "Somewhere else")
        for starts in ((both,), (only_412,), (at(15),), (own,), (at(19),), (both, only_412))
    ]
    for unit, starts in combos:
        assert evals_allow(unit, *starts) == send_allows(unit, *starts), (unit, starts)


def test_an_own_showing_says_whether_the_leads_details_are_in():
    """Not yet only for the provisional place a reply booked, the one the link can complete."""
    from api.src.showing_bookings.models import Showing, ShowingAttendee

    showing = Showing(unit=UNIT, starts_at=at(16), status="scheduled")

    def own(source: str, consent: datetime | None) -> OwnShowing:
        attendee = ShowingAttendee(source=source, status="booked", texts_consent_at=consent)
        return lead_tours._own(attendee, showing)

    assert own("lead_agent", None).details_added is False
    assert own("lead_agent", NOW).details_added is True  # completed through the link
    assert own("page", NOW).details_added is True

    case = build_case(
        weekly_thread(weekly_message(0)),
        WEEKLY_LISTING_DATA,
        upto=0,
        own_showings=[own("lead_agent", None)],
    )
    assert f"- {UNIT}: Wednesday, April 14 at 4:00 PM, booked, details not yet added" in (
        render_case(case)
    )


def test_the_rendered_case_lists_times_by_unit_and_the_own_showing():
    case, links, _, _ = booking_case()
    text = render_case(case)
    assert (
        "## Tour times that may be offered\n"
        f"- {UNIT}, Wednesday, April 14: 4:00 PM\n"
        "- 412-01, Wednesday, April 14: 4:00 PM, 5:00 PM\n"
    ) in text
    assert f"{OWN_SHOWINGS_HEADING}\n- {UNIT}: Wednesday, April 14 at 6:00 PM, booked" in text
    assert f"## Booking links\n- {UNIT}: {links[UNIT]}\n- 412-01: {links['412-01']}" in text


# The saved case


def test_the_saved_case_round_trips_without_staff_names_or_addresses():
    case = build_case(
        weekly_thread(weekly_message(0, body="Is it available?")),
        WEEKLY_LISTING_DATA,
        upto=0,
        own_showings=[
            OwnShowing(
                unit="12-A",
                start=datetime(2026, 5, 6, 10, 0, tzinfo=ET),
                status="cancelled",
                cancelled_by="team",
            )
        ],
    )
    assert {slot.agent for slot in case.offerable_slots} == {"Alex Agent", "Blair Agent"}

    record = json.loads(json.dumps(case_record(case)))
    rebuilt = case_from_record(record)

    assert render_case(rebuilt) == render_case(case)
    assert rebuilt.model_dump(
        exclude={"offerable_slots": {"__all__": {"agent"}}, "thread": True}
    ) == case.model_dump(exclude={"offerable_slots": {"__all__": {"agent"}}, "thread": True})
    assert [m.body for m in rebuilt.thread.messages] == ["Is it available?"]
    saved = json.dumps(record)
    assert "Alex Agent" not in saved and "Blair" not in saved
    assert "lead@convo.zillow.com" not in saved
    assert "cancelled by the team" in render_case(rebuilt)
