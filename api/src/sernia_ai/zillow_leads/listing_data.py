"""Where a live case gets its listings and showing rules: the rental listings tables.

``api/src/rental_listings`` holds each unit's listing as it stands now, and
the showing rules. This module reads them once per lead and turns them into
the ``ListingData`` the case builder takes (``case.py``):

- a listing's public facts become the unit's fact sheet, which the agent may
  repeat;
- its internal notes go into the case under their own label, for the agent
  to decide with and never repeat (see ``prompts/policy.md``);
- a listing with an off-market date stops being offered from that date.

The table's field names say which lease they mean (``asking_rent``,
``next_lease_start_earliest``, ``off_market_on``). Inside a case, under
"Listings", the shorter names the case builder and the evals use (``rent``,
``lease_start_earliest``, ``rented_on``) cannot be misread, so they are kept.

Tour times and booked tours are not here. They come from a ``TourSource``
(``tours.py``).

Data that cannot be read, or that fails the checks in ``rental_listings``, is
not used at all: ``load`` returns nothing with the reason, and ``handle.py``
hands the lead to a person. With no listing saved yet there is no problem to
report: the agent runs with no listings and escalates on its own.

The evals do not come through here. They read a season's listing data file.
"""

from dataclasses import dataclass, field

import logfire

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.sernia_ai.zillow_leads.case import Listing, ListingData, ShowingWindow


@dataclass(frozen=True)
class Loaded:
    """Listing data as read, or the reason there is none."""

    data: ListingData = field(default_factory=ListingData)
    problem: str | None = None


def as_listing_data(listings: list[RentalListing], rules: ShowingRules) -> ListingData:
    """Listings and rules in the shape the case builder takes."""
    return ListingData(
        timezone=rules.timezone,
        listings=[
            Listing(
                unit=unit.unit,
                address=unit.address,
                rent=unit.asking_rent,
                lease_start_earliest=unit.next_lease_start_earliest,
                lease_start_latest=unit.next_lease_start_latest,
                virtual_tours=unit.virtual_tours_allowed,
                rented_on=unit.off_market_on,
                booking_url=unit.showing_booking_url,
                facts="\n".join(unit.public_facts),
                internal_notes="\n".join(unit.internal_notes),
                preferred_times=unit.preferred_showing_times,
            )
            for unit in listings
        ],
        booking_url=rules.showing_booking_url,
        showing_window=ShowingWindow(
            earliest_start=rules.earliest_start, latest_start=rules.latest_start
        ),
        blackout_dates=rules.blackout_dates,
    )


async def load() -> Loaded:
    """Listing data as it stands now, for one lead's case. Never raises."""
    try:
        async with AsyncSessionFactory() as session:
            listings = await service.read_listings(session)
            rules = await service.read_showing_rules(session)
    except Exception as error:  # the database is down, or a row fails its checks
        logfire.exception(
            "zillow lead: listing data is not usable, every lead goes to a person: {problem}",
            problem=str(error),
        )
        return Loaded(problem=str(error) or type(error).__name__)
    if not listings:
        logfire.warn("zillow lead: no rental listings saved, the agent has no listings")
    return Loaded(data=as_listing_data(listings, rules))
