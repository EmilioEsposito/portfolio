"""The Zillow lead agent's tour source, backed by the open times here. Behind a switch.

``zillow_leads.handle`` asks ``tour_source_for_lead_agent`` which source to
use for each lead. Only when the setting ``lead_agent_uses_open_times`` is on
is it ``BookingTourSource``; otherwise, and whenever the setting cannot be
read, it is the lead agent's own source (``NoTours``), exactly as before.

``BookingTourSource`` answers the ``zillow_leads.tours.TourSource`` interface:

- ``open_times``: the start times every unit on offer can take, each with the
  agent who would host. The case lists tour times for no particular unit, so
  a time is listed only when each unit on offer can be shown then (as a new
  time or by joining a showing); a time we cannot vouch for for the unit a
  lead asks about is never offered.
- ``booked``: the showings already booked that have room, by unit.

``tours_for_case`` keeps filtering whatever this returns with the lead agent's
own fixed rules (its 24-hour floor, the window, blackout dates).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import logfire

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings.open_times import open_times
from api.src.showing_bookings.settings import read_settings
from api.src.zillow_leads.case import BookedTour, ListingData, TourSlot
from api.src.zillow_leads.tours import Tours, TourSource


class BookingTourSource:
    """Open times and joinable showings from the booking service."""

    async def tours(self, listing_data: ListingData, now: datetime) -> Tours:
        today = now.astimezone(ZoneInfo(listing_data.timezone)).date()
        units = [
            listing.unit
            for listing in listing_data.listings
            if listing.rented_on is None or listing.rented_on > today
        ]
        if not units:
            return Tours()
        found = await open_times(now=now, units=units)
        if not found.units:
            return Tours()

        booked: list[BookedTour] = []
        hosts: dict[datetime, str] = {}
        common: set[datetime] | None = None
        for unit in found.units:
            starts = set()
            for offer in unit.offers:
                starts.add(offer.start)
                if offer.kind == "join":
                    booked.append(BookedTour(unit=unit.unit, start=offer.start))
                elif offer.start not in hosts:
                    hosts[offer.start] = offer.host_name
            common = starts if common is None else common & starts
        for unit in found.units:
            for offer in unit.offers:
                hosts.setdefault(offer.start, offer.host_name)
        open_starts = sorted(common or set())
        return Tours(
            open_times=[TourSlot(start=start, agent=hosts[start]) for start in open_starts],
            booked=sorted(booked, key=lambda tour: (tour.start, tour.unit)),
        )


BOOKING_TOUR_SOURCE = BookingTourSource()


async def tour_source_for_lead_agent(fallback: TourSource) -> TourSource:
    """``BookingTourSource`` when the switch is on; ``fallback`` when it is off or unreadable."""
    try:
        async with AsyncSessionFactory() as session:
            settings = await read_settings(session)
    except Exception:
        logfire.warn("zillow lead: booking settings unreadable, no open times offered")
        return fallback
    return BOOKING_TOUR_SOURCE if settings.lead_agent_uses_open_times else fallback
