"""The units on offer, as the public list at ``/showings`` shows them.

One call to ``open_times.open_times`` covers every unit on offer, so the
agents' calendars are read once per connection for the whole list (and that
read is kept for 90 seconds, which the unit's booking page then reuses). No
host names and nothing about who is booked: each unit says only whether it
has an open time right now.
"""

import re
from datetime import date, datetime

import logfire
from pydantic import BaseModel

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.showing_bookings import open_times, turnstile, wording
from api.src.showing_bookings.bookings import Refused


class UnitOnOffer(BaseModel):
    unit: str
    address: str
    rent: int | None
    lease_start_earliest: date | None
    lease_start_latest: date | None
    # "between Jun 1 and Jul 1, 2027", "on Jun 1, 2027", or None: the same rule as
    # the booking form's lease start tick (``wording.lease_start_dates``).
    lease_start: str | None
    has_open_times: bool


class UnitsOnOffer(BaseModel):
    units: list[UnitOnOffer]
    turnstile_site_key: str | None


def _natural(text: str) -> tuple:
    """Numbers compared as numbers, so "90 Oak St" comes before "320 Oak St"."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", text.casefold())
        if part
    )


def list_order(item: UnitOnOffer) -> tuple:
    """By address, then unit; a unit with no address goes last."""
    return (item.address == "", _natural(item.address), _natural(item.unit))


async def units_on_offer(*, now: datetime) -> UnitsOnOffer:
    try:
        async with AsyncSessionFactory() as session:
            listings = await listings_service.read_listings(session)
            found = await open_times.open_times(now=now, session=session)
    except RentalDataInvalid:
        logfire.exception("showing bookings: rental data fails its checks")
        raise Refused(503, "unavailable", "Booking is not available right now.") from None
    by_unit = {listing.unit: listing for listing in listings}
    units = []
    for offers in found.units:
        listing = by_unit[offers.unit]
        earliest, latest = listing.next_lease_start_earliest, listing.next_lease_start_latest
        units.append(
            UnitOnOffer(
                unit=listing.unit,
                address=listing.address,
                rent=listing.asking_rent,
                lease_start_earliest=earliest,
                lease_start_latest=latest,
                lease_start=wording.lease_start_dates(earliest, latest),
                has_open_times=bool(offers.offers),
            )
        )
    units.sort(key=list_order)
    return UnitsOnOffer(units=units, turnstile_site_key=turnstile.site_key())
