"""The Zillow lead agent's tour source, and the switch that keeps it off.

With the switch off, or the settings unreadable, the lead agent gets its own
``NoTours`` source exactly as before. With it on, it gets the booking
service's open times, which still pass through ``tours_for_case``. Uses the
test database and an ``InMemoryCalendar``; no model and no Gmail.
"""

import uuid
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import TypeAdapter
from sqlalchemy import delete, select

from api.src.agent_conversations.models import AgentConversation
from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import connections, lead_tours, open_times
from api.src.showing_bookings.fakes import InMemoryCalendar
from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.tests.showing_helpers import (
    LOCAL,
    add_attendee,
    add_showing,
    clear_showing_data,
    store_settings,
)
from api.src.zillow_leads import handle
from api.src.zillow_leads import listing_data as listing_data_module
from api.src.zillow_leads.agent import LeadOutcome
from api.src.zillow_leads.case import BookedTour
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.thread import LeadMessage, LeadThread
from api.src.zillow_leads.tours import NoTours, tours_for_case

NOW = datetime.combine(date(2027, 4, 13), time(14, 0), LOCAL)
THURSDAY = date(2027, 4, 15)


def at(hour: int, minute: int = 0, day: date = THURSDAY) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL)


class Data:
    def __init__(self) -> None:
        self.listings = [RentalListing(unit="320-03", address="320 Example St")]
        self.rules = ShowingRules()
        self.calendar = InMemoryCalendar()


@pytest_asyncio.fixture
async def data(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    open_times.forget_free_busy()
    await clear_showing_data()
    state = Data()

    async def read_listings(session):
        return list(state.listings)

    async def read_rules(session, lock=False):
        return state.rules

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    monkeypatch.setattr(connections, "calendar_for_connection", lambda row: state.calendar)
    monkeypatch.setattr(connections, "unusable_reason", lambda row: None)
    async with AsyncSessionFactory() as session:
        session.add(
            CalendarConnection(
                google_email="team@example.com",
                google_subject="100000000000000000001",
                connected_by="pat@serniacapital.com",
                connected_at=NOW - timedelta(days=30),
            )
        )
        session.add(
            ShowingAgentRecord(
                name="Jane Doe",
                email="jane.doe@example.com",
                busy_calendar_ids=["jane.doe@example.com"],
                weekly_hours=[{"days": ["thu"], "start": "10:00", "end": "12:00"}],
            )
        )
        await session.commit()
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def jane_id() -> int:
    async with AsyncSessionFactory() as session:
        return (await session.scalars(select(ShowingAgentRecord.id))).one()


# The tour source


@pytest.mark.asyncio
async def test_open_times_and_joinable_showings_pass_tours_for_case_unchanged(data):
    showing = await add_showing(await jane_id(), unit="320-03", starts_at=at(11, 0))
    await add_attendee(showing)
    listing_data = as_listing_data(data.listings, data.rules)
    source = lead_tours.BookingTourSource()

    raw = await source.tours(listing_data, NOW)
    kept = await tours_for_case(source, listing_data, NOW)

    starts = [slot.start for slot in raw.open_times]
    assert [start.astimezone(LOCAL).strftime("%a %H:%M") for start in starts[:5]] == [
        "Thu 10:00",
        "Thu 10:30",
        "Thu 11:00",
        "Thu 11:30",
        "Thu 12:00",
    ]
    assert {slot.agent for slot in raw.open_times} == {"Jane Doe"}
    assert raw.booked[0] == BookedTour(unit="320-03", start=at(11, 0))
    # Nothing the source gives is outside the lead agent's own rules.
    assert kept.open_times == raw.open_times
    assert kept.booked == raw.booked


@pytest.mark.asyncio
async def test_a_time_is_listed_only_when_every_unit_on_offer_can_take_it(data):
    data.listings.append(
        RentalListing(unit="412-01", address="412 Example St", showing_notice_hours=48)
    )
    data.listings.append(
        RentalListing(unit="500-02", address="500 Example St", off_market_on=date(2027, 4, 1))
    )
    listing_data = as_listing_data(data.listings, data.rules)

    tours = await lead_tours.BookingTourSource().tours(listing_data, NOW)

    days = {slot.start.astimezone(LOCAL).date() for slot in tours.open_times}
    # 412-01 needs 48 hours, which rules out Thursday morning for both units;
    # 500-02 is off the market and does not count.
    assert days == {date(2027, 4, 22)}


@pytest.mark.asyncio
async def test_no_units_on_offer_means_no_tours(data):
    data.listings = [RentalListing(unit="320-03", off_market_on=date(2027, 4, 1))]
    tours = await lead_tours.BookingTourSource().tours(
        as_listing_data(data.listings, data.rules), NOW
    )
    assert tours.open_times == [] and tours.booked == []


# The switch


@pytest.mark.asyncio
async def test_the_switch_decides_the_source_and_off_or_unreadable_means_no_tours(
    data, monkeypatch
):
    fallback = NoTours()
    assert await lead_tours.tour_source_for_lead_agent(fallback) is fallback  # no row

    await store_settings(lead_agent_uses_open_times=False)
    assert await lead_tours.tour_source_for_lead_agent(fallback) is fallback

    await store_settings(lead_agent_uses_open_times="yes")  # not a true or false
    assert await lead_tours.tour_source_for_lead_agent(fallback) is fallback

    await store_settings(lead_agent_uses_open_times=True)
    assert await lead_tours.tour_source_for_lead_agent(fallback) is lead_tours.BOOKING_TOUR_SOURCE

    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(lead_tours, "AsyncSessionFactory", broken)
    assert await lead_tours.tour_source_for_lead_agent(fallback) is fallback


# The lead agent, end to end around the model


def lead_thread() -> LeadThread:
    return LeadThread(
        mailbox="all@serniacapital.com",
        thread_id="T",
        messages=[
            LeadMessage(
                message_id="m0",
                rfc_message_id="<m0@x>",
                sent_at=NOW - timedelta(minutes=10),
                from_address="noreply@mail.zillow.com",
                reply_to="abc123@convo.zillow.com",
                from_sernia=False,
                subject="320 Example St inquiry",
                body="Can I see it Thursday?",
            )
        ],
    )


@pytest_asyncio.fixture
async def lead_run(monkeypatch, data):
    conversation_id = f"test-lead-{uuid.uuid4().hex}"
    loaded = listing_data_module.Loaded(data=as_listing_data(data.listings, data.rules))
    monkeypatch.setattr(listing_data_module, "load", AsyncMock(return_value=loaded))
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=lead_thread()))
    monkeypatch.setattr(handle, "notify_pending_approval", AsyncMock())
    monkeypatch.setattr(handle, "send_reply", AsyncMock(return_value="Email sent."))
    no_reply = TypeAdapter(LeadOutcome).validate_python(
        {"lead": {}, "tour": {}, "action": "no_reply", "reason": "Test."}
    )
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=no_reply))
    # The lead agent reads the clock; the case is built for the worked example's Tuesday.
    monkeypatch.setattr(handle, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    spy = AsyncMock(wraps=lead_tours.BOOKING_TOUR_SOURCE.tours)
    monkeypatch.setattr(lead_tours.BOOKING_TOUR_SOURCE, "tours", spy)

    async def run():
        await handle.handle_thread("T", conversation_id=conversation_id, metadata={})
        (case,) = handle.respond.call_args.args
        return case, spy

    yield run
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AgentConversation).where(AgentConversation.id == conversation_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_with_the_switch_off_the_lead_agent_offers_no_times_as_today(lead_run):
    await store_settings(send_mode="capture", lead_agent_uses_open_times=False)
    assert handle.TOUR_SOURCE.__class__ is NoTours

    case, spy = await lead_run()

    spy.assert_not_called()
    assert case.offerable_slots == [] and case.booked_tours == []


@pytest.mark.asyncio
async def test_with_the_settings_unreadable_the_lead_agent_offers_no_times(lead_run):
    await store_settings(send_mode="sometimes")

    case, spy = await lead_run()

    spy.assert_not_called()
    assert case.offerable_slots == []


@pytest.mark.asyncio
async def test_with_the_switch_on_the_lead_agent_offers_open_times(lead_run):
    await store_settings(lead_agent_uses_open_times=True)

    case, spy = await lead_run()

    spy.assert_called_once()
    assert case.offerable_slots
    assert {slot.start.astimezone(LOCAL).weekday() for slot in case.offerable_slots} == {3}
