"""Rental listings: what may be saved, and that every save is in the history.

Uses the test database. All unit data here is invented.
"""

import asyncio
from datetime import UTC, date, time

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from api.src.database.app_settings import AppSetting
from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service
from api.src.rental_listings.checks import RentalDataInvalid, listing_problems, rules_problems
from api.src.rental_listings.models import RentalListingChange, RentalListingRecord
from api.src.rental_listings.schema import PreferredWindow, RentalListing, ShowingRules

LISTING = RentalListing(
    unit="12-A",
    address="12 Example St #A",
    asking_rent=1500,
    next_lease_start_earliest=date(2027, 7, 1),
    next_lease_start_latest=date(2027, 8, 1),
    showing_booking_url="https://book.example.com/12-a",
    preferred_showing_times=[
        PreferredWindow(days=["mon", "tue"], start=time(15, 0), end=time(18, 0))
    ],
    public_facts=["1 bedroom, second floor", "Dishwasher", "Built 1925, renovated 2019"],
    internal_notes=["Resident wants 48 hours' notice where possible"],
)


async def clear_rental_data() -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(delete(RentalListingChange))
        await session.execute(delete(RentalListingRecord))
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
        assert listing_problems(LISTING) == []
        assert listing_problems(RentalListing(unit="12-B")) == []
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
            # Written as one word, or joined to the word before it.
            ("electricDoor code is 4821", "access code"),
            ("Frontdoor code 4821", "access code"),
            ("Keycode 4821", "access code"),
            ("Gatecode 12", "access code"),
            ("Lockbox4821 by the stairs", "access code"),
            ("Passcode on file", "access code"),
        ],
    )
    def test_text_that_would_matter_if_it_leaked_is_refused_in_facts_and_notes(self, text, reason):
        as_fact = listing_problems(RentalListing(unit="12-A", public_facts=["Dishwasher", text]))
        as_note = listing_problems(RentalListing(unit="12-A", internal_notes=[text]))

        assert len(as_fact) == 1 and reason in as_fact[0] and "public fact" in as_fact[0]
        assert len(as_note) == 1 and reason in as_note[0] and "internal note" in as_note[0]

    @pytest.mark.parametrize(
        "text", ["Dishwasher", "Barcode scanner in the lobby", "Zip code 15213", "Encoded keys"]
    )
    def test_ordinary_text_with_similar_words_passes(self, text):
        assert listing_problems(RentalListing(unit="12-A", public_facts=[text])) == []

    def test_a_low_stakes_internal_note_passes(self):
        unit = RentalListing(
            unit="12-A",
            internal_notes=[
                "Resident wants 48 hours' notice where possible",
                "Lease start can move to June 15 if the resident leaves early; check with us",
            ],
        )
        assert listing_problems(unit) == []

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
        assert listing_problems(RentalListing(unit="12-A", showing_booking_url=url))
        assert rules_problems(ShowingRules(showing_booking_url=url))

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.zillow.com/homedetails/12-Example-St/1_zpid/?view=public",
            "https://zillow.com/rental-manager/properties/1",
            "",
        ],
    )
    @pytest.mark.parametrize(
        "field", ["zillow_listing_url_public_view", "zillow_listing_url_internal_editor_view"]
    )
    def test_a_zillow_ad_link_on_zillow_passes(self, field, url):
        assert listing_problems(RentalListing(unit="12-A", **{field: url})) == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://www.zillow.com/homedetails/1_zpid/",  # not https
            "https://zillow.com.example.com/homedetails/1_zpid/",
            "https://notzillow.com/homedetails/1_zpid/",
            "https://example.com/?u=https://www.zillow.com/",
            "https://user:secret@www.zillow.com/",
            "https://www.zillow.com:8443/homedetails/1_zpid/",
            "https://www.zillow.com:abc/",
            "https://www.zillow.com/a b",
        ],
    )
    @pytest.mark.parametrize(
        "field", ["zillow_listing_url_public_view", "zillow_listing_url_internal_editor_view"]
    )
    def test_a_zillow_ad_link_must_be_plain_https_on_zillow(self, field, url):
        assert listing_problems(RentalListing(unit="12-A", **{field: url}))

    @pytest.mark.parametrize(
        "unit",
        [
            RentalListing(unit=" "),
            RentalListing(unit=" 12-A"),
            RentalListing(unit="12-A", address="12 Example St, call 412-555-0134"),
            RentalListing(unit="12-A", asking_rent=0),
            RentalListing(
                unit="12-A",
                next_lease_start_earliest=date(2027, 8, 1),
                next_lease_start_latest=date(2027, 7, 1),
            ),
            RentalListing(unit="12-A", public_facts=["x" * 301]),
            RentalListing(unit="12-A", public_facts=["Two\nlines"]),
            RentalListing(unit="12-A", internal_notes=[""]),
            RentalListing(unit="12-A", public_facts=["A fact"] * 61),
            RentalListing(
                unit="12-A",
                preferred_showing_times=[PreferredWindow(days=[], start=time(9), end=time(10))],
            ),
            RentalListing(
                unit="12-A",
                preferred_showing_times=[
                    PreferredWindow(days=["mon"], start=time(12), end=time(10))
                ],
            ),
        ],
    )
    def test_a_unit_with_values_that_make_no_sense_is_refused(self, unit):
        assert listing_problems(unit)

    @pytest.mark.parametrize(
        "rules",
        [
            ShowingRules(timezone="Mars/Olympus"),
            ShowingRules(earliest_start=time(18), latest_start=time(9)),
        ],
    )
    def test_rules_that_make_no_sense_are_refused(self, rules):
        assert rules_problems(rules)

    @pytest.mark.parametrize("both", [False, True])
    def test_a_time_with_a_utc_offset_is_refused_not_compared(self, both):
        """An offset time compared with a local one raises; two offset times compare wrongly."""
        aware = time(9, 0, tzinfo=UTC)
        rules = ShowingRules(earliest_start=aware, latest_start=aware if both else time(18))
        window = PreferredWindow(days=["mon"], start=aware, end=aware if both else time(18))

        (rules_problem,) = rules_problems(rules)
        (window_problem,) = listing_problems(
            RentalListing(unit="12-A", preferred_showing_times=[window])
        )

        assert "no UTC offset" in rules_problem and "no UTC offset" in window_problem

    def test_an_unknown_field_is_an_error_not_a_dropped_value(self):
        """Dropped silently, ``rentedOn`` would leave a rented unit on offer."""
        with pytest.raises(ValidationError):
            RentalListing.model_validate({"unit": "12-A", "rentedOn": "2027-06-01"})
        with pytest.raises(ValidationError):
            ShowingRules.model_validate({"earliestStart": "09:00"})


class TestUnits:
    @pytest.mark.asyncio
    async def test_a_saved_unit_reads_back_as_it_was_saved(self, session):
        await service.save_listing(session, LISTING, changed_by="Pat")

        assert await service.read_listings(session) == [LISTING]
        assert await service.read_listing(session, " 12-a ") == LISTING
        assert await service.read_listing(session, "12-Z") is None

    @pytest.mark.asyncio
    async def test_saving_a_unit_again_replaces_it_whatever_the_case_of_its_name(self, session):
        await service.save_listing(session, LISTING, changed_by="Pat")
        rented = LISTING.model_copy(update={"unit": "12-a", "off_market_on": date(2027, 6, 1)})

        await service.save_listing(session, rented, changed_by="Pat")

        assert await service.read_listings(session) == [rented]

    @pytest.mark.asyncio
    async def test_the_database_refuses_two_listings_whose_units_differ_only_in_case(self, session):
        session.add(RentalListingRecord(unit="12-A"))
        session.add(RentalListingRecord(unit="12-a"))

        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    @pytest.mark.asyncio
    async def test_a_locked_read_of_an_unlisted_unit_holds_off_another_creator(self, session):
        """No row exists to lock, so the lock is on the name, whatever its case."""
        async with AsyncSessionFactory() as first, AsyncSessionFactory() as second:
            assert await service.read_listing(first, "12-A", lock=True) is None
            waiting = asyncio.create_task(service.read_listing(second, "12-a", lock=True))
            await asyncio.sleep(0.3)
            assert not waiting.done()

            await service.save_listing(first, RentalListing(unit="12-A"), changed_by="Pat")

            assert await asyncio.wait_for(waiting, 5) == RentalListing(unit="12-A")
            await second.rollback()

    @pytest.mark.asyncio
    async def test_units_come_back_in_name_order(self, session):
        for name in ("14-B", "12-A", "12-C"):
            await service.save_listing(session, RentalListing(unit=name), changed_by="Pat")

        assert [unit.unit for unit in await service.read_listings(session)] == [
            "12-A",
            "12-C",
            "14-B",
        ]

    @pytest.mark.asyncio
    async def test_a_unit_that_fails_its_checks_is_not_saved_or_recorded(self, session):
        bad = LISTING.model_copy(update={"public_facts": ["Door code is 4821"]})

        with pytest.raises(RentalDataInvalid, match="access code"):
            await service.save_listing(session, bad, changed_by="Pat")

        assert await service.read_listings(session) == []
        assert await service.read_history(session) == []

    @pytest.mark.asyncio
    async def test_a_row_changed_behind_the_service_s_back_is_refused_on_read(self, session):
        """Someone edits the table by hand: readers must not get the bad value."""
        await service.save_listing(session, LISTING, changed_by="Pat")
        await session.execute(
            update(RentalListingRecord).values(internal_notes=["Lockbox code 4821 on the rail"])
        )
        await session.commit()

        with pytest.raises(RentalDataInvalid, match="access code"):
            await service.read_listings(session)


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
    @pytest.mark.parametrize("saved_before", [False, True])
    async def test_a_locked_read_holds_off_another_until_the_first_commits(
        self, session, saved_before
    ):
        """Read, change, save: a second editor waits, so neither change is lost.

        Holds whether or not the rules have been saved before, when there is
        no row to lock.
        """
        if saved_before:
            await service.save_showing_rules(session, ShowingRules(), changed_by="Pat")
        async with AsyncSessionFactory() as first, AsyncSessionFactory() as second:
            await service.read_showing_rules(first, lock=True)
            waiting = asyncio.create_task(service.read_showing_rules(second, lock=True))
            await asyncio.sleep(0.3)
            assert not waiting.done()

            one = ShowingRules(blackout_dates=[date(2027, 7, 4)])
            await service.save_showing_rules(first, one, changed_by="Pat")

            assert await asyncio.wait_for(waiting, 5) == one
            await second.rollback()

    @pytest.mark.asyncio
    async def test_a_settings_row_of_the_wrong_shape_is_refused_on_read(self, session):
        session.add(AppSetting(key=service.SHOWING_RULES_KEY, value={"earliestStart": "09:00"}))
        await session.commit()

        with pytest.raises(RentalDataInvalid):
            await service.read_showing_rules(session)


class TestHistory:
    @pytest.mark.asyncio
    async def test_every_save_is_recorded_with_who_where_and_the_result(self, session):
        await service.save_listing(session, LISTING, changed_by="Pat", source="conversation-1")
        rented = LISTING.model_copy(update={"off_market_on": date(2027, 6, 1)})
        await service.save_listing(session, rented, changed_by="Sam", source="conversation-2")

        newest, oldest = await service.read_history(session, "12-a")

        assert (newest.changed_by, newest.source) == ("Sam", "conversation-2")
        assert newest.snapshot == rented.model_dump(mode="json")
        assert oldest.snapshot == LISTING.model_dump(mode="json")
        assert newest.changed_at >= oldest.changed_at
        # A snapshot is a whole unit, so any past state can be read back as one.
        assert RentalListing.model_validate(oldest.snapshot) == LISTING

    @pytest.mark.asyncio
    async def test_showing_rules_changes_are_in_the_same_history(self, session):
        await service.save_listing(session, LISTING, changed_by="Pat")
        rules = ShowingRules(blackout_dates=[date(2027, 7, 4)])
        await service.save_showing_rules(session, rules, changed_by="Pat")

        everything = await service.read_history(session)
        (rules_change,) = await service.read_history(session, service.SHOWING_RULES_SUBJECT)

        assert [change.subject for change in everything] == ["showing_rules", "12-A"]
        assert ShowingRules.model_validate(rules_change.snapshot) == rules
        assert len(await service.read_history(session, limit=1)) == 1

    @pytest.mark.asyncio
    async def test_history_outlives_the_unit(self, session):
        await service.save_listing(session, LISTING, changed_by="Pat")
        await session.execute(delete(RentalListingRecord))
        await session.commit()

        (change,) = await service.read_history(session, "12-A")
        listing_id = (await session.execute(select(RentalListingChange.listing_id))).scalar_one()

        assert change.snapshot["unit"] == "12-A" and listing_id is None
