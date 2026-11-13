"""Rental units: what may be saved, and that every save is in the history.

Uses the test database. All unit data here is invented.
"""

from datetime import date, time

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete, select, update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_units import service
from api.src.rental_units.checks import RentalDataInvalid, rules_problems, unit_problems
from api.src.rental_units.models import RentalUnitChange, RentalUnitRecord
from api.src.rental_units.schema import PreferredWindow, RentalUnit, ShowingRules
from api.src.sernia_ai.models import AppSetting

UNIT = RentalUnit(
    unit="12-A",
    address="12 Example St #A",
    rent=1500,
    lease_start_earliest=date(2027, 7, 1),
    lease_start_latest=date(2027, 8, 1),
    booking_url="https://book.example.com/12-a",
    preferred_times=[PreferredWindow(days=["mon", "tue"], start=time(15, 0), end=time(18, 0))],
    public_facts=["1 bedroom, second floor", "Dishwasher", "Built 1925, renovated 2019"],
    internal_notes=["Resident wants 48 hours' notice where possible"],
)


async def clear_rental_data() -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(delete(RentalUnitChange))
        await session.execute(delete(RentalUnitRecord))
        await session.execute(delete(AppSetting).where(AppSetting.key == service.SHOWING_RULES_KEY))
        await session.commit()


@pytest_asyncio.fixture
async def session():
    await clear_rental_data()
    async with AsyncSessionFactory() as db:
        yield db
    await clear_rental_data()


class TestChecks:
    def test_an_ordinary_unit_and_the_default_rules_pass(self):
        assert unit_problems(UNIT) == []
        assert unit_problems(RentalUnit(unit="12-B")) == []
        assert rules_problems(ShowingRules()) == []

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("Call Sam at 412-555-0134 to see it", "phone number"),
            ("Call (412) 555 0134", "phone number"),
            ("Questions: sam@example.com", "email address"),
            ("Floor plan at https://example.com/plan.pdf", "link"),
            ("More at www.example.com", "link"),
            ("Lockbox on the back door", "access code"),
            ("Door code is 4821", "access code"),
            ("Wifi password on the fridge", "access code"),
        ],
    )
    def test_text_that_would_matter_if_it_leaked_is_refused_in_facts_and_notes(self, text, reason):
        as_fact = unit_problems(RentalUnit(unit="12-A", public_facts=["Dishwasher", text]))
        as_note = unit_problems(RentalUnit(unit="12-A", internal_notes=[text]))

        assert len(as_fact) == 1 and reason in as_fact[0] and "public fact" in as_fact[0]
        assert len(as_note) == 1 and reason in as_note[0] and "internal note" in as_note[0]

    def test_a_low_stakes_internal_note_passes(self):
        unit = RentalUnit(
            unit="12-A",
            internal_notes=[
                "Resident wants 48 hours' notice where possible",
                "Lease start can move to June 15 if the resident leaves early; check with us",
            ],
        )
        assert unit_problems(unit) == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://book.example.com/12-a",  # not https
            "book.example.com/12-a",
            "https://user:secret@book.example.com/",
            "https://book.example.com/a b",
            "javascript:alert(1)",
        ],
    )
    def test_a_booking_link_must_be_a_plain_https_address(self, url):
        assert unit_problems(RentalUnit(unit="12-A", booking_url=url))
        assert rules_problems(ShowingRules(booking_url=url))

    @pytest.mark.parametrize(
        "unit",
        [
            RentalUnit(unit=" "),
            RentalUnit(unit=" 12-A"),
            RentalUnit(unit="12-A", address="12 Example St, call 412-555-0134"),
            RentalUnit(unit="12-A", rent=0),
            RentalUnit(
                unit="12-A",
                lease_start_earliest=date(2027, 8, 1),
                lease_start_latest=date(2027, 7, 1),
            ),
            RentalUnit(unit="12-A", public_facts=["x" * 301]),
            RentalUnit(unit="12-A", public_facts=["Two\nlines"]),
            RentalUnit(unit="12-A", internal_notes=[""]),
            RentalUnit(unit="12-A", public_facts=["A fact"] * 61),
            RentalUnit(
                unit="12-A",
                preferred_times=[PreferredWindow(days=[], start=time(9), end=time(10))],
            ),
            RentalUnit(
                unit="12-A",
                preferred_times=[PreferredWindow(days=["mon"], start=time(12), end=time(10))],
            ),
        ],
    )
    def test_a_unit_with_values_that_make_no_sense_is_refused(self, unit):
        assert unit_problems(unit)

    @pytest.mark.parametrize(
        "rules",
        [
            ShowingRules(timezone="Mars/Olympus"),
            ShowingRules(earliest_start=time(18), latest_start=time(9)),
        ],
    )
    def test_rules_that_make_no_sense_are_refused(self, rules):
        assert rules_problems(rules)

    def test_an_unknown_field_is_an_error_not_a_dropped_value(self):
        """Dropped silently, ``rentedOn`` would leave a rented unit on offer."""
        with pytest.raises(ValidationError):
            RentalUnit.model_validate({"unit": "12-A", "rentedOn": "2027-06-01"})
        with pytest.raises(ValidationError):
            ShowingRules.model_validate({"earliestStart": "09:00"})


class TestUnits:
    @pytest.mark.asyncio
    async def test_a_saved_unit_reads_back_as_it_was_saved(self, session):
        await service.save_unit(session, UNIT, changed_by="Pat")

        assert await service.read_units(session) == [UNIT]
        assert await service.read_unit(session, " 12-a ") == UNIT
        assert await service.read_unit(session, "12-Z") is None

    @pytest.mark.asyncio
    async def test_saving_a_unit_again_replaces_it_whatever_the_case_of_its_name(self, session):
        await service.save_unit(session, UNIT, changed_by="Pat")
        rented = UNIT.model_copy(update={"unit": "12-a", "rented_on": date(2027, 6, 1)})

        await service.save_unit(session, rented, changed_by="Pat")

        assert await service.read_units(session) == [rented]

    @pytest.mark.asyncio
    async def test_units_come_back_in_name_order(self, session):
        for name in ("14-B", "12-A", "12-C"):
            await service.save_unit(session, RentalUnit(unit=name), changed_by="Pat")

        assert [unit.unit for unit in await service.read_units(session)] == ["12-A", "12-C", "14-B"]

    @pytest.mark.asyncio
    async def test_a_unit_that_fails_its_checks_is_not_saved_or_recorded(self, session):
        bad = UNIT.model_copy(update={"public_facts": ["Door code is 4821"]})

        with pytest.raises(RentalDataInvalid, match="access code"):
            await service.save_unit(session, bad, changed_by="Pat")

        assert await service.read_units(session) == []
        assert await service.read_history(session) == []

    @pytest.mark.asyncio
    async def test_a_row_changed_behind_the_service_s_back_is_refused_on_read(self, session):
        """Someone edits the table by hand: readers must not get the bad value."""
        await service.save_unit(session, UNIT, changed_by="Pat")
        await session.execute(
            update(RentalUnitRecord).values(internal_notes=["Lockbox code 4821 on the rail"])
        )
        await session.commit()

        with pytest.raises(RentalDataInvalid, match="access code"):
            await service.read_units(session)


class TestShowingRules:
    @pytest.mark.asyncio
    async def test_with_nothing_saved_the_rules_are_the_defaults(self, session):
        rules = await service.read_showing_rules(session)

        assert rules == ShowingRules()
        assert (rules.earliest_start, rules.latest_start) == (time(9, 0), time(18, 0))

    @pytest.mark.asyncio
    async def test_saved_rules_read_back_and_can_be_replaced(self, session):
        rules = ShowingRules(earliest_start=time(10), blackout_dates=[date(2027, 7, 4)])
        await service.save_showing_rules(session, rules, changed_by="Pat")
        assert await service.read_showing_rules(session) == rules

        later = rules.model_copy(update={"blackout_dates": []})
        await service.save_showing_rules(session, later, changed_by="Pat")
        assert await service.read_showing_rules(session) == later

    @pytest.mark.asyncio
    async def test_rules_that_fail_their_checks_are_not_saved(self, session):
        with pytest.raises(RentalDataInvalid):
            await service.save_showing_rules(
                session, ShowingRules(timezone="Mars/Olympus"), changed_by="Pat"
            )
        assert await service.read_showing_rules(session) == ShowingRules()

    @pytest.mark.asyncio
    async def test_a_settings_row_of_the_wrong_shape_is_refused_on_read(self, session):
        session.add(AppSetting(key=service.SHOWING_RULES_KEY, value={"earliestStart": "09:00"}))
        await session.commit()

        with pytest.raises(RentalDataInvalid):
            await service.read_showing_rules(session)


class TestHistory:
    @pytest.mark.asyncio
    async def test_every_save_is_recorded_with_who_where_and_the_result(self, session):
        await service.save_unit(session, UNIT, changed_by="Pat", source="conversation-1")
        rented = UNIT.model_copy(update={"rented_on": date(2027, 6, 1)})
        await service.save_unit(session, rented, changed_by="Sam", source="conversation-2")

        newest, oldest = await service.read_history(session, "12-a")

        assert (newest.changed_by, newest.source) == ("Sam", "conversation-2")
        assert newest.snapshot == rented.model_dump(mode="json")
        assert oldest.snapshot == UNIT.model_dump(mode="json")
        assert newest.changed_at >= oldest.changed_at
        # A snapshot is a whole unit, so any past state can be read back as one.
        assert RentalUnit.model_validate(oldest.snapshot) == UNIT

    @pytest.mark.asyncio
    async def test_showing_rules_changes_are_in_the_same_history(self, session):
        await service.save_unit(session, UNIT, changed_by="Pat")
        rules = ShowingRules(blackout_dates=[date(2027, 7, 4)])
        await service.save_showing_rules(session, rules, changed_by="Pat")

        everything = await service.read_history(session)
        (rules_change,) = await service.read_history(session, service.SHOWING_RULES_SUBJECT)

        assert [change.subject for change in everything] == ["showing_rules", "12-A"]
        assert ShowingRules.model_validate(rules_change.snapshot) == rules
        assert len(await service.read_history(session, limit=1)) == 1

    @pytest.mark.asyncio
    async def test_history_outlives_the_unit(self, session):
        await service.save_unit(session, UNIT, changed_by="Pat")
        await session.execute(delete(RentalUnitRecord))
        await session.commit()

        (change,) = await service.read_history(session, "12-A")
        unit_id = (await session.execute(select(RentalUnitChange.unit_id))).scalar_one()

        assert change.snapshot["unit"] == "12-A" and unit_id is None
