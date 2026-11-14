"""Where a live case gets its tour times, and the showing rules kept in listing data.

No network. All unit data here is invented.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from api.src.zillow_leads.agent import load_policy
from api.src.zillow_leads.case import (
    INTERNAL_NOTES_LABEL,
    AgentAvailability,
    BookedTour,
    Listing,
    ListingData,
    PreferredWindow,
    ShowingWindow,
    TourSlot,
    build_case,
    offerable_slots,
    render_case,
    within_showing_rules,
)
from api.src.zillow_leads.thread import LeadMessage, LeadThread
from api.src.zillow_leads.tours import NoTours, Tours, tours_for_case

ET = ZoneInfo("America/New_York")
MONDAY_9AM = datetime(2027, 5, 3, 9, 0, tzinfo=ET)
TUESDAY = date(2027, 5, 4)
WEDNESDAY = date(2027, 5, 5)

LISTING_DATA = ListingData(
    listings=[Listing(unit="12-A", address="12 Example St #A", rent=1500)],
    blackout_dates=[WEDNESDAY],
)
THREAD = LeadThread(
    mailbox="all@serniacapital.com",
    thread_id="T",
    messages=[
        LeadMessage(
            message_id="m0",
            rfc_message_id="<m0@x>",
            sent_at=MONDAY_9AM.astimezone(UTC),
            from_address="lead@convo.zillow.com",
            reply_to=None,
            from_sernia=False,
            subject="12 Example St inquiry",
            body="Can I see it?",
        )
    ],
)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def slot(day: date, hour: int, minute: int = 0) -> TourSlot:
    return TourSlot(start=at(day, hour, minute), agent="Alex Agent")


class Source:
    """A tour source that returns what it was given, or fails."""

    def __init__(self, tours: Tours | None = None, error: Exception | None = None) -> None:
        self._tours, self._error = tours, error

    async def tours(self, listing_data: ListingData, now: datetime) -> Tours:
        if self._error:
            raise self._error
        return self._tours or Tours()


class TestShowingRules:
    def test_a_time_must_be_a_day_ahead_inside_the_window_and_off_blackout_dates(self):
        assert within_showing_rules(at(TUESDAY, 10), LISTING_DATA, MONDAY_9AM)
        assert within_showing_rules(at(TUESDAY, 9), LISTING_DATA, MONDAY_9AM)  # exactly 24 hours
        assert not within_showing_rules(at(TUESDAY, 8, 59), LISTING_DATA, MONDAY_9AM)  # notice
        assert not within_showing_rules(at(TUESDAY, 18, 30), LISTING_DATA, MONDAY_9AM)  # too late
        assert not within_showing_rules(at(WEDNESDAY, 10), LISTING_DATA, MONDAY_9AM)  # blackout
        assert not within_showing_rules(datetime(2027, 5, 4, 10, 0), LISTING_DATA, MONDAY_9AM)

    def test_the_window_is_read_in_the_business_time_zone(self):
        ten_eastern_in_utc = at(TUESDAY, 10).astimezone(UTC)
        assert within_showing_rules(ten_eastern_in_utc, LISTING_DATA, MONDAY_9AM)

    def test_the_window_comes_from_listing_data(self):
        narrow = LISTING_DATA.model_copy(
            update={"showing_window": ShowingWindow(earliest_start=time(12), latest_start=time(15))}
        )
        assert not within_showing_rules(at(TUESDAY, 10), narrow, MONDAY_9AM)
        assert within_showing_rules(at(TUESDAY, 15), narrow, MONDAY_9AM)

    def test_the_weekly_pattern_uses_the_same_window(self):
        pattern = [AgentAvailability(agent="Alex Agent", weekday_times=[time(8), time(13)])]
        default = ListingData(availability=pattern)
        early = default.model_copy(
            update={"showing_window": ShowingWindow(earliest_start=time(8), latest_start=time(12))}
        )

        assert {slot.start.time() for slot in offerable_slots(default, MONDAY_9AM)} == {time(13)}
        assert {slot.start.time() for slot in offerable_slots(early, MONDAY_9AM)} == {time(8)}

    def test_the_default_window_is_nine_to_six(self):
        assert ListingData().showing_window == ShowingWindow(
            earliest_start=time(9, 0), latest_start=time(18, 0)
        )


class TestTourSource:
    @pytest.mark.asyncio
    async def test_until_the_booking_system_exists_nothing_is_open_or_booked(self):
        tours = await tours_for_case(NoTours(), LISTING_DATA, MONDAY_9AM)

        assert tours == Tours(open_times=[], booked=[])

    @pytest.mark.asyncio
    async def test_a_source_s_times_are_held_to_the_fixed_rules(self):
        source = Source(
            Tours(
                open_times=[
                    slot(TUESDAY, 15),
                    slot(TUESDAY, 10),
                    slot(MONDAY_9AM.date(), 17),  # inside the notice period
                    slot(TUESDAY, 20),  # outside the showing window
                    slot(WEDNESDAY, 10),  # a blackout date
                ],
                booked=[BookedTour(unit="12-A", start=at(TUESDAY, 15))],
            )
        )

        tours = await tours_for_case(source, LISTING_DATA, MONDAY_9AM)

        assert [item.start for item in tours.open_times] == [at(TUESDAY, 10), at(TUESDAY, 15)]
        assert tours.booked == [BookedTour(unit="12-A", start=at(TUESDAY, 15))]

    @pytest.mark.asyncio
    async def test_a_source_that_fails_gives_no_times(self):
        """A calendar that cannot be read is unknown, never free."""
        tours = await tours_for_case(
            Source(error=RuntimeError("calendar not found")), LISTING_DATA, MONDAY_9AM
        )

        assert tours == Tours()

    @pytest.mark.asyncio
    async def test_a_live_case_lists_what_the_source_knows(self):
        source = Source(
            Tours(
                open_times=[slot(TUESDAY, 10)],
                booked=[BookedTour(unit="12-A", start=at(TUESDAY, 15))],
            )
        )
        tours = await tours_for_case(source, LISTING_DATA, MONDAY_9AM)

        case = build_case(
            THREAD, LISTING_DATA, now=MONDAY_9AM, slots=tours.open_times, booked_tours=tours.booked
        )
        text = render_case(case)

        assert "- Tuesday, May 4: 10:00 AM" in text
        assert "- 12-A: Tuesday, May 4 at 3:00 PM" in text
        assert "Alex" not in text

    @pytest.mark.asyncio
    async def test_with_no_source_a_live_case_lists_no_times_even_if_a_pattern_is_on_file(self):
        """Production ignores hand-kept tour times; only the evals read the pattern."""
        listing_data = LISTING_DATA.model_copy(
            update={
                "availability": [AgentAvailability(agent="Alex Agent", weekday_times=[time(10)])]
            }
        )
        tours = await tours_for_case(NoTours(), listing_data, MONDAY_9AM)

        case = build_case(
            THREAD, listing_data, now=MONDAY_9AM, slots=tours.open_times, booked_tours=tours.booked
        )

        assert case.offerable_slots == [] and case.booked_tours == []
        assert "## Tour times that may be offered\n- (none)" in render_case(case)
        # The evals' path, which passes no slots, still reads the pattern.
        assert build_case(THREAD, listing_data, now=MONDAY_9AM).offerable_slots


class TestPreferredTimes:
    afternoons = PreferredWindow(
        days=["mon", "tue", "wed", "thu", "fri"], start=time(15), end=time(18)
    )

    def listing_data(self, **fields) -> ListingData:
        listing = Listing(unit="12-A", preferred_times=[self.afternoons], **fields)
        return ListingData(listings=[listing, Listing(unit="12-B")])

    def test_open_times_inside_a_unit_s_windows_are_listed_as_preferred(self):
        slots = [
            slot(TUESDAY, 10),
            slot(TUESDAY, 15),
            slot(TUESDAY, 18),
            slot(date(2027, 5, 8), 15),  # a Saturday
        ]

        case = build_case(THREAD, self.listing_data(), now=MONDAY_9AM, slots=slots)

        assert [(item.unit, item.start) for item in case.preferred_times] == [
            ("12-A", at(TUESDAY, 15)),
            ("12-A", at(TUESDAY, 18)),
        ]
        text = render_case(case)
        assert "## Preferred showing times\n- 12-A: Tuesday, May 4 at 3:00 PM, 6:00 PM" in text
        # A preferred time is still one that may be offered.
        assert {item.start for item in case.preferred_times} <= {s.start for s in slots}

    def test_two_people_free_at_once_is_one_preferred_time(self):
        slots = [slot(TUESDAY, 15), TourSlot(start=at(TUESDAY, 15), agent="Blair Agent")]

        case = build_case(THREAD, self.listing_data(), now=MONDAY_9AM, slots=slots)

        assert len(case.preferred_times) == 1

    def test_with_no_open_times_nothing_is_preferred_and_the_case_does_not_mention_it(self):
        case = build_case(THREAD, self.listing_data(), now=MONDAY_9AM, slots=[])

        assert case.preferred_times == []
        assert "Preferred" not in render_case(case)

    def test_a_rented_unit_has_no_preferred_times(self):
        listing_data = self.listing_data(rented_on=date(2027, 5, 1))

        case = build_case(THREAD, listing_data, now=MONDAY_9AM, slots=[slot(TUESDAY, 15)])

        assert case.preferred_times == []

    def test_a_case_without_preferences_reads_as_it_did_before_they_existed(self):
        listing_data = ListingData(listings=[Listing(unit="12-A")])

        text = render_case(
            build_case(THREAD, listing_data, now=MONDAY_9AM, slots=[slot(TUESDAY, 15)])
        )

        assert "## Tours already booked\n- (none)\n\n## Thread (oldest first)" in text


class TestInternalNotes:
    def case_text(self, **fields) -> str:
        listing_data = ListingData(listings=[Listing(unit="12-A", facts="Dishwasher", **fields)])
        return render_case(build_case(THREAD, listing_data, now=MONDAY_9AM, slots=[]))

    def test_notes_are_in_the_case_under_a_label_that_says_not_to_share_them(self):
        text = self.case_text(
            internal_notes="Resident wants 48 hours' notice where possible\n- Lease start can move"
        )

        assert (
            "  - Dishwasher\n"
            f"  - {INTERNAL_NOTES_LABEL}\n"
            "    - Resident wants 48 hours' notice where possible\n"
            "    - Lease start can move\n"
        ) in text
        assert "never tell the lead" in INTERNAL_NOTES_LABEL

    def test_a_unit_without_notes_reads_as_it_did_before_they_existed(self):
        assert "Internal notes" not in self.case_text()
        assert "Internal notes" not in self.case_text(internal_notes=" \n")

    def test_the_policy_says_how_to_use_them(self):
        policy = load_policy()

        assert "internal notes" in policy
        assert "Never state, quote or hint at them" in policy


class TestFactSheet:
    def test_a_fact_sheet_can_be_given_as_lines(self):
        listing = Listing.model_validate({"unit": "12-A", "facts": ["Dishwasher", "Cats allowed"]})

        assert listing.facts == "Dishwasher\nCats allowed"
        assert listing == Listing(unit="12-A", facts="Dishwasher\nCats allowed")

    def test_the_notice_period_is_a_day(self):
        assert within_showing_rules(MONDAY_9AM + timedelta(hours=24), LISTING_DATA, MONDAY_9AM)
        assert not within_showing_rules(MONDAY_9AM + timedelta(hours=23), LISTING_DATA, MONDAY_9AM)
