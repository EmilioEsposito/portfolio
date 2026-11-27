"""How a live case gets its listings from the rental listings tables.

Uses the test database. All unit data here is invented.
"""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service
from api.src.rental_listings.models import RentalListingRecord
from api.src.rental_listings.schema import PreferredWindow, RentalListing, ShowingRules
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.zillow_leads import listing_data
from api.src.zillow_leads.case import (
    DESCRIPTION_LABEL,
    INTERNAL_NOTES_LABEL,
    Listing,
    ListingData,
    ShowingWindow,
    build_case,
    render_case,
)
from api.src.zillow_leads.thread import LeadMessage, LeadThread

AFTERNOONS = PreferredWindow(days=["mon", "tue"], start=time(15), end=time(18))
LISTING = RentalListing(
    unit="12-A",
    address="12 Example St #A",
    asking_rent=1500,
    next_lease_start_earliest=date(2027, 7, 1),
    next_lease_start_latest=date(2027, 8, 1),
    virtual_tours_allowed=True,
    off_market_on=date(2027, 6, 1),
    preferred_showing_times=[AFTERNOONS],
    listing_description="Sunny one bedroom near the park.\n\nHeat and hot water included.",
    public_facts=["1 bedroom, second floor", "Dishwasher"],
    internal_notes=["Resident wants 48 hours' notice where possible"],
)
RULES = ShowingRules(
    earliest_start=time(10),
    latest_start=time(17),
    blackout_dates=[date(2027, 7, 4)],
)


LEAD = LeadThread(
    mailbox="all@serniacapital.com",
    thread_id="T",
    messages=[
        LeadMessage(
            message_id="m0",
            rfc_message_id="<m0@x>",
            sent_at=datetime(2027, 5, 3, 13, tzinfo=UTC),
            from_address="lead@convo.zillow.com",
            reply_to=None,
            from_sernia=False,
            subject="12 Example St inquiry",
            body="Is it still available?",
        )
    ],
)


def rendered(listing: RentalListing) -> bytes:
    """The case a lead's first message gets, with this one listing on file."""
    data = listing_data.as_listing_data([listing], RULES)
    return render_case(build_case(LEAD, data, upto=0)).encode()


@pytest_asyncio.fixture
async def saved():
    await clear_rental_data()
    async with AsyncSessionFactory() as session:
        await service.save_listing(session, LISTING, changed_by="setup")
        await service.save_showing_rules(session, RULES, changed_by="setup")
    yield
    await clear_rental_data()


class TestShape:
    def test_every_field_reaches_the_case_builder(self):
        data = listing_data.as_listing_data([LISTING], RULES)

        assert data == ListingData(
            listings=[
                Listing(
                    unit="12-A",
                    address="12 Example St #A",
                    rent=1500,
                    lease_start_earliest=date(2027, 7, 1),
                    lease_start_latest=date(2027, 8, 1),
                    virtual_tours=True,
                    rented_on=date(2027, 6, 1),
                    facts="1 bedroom, second floor\nDishwasher",
                    description=(
                        "Sunny one bedroom near the park.\n\nHeat and hot water included."
                    ),
                    internal_notes="Resident wants 48 hours' notice where possible",
                    preferred_times=[AFTERNOONS],
                )
            ],
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
            "asking_rent",
            "next_lease_start_earliest",
            "next_lease_start_latest",
            "virtual_tours_allowed",
            "off_market_on",
            "preferred_showing_times",
            "listing_description",
            "public_facts",
            "internal_notes",
        }
        # For the team and the general agent's ad check; leads are not given them.
        not_given_to_the_lead_agent = {
            "zillow_listing_url_public_view",
            "zillow_listing_url_internal_editor_view",
        }
        # Applied by the booking service's open times, which the lead agent's tour
        # source passes on; the lead agent keeps its own 24-hour floor.
        applied_by_the_tour_source = {"showing_notice_hours"}
        assert (
            set(RentalListing.model_fields)
            == mapped | not_given_to_the_lead_agent | applied_by_the_tour_source
        )

    def test_the_zillow_ad_links_leave_the_case_unchanged(self):
        """The lead agent's case reads byte for byte the same with or without them."""
        with_links = LISTING.model_copy(
            update={
                "zillow_listing_url_public_view": "https://www.zillow.com/homedetails/x/1_zpid/",
                "zillow_listing_url_internal_editor_view": "https://www.zillow.com/rental-manager/x",
            }
        )
        assert rendered(with_links) == rendered(LISTING)
        assert "zillow.com" not in rendered(with_links).decode()

    def test_the_description_is_in_the_unit_entry_between_the_facts_and_the_notes(self):
        listing = LISTING.model_copy(update={"off_market_on": None})
        text = rendered(listing).decode()

        entry = text.split("## Listings\n")[1].split("\n\n")[0]
        assert entry.splitlines()[1:] == [
            "  - 1 bedroom, second floor",
            "  - Dishwasher",
            f"  - {DESCRIPTION_LABEL}",
            "    Sunny one bedroom near the park.",
            "    Heat and hot water included.",
            f"  - {INTERNAL_NOTES_LABEL}",
            "    - Resident wants 48 hours' notice where possible",
        ]

    def test_without_a_description_the_entry_has_no_label(self):
        listing = LISTING.model_copy(update={"off_market_on": None, "listing_description": ""})
        blank = listing.model_copy(update={"listing_description": "  \n\n  "})

        assert rendered(blank) == rendered(listing)
        assert DESCRIPTION_LABEL not in rendered(listing).decode()

    def test_the_defaults_match_what_the_evals_have_always_used(self):
        assert listing_data.as_listing_data([], ShowingRules()) == ListingData()


class TestLoad:
    @pytest.mark.usefixtures("saved")
    @pytest.mark.asyncio
    async def test_it_reads_what_is_saved(self):
        loaded = await listing_data.load()

        assert loaded.problem is None
        assert loaded.data == listing_data.as_listing_data([LISTING], RULES)

    @pytest.mark.asyncio
    async def test_with_nothing_saved_there_is_no_data_and_no_problem(self):
        await clear_rental_data()

        assert await listing_data.load() == listing_data.Loaded()

    @pytest.mark.usefixtures("saved")
    @pytest.mark.asyncio
    async def test_a_row_that_fails_its_checks_makes_all_of_it_unusable(self):
        async with AsyncSessionFactory() as session:
            await session.execute(update(RentalListingRecord).values(asking_rent=-1))
            await session.commit()

        loaded = await listing_data.load()

        assert loaded.data == ListingData()
        assert "rent must be more than 0" in loaded.problem

    @pytest.mark.asyncio
    async def test_a_database_that_cannot_be_read_is_reported_and_never_raises(self, monkeypatch):
        monkeypatch.setattr(service, "read_listings", AsyncMock(side_effect=RuntimeError("no db")))

        loaded = await listing_data.load()

        assert loaded.data == ListingData() and loaded.problem == "no db"
