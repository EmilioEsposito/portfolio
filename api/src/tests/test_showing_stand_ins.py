"""The stand-ins for Quo and the calendars, and the made-up seed data later tests build on."""

from datetime import date, time, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from api import seed_db
from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings.fakes import InMemoryCalendar, RecordingTextSender
from api.src.showing_bookings.models import ShowingAgentRecord
from api.src.showing_bookings.providers import BusyBlock, CalendarUnreadable, EventDraft, NotSent
from api.src.showing_bookings.schema import normalize_us_mobile
from api.src.showing_bookings.seed import SAMPLE_AGENTS, fill_sample_week
from api.src.tests.showing_helpers import PROSPECT_PHONE, at, clear_showing_data

JANE, SAM = "jane.doe@example.com", "sam.rivera@example.com"


# The text recorder


@pytest.mark.asyncio
async def test_the_recorder_keeps_texts_in_order_and_answers_like_quo():
    texts = RecordingTextSender()
    first = await texts.send_text(PROSPECT_PHONE, "One")
    await texts.send_text("+14125550142", "Two")
    await texts.send_text(PROSPECT_PHONE, "Three")

    assert texts.to(PROSPECT_PHONE) == ["One", "Three"]
    assert (first.provider_id, first.provider_status) == ("AC0001", "queued")
    assert await texts.text_status(first.provider_id) == "delivered"
    texts.report(PROSPECT_PHONE, "undelivered")
    assert await texts.text_status(first.provider_id) == "undelivered"


@pytest.mark.asyncio
async def test_the_recorder_refuses_and_loses_answers_on_request():
    texts = RecordingTextSender()
    texts.refuse(PROSPECT_PHONE, "opted_out")
    texts.lose_answer("+14125550142")

    with pytest.raises(NotSent) as refused:
        await texts.send_text(PROSPECT_PHONE, "Hi")
    assert refused.value.reason == "opted_out" and not refused.value.retryable
    with pytest.raises(TimeoutError):
        await texts.send_text("+14125550142", "Hi")
    # A refused text never went out; a lost answer's text did.
    assert [text.to for text in texts.sent] == ["+14125550142"]


# The in-memory calendar


@pytest.mark.asyncio
async def test_busy_blocks_are_clipped_to_the_window_and_sorted():
    calendar = InMemoryCalendar()
    calendar.add_busy(JANE, at(17), at(17, 30))
    calendar.add_busy(JANE, at(8), at(10))
    calendar.add_busy(JANE, at(20), at(21))

    busy = await calendar.busy_blocks([JANE, SAM], at(9), at(18))

    assert busy == {
        JANE: [BusyBlock(at(9), at(10)), BusyBlock(at(17), at(17, 30))],
        SAM: [],
    }


@pytest.mark.asyncio
async def test_a_lost_calendar_is_named_and_the_others_still_read():
    calendar = InMemoryCalendar()
    calendar.add_busy(SAM, at(13), at(14))
    calendar.lose(JANE)

    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE, SAM], at(9), at(18))
    assert set(unreadable.value.unreadable) == {JANE}
    assert unreadable.value.busy == {SAM: [BusyBlock(at(13), at(14))]}
    with pytest.raises(NotSent):
        await calendar.create_event(JANE, draft())

    calendar.restore(JANE)
    assert await calendar.busy_blocks([JANE], at(9), at(18)) == {JANE: []}


def draft(**fields) -> EventDraft:
    return EventDraft(
        summary="Showing 320-03",
        starts_at=at(16),
        ends_at=at(16, 30),
        attendees=["team.test@example.com"],
        **fields,
    )


@pytest.mark.asyncio
async def test_events_are_created_changed_and_cancelled_in_memory():
    calendar = InMemoryCalendar()
    event_id = await calendar.create_event("showings@example.com", draft())
    await calendar.update_event("showings@example.com", event_id, draft(location="100 Example Ave"))
    await calendar.cancel_event("showings@example.com", event_id)

    (event,) = calendar.events_in("showings@example.com")
    assert (event.draft.location, event.cancelled) == ("100 Example Ave", True)
    assert [entry[0] for entry in calendar.log] == ["create", "update", "cancel"]
    with pytest.raises(NotSent):
        await calendar.update_event("showings@example.com", "evt9999", draft())


# Made-up seed data


def test_the_sample_agents_are_plainly_made_up():
    assert [agent.email for agent in SAMPLE_AGENTS] == [JANE, SAM]
    for agent in SAMPLE_AGENTS:
        assert agent.email.endswith("@example.com")
        assert normalize_us_mobile(agent.mobile)[5:8] == "555"
        assert agent.busy_calendar_ids == [agent.email]


@pytest.mark.asyncio
async def test_the_sample_week_fills_monday_to_saturday_of_the_week_given():
    calendar = fill_sample_week(InMemoryCalendar(), date(2027, 4, 14))  # a Wednesday
    monday, sunday = at(0, day=date(2027, 4, 12)), at(0, day=date(2027, 4, 18))

    busy = await calendar.busy_blocks([JANE, SAM], monday, sunday + timedelta(days=1))

    days = {block.start.date() for blocks in busy.values() for block in blocks}
    assert min(days) == date(2027, 4, 12) and max(days) == date(2027, 4, 17)
    # Jane's Wednesday: busy 5:00-5:30, as in the design's worked example.
    assert BusyBlock(at(17), at(17, 30)) in busy[JANE]
    assert all(block.start.tzinfo is not None for blocks in busy.values() for block in blocks)
    assert all(time(9) <= block.start.time() for block in busy[SAM])


@pytest_asyncio.fixture
async def empty_tables():
    await clear_showing_data()
    yield
    await clear_showing_data()


@pytest.mark.asyncio
async def test_seeding_adds_the_sample_agents_once(empty_tables, monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    await seed_db.seed_showing_agents()
    await seed_db.seed_showing_agents()

    async with AsyncSessionFactory() as session:
        agents = (await session.scalars(select(ShowingAgentRecord))).all()
    assert sorted(agent.email for agent in agents) == [JANE, SAM]
    jane = next(agent for agent in agents if agent.email == JANE)
    assert jane.weekly_hours[0] == {
        "days": ["mon", "tue", "wed", "thu", "fri"],
        "start": "16:00:00",
        "end": "18:30:00",
    }


@pytest.mark.asyncio
async def test_seeding_never_touches_production(empty_tables, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await seed_db.seed_showing_agents()

    async with AsyncSessionFactory() as session:
        assert (await session.scalars(select(ShowingAgentRecord))).all() == []
