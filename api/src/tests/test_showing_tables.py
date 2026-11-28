"""The rules the showing tables enforce themselves, against the test database.

- one scheduled showing per unit and start time, and several attendees in it;
- no overlapping scheduled showings for one agent;
- no duplicate notices for one kind, showing and recipient;

and the models in code match what migration 0042 built.
"""

from datetime import timedelta

import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api.src.database.database import AsyncSessionFactory, Base, engine
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.tests.showing_helpers import (
    PROSPECT_PHONE,
    add_agent,
    add_attendee,
    add_showing,
    at,
    clear_showing_data,
)

TABLES = {
    "calendar_connections",
    "showing_agents",
    "showings",
    "showing_attendees",
    "showing_notices",
    "lead_contact_jobs",
}


@pytest_asyncio.fixture(autouse=True)
async def clean():
    await clear_showing_data()
    yield
    await clear_showing_data()


async def refused_by(constraint: str, coroutine) -> None:
    with pytest.raises(IntegrityError) as refused:
        await coroutine
    assert constraint in str(refused.value)


async def cancel(showing_id: int) -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(Showing).where(Showing.id == showing_id).values(status="cancelled")
        )
        await session.commit()


# One scheduled showing per unit and start time


@pytest.mark.asyncio
async def test_a_second_showing_at_the_same_unit_and_time_is_refused():
    jane, sam = await add_agent(), await add_agent("Sam Rivera", "sam.rivera@example.com")
    await add_showing(jane)

    await refused_by("uq_showings_unit_starts_at_scheduled", add_showing(sam))


@pytest.mark.asyncio
async def test_a_cancelled_showing_does_not_hold_its_time():
    jane = await add_agent()
    await cancel(await add_showing(jane))

    assert await add_showing(jane)


@pytest.mark.asyncio
async def test_other_units_can_have_showings_at_the_same_time():
    jane, sam = await add_agent(), await add_agent("Sam Rivera", "sam.rivera@example.com")
    await add_showing(jane)

    assert await add_showing(sam, unit="320-04")


@pytest.mark.asyncio
async def test_two_people_join_one_showing_as_two_attendees():
    showing = await add_showing(await add_agent())
    await add_attendee(showing)
    await add_attendee(showing, mobile="+14125550118", name="Robin Example")

    async with AsyncSessionFactory() as session:
        attendees = (
            await session.scalars(
                select(ShowingAttendee).where(ShowingAttendee.showing_id == showing)
            )
        ).all()
    assert len(attendees) == 2


@pytest.mark.asyncio
async def test_one_mobile_is_booked_once_per_showing():
    showing = await add_showing(await add_agent())
    first = await add_attendee(showing)

    await refused_by("uq_showing_attendees_showing_id_mobile_booked", add_attendee(showing))

    # Once that booking is cancelled, the same number can book again.
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAttendee).where(ShowingAttendee.id == first).values(status="cancelled")
        )
        await session.commit()
    assert await add_attendee(showing, mobile=PROSPECT_PHONE)


@pytest.mark.asyncio
async def test_a_lead_booked_in_an_email_thread_needs_no_mobile():
    """The lead agent can agree a time before the lead has given a number or an address."""
    showing = await add_showing(await add_agent())
    attendee = await add_attendee(showing, mobile=None, thread="thread-a", source="lead_agent")

    async with AsyncSessionFactory() as session:
        saved = await session.get(ShowingAttendee, attendee)
    assert (saved.mobile, saved.email, saved.source) == (None, None, "lead_agent")


@pytest.mark.asyncio
async def test_two_leads_without_a_mobile_from_different_threads_join_one_showing():
    showing = await add_showing(await add_agent())
    await add_attendee(showing, mobile=None, thread="thread-a", source="lead_agent")

    assert await add_attendee(showing, mobile=None, thread="thread-b", source="lead_agent")


@pytest.mark.asyncio
async def test_one_zillow_thread_is_booked_once_per_showing():
    showing = await add_showing(await add_agent())
    first = await add_attendee(showing, mobile=None, thread="thread-a", source="lead_agent")

    await refused_by(
        "uq_showing_attendees_showing_id_zillow_thread_id_booked",
        add_attendee(showing, mobile="+14125550118", thread="thread-a"),
    )

    # Once that booking is cancelled, the thread can book again; another showing always can.
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(ShowingAttendee).where(ShowingAttendee.id == first).values(status="cancelled")
        )
        await session.commit()
    assert await add_attendee(showing, mobile=None, thread="thread-a", source="lead_agent")
    other = await add_showing(
        await add_agent("Sam Rivera", "sam.rivera@example.com"), unit="320-04"
    )
    assert await add_attendee(other, mobile=None, thread="thread-a", source="lead_agent")


@pytest.mark.asyncio
async def test_an_unknown_attendee_source_is_refused():
    showing = await add_showing(await add_agent())
    await refused_by("ck_showing_attendees_source", add_attendee(showing, source="website"))


@pytest.mark.asyncio
async def test_every_attendee_gets_their_own_cancel_token():
    showing = await add_showing(await add_agent())
    ids = [await add_attendee(showing, mobile=f"+1412555011{n}") for n in range(3)]

    async with AsyncSessionFactory() as session:
        tokens = [(await session.get(ShowingAttendee, i)).cancel_token for i in ids]
    assert len(set(tokens)) == 3 and all(len(token) >= 32 for token in tokens)


# No overlapping showings for one agent


@pytest.mark.asyncio
async def test_an_agent_cannot_have_overlapping_showings():
    jane = await add_agent()
    await add_showing(jane, starts_at=at(16))

    await refused_by(
        "ex_showings_agent_overlap", add_showing(jane, unit="320-04", starts_at=at(16, 15))
    )


@pytest.mark.asyncio
async def test_back_to_back_showings_are_allowed():
    jane = await add_agent()
    await add_showing(jane, starts_at=at(16))

    assert await add_showing(jane, unit="320-04", starts_at=at(16, 30))


@pytest.mark.asyncio
async def test_a_cancelled_showing_does_not_block_its_agent():
    jane = await add_agent()
    await cancel(await add_showing(jane, starts_at=at(16)))

    assert await add_showing(jane, unit="320-04", starts_at=at(16, 15))


@pytest.mark.asyncio
async def test_two_agents_can_show_at_the_same_time():
    jane, sam = await add_agent(), await add_agent("Sam Rivera", "sam.rivera@example.com")
    await add_showing(jane, starts_at=at(16))

    assert await add_showing(sam, unit="320-04", starts_at=at(16))


@pytest.mark.asyncio
async def test_moving_a_showing_onto_another_of_the_agents_showings_is_refused():
    jane = await add_agent()
    await add_showing(jane, starts_at=at(16))
    later = await add_showing(jane, unit="320-04", starts_at=at(17))

    async def move():
        async with AsyncSessionFactory() as session:
            await session.execute(
                update(Showing)
                .where(Showing.id == later)
                .values(starts_at=at(16, 10), ends_at=at(16, 10) + timedelta(minutes=30))
            )
            await session.commit()

    await refused_by("ex_showings_agent_overlap", move())


# No duplicate notices


@pytest.mark.asyncio
async def test_a_notice_for_the_same_kind_showing_and_recipient_is_refused():
    showing = await add_showing(await add_agent())

    async def add_notice():
        async with AsyncSessionFactory() as session:
            session.add(
                ShowingNotice(
                    kind="resident_new_showing",
                    channel="sms",
                    showing_id=showing,
                    recipient="+14125550142",
                    recipient_key="resident:CT_example",
                    payload={"body": "A showing is booked."},
                    not_before=at(9),
                )
            )
            await session.commit()

    await add_notice()
    await refused_by("uq_showing_notices_kind_showing_id_recipient_key", add_notice())


# The models match the migration


@pytest.mark.asyncio
async def test_the_models_match_what_the_migration_built():
    def only_showing_tables(obj, name, type_, reflected, compare_to):
        table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
        return table in TABLES

    def differences(connection):
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "include_object": only_showing_tables},
        )
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == []
    assert TABLES <= set(Base.metadata.tables)
