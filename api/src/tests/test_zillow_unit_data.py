"""How a live case gets its listings from the rental units tables.

Uses the test database. All unit data here is invented.
"""

from datetime import date, time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_units import service
from api.src.rental_units.models import RentalUnitRecord
from api.src.rental_units.schema import PreferredWindow, RentalUnit, ShowingRules
from api.src.sernia_ai.zillow_leads import unit_data
from api.src.sernia_ai.zillow_leads.case import Listing, ShowingWindow, UnitData
from api.src.tests.test_rental_units import clear_rental_data

AFTERNOONS = PreferredWindow(days=["mon", "tue"], start=time(15), end=time(18))
UNIT = RentalUnit(
    unit="12-A",
    address="12 Example St #A",
    rent=1500,
    lease_start_earliest=date(2027, 7, 1),
    lease_start_latest=date(2027, 8, 1),
    virtual_tours=True,
    rented_on=date(2027, 6, 1),
    booking_url="https://book.example.com/12-a",
    preferred_times=[AFTERNOONS],
    public_facts=["1 bedroom, second floor", "Dishwasher"],
    internal_notes=["Resident wants 48 hours' notice where possible"],
)
RULES = ShowingRules(
    earliest_start=time(10),
    latest_start=time(17),
    blackout_dates=[date(2027, 7, 4)],
    booking_url="https://book.example.com",
)


@pytest_asyncio.fixture
async def saved():
    await clear_rental_data()
    async with AsyncSessionFactory() as session:
        await service.save_unit(session, UNIT, changed_by="setup")
        await service.save_showing_rules(session, RULES, changed_by="setup")
    yield
    await clear_rental_data()


class TestShape:
    def test_every_field_reaches_the_case_builder(self):
        data = unit_data.as_unit_data([UNIT], RULES)

        assert data == UnitData(
            listings=[
                Listing(
                    unit="12-A",
                    address="12 Example St #A",
                    rent=1500,
                    lease_start_earliest=date(2027, 7, 1),
                    lease_start_latest=date(2027, 8, 1),
                    virtual_tours=True,
                    rented_on=date(2027, 6, 1),
                    booking_url="https://book.example.com/12-a",
                    facts="1 bedroom, second floor\nDishwasher",
                    internal_notes="Resident wants 48 hours' notice where possible",
                    preferred_times=[AFTERNOONS],
                )
            ],
            booking_url="https://book.example.com",
            showing_window=ShowingWindow(earliest_start=time(10), latest_start=time(17)),
            blackout_dates=[date(2027, 7, 4)],
        )
        # Production keeps no tour times by hand; they come from the tour source.
        assert data.availability == []

    def test_every_unit_field_is_carried_over_or_deliberately_not(self):
        """A field added to a unit must be mapped here, or the lead agent never sees it."""
        mapped = {
            "unit",
            "address",
            "rent",
            "lease_start_earliest",
            "lease_start_latest",
            "virtual_tours",
            "rented_on",
            "booking_url",
            "preferred_times",
            "public_facts",
            "internal_notes",
        }
        assert set(RentalUnit.model_fields) == mapped

    def test_the_defaults_match_what_the_evals_have_always_used(self):
        assert unit_data.as_unit_data([], ShowingRules()) == UnitData()


class TestLoad:
    @pytest.mark.usefixtures("saved")
    @pytest.mark.asyncio
    async def test_it_reads_what_is_saved(self):
        loaded = await unit_data.load()

        assert loaded.problem is None
        assert loaded.data == unit_data.as_unit_data([UNIT], RULES)

    @pytest.mark.asyncio
    async def test_with_nothing_saved_there_is_no_data_and_no_problem(self):
        await clear_rental_data()

        assert await unit_data.load() == unit_data.Loaded()

    @pytest.mark.usefixtures("saved")
    @pytest.mark.asyncio
    async def test_a_row_that_fails_its_checks_makes_all_of_it_unusable(self):
        async with AsyncSessionFactory() as session:
            await session.execute(update(RentalUnitRecord).values(rent=-1))
            await session.commit()

        loaded = await unit_data.load()

        assert loaded.data == UnitData()
        assert "rent must be more than 0" in loaded.problem

    @pytest.mark.asyncio
    async def test_a_database_that_cannot_be_read_is_reported_and_never_raises(self, monkeypatch):
        monkeypatch.setattr(service, "read_units", AsyncMock(side_effect=RuntimeError("no db")))

        loaded = await unit_data.load()

        assert loaded.data == UnitData() and loaded.problem == "no db"
