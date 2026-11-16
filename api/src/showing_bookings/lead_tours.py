"""What the Zillow lead agent's case knows from the booking service. Behind a switch.

``zillow_leads.handle`` asks ``tour_source_for_lead_agent`` which source to
use for each lead. Only when the setting ``lead_agent_uses_open_times`` is on
is it ``BookingTourSource``; otherwise, and whenever the setting cannot be
read, it is the lead agent's own source (``NoTours``), and the case is
exactly what it was before any of this existed.

``BookingTourSource`` answers the ``zillow_leads.tours.TourSource`` interface:

- ``open_times``: each unit's own open times (as a new showing or by joining
  one), each a ``TourSlot`` with its unit. The case lists them by unit. No
  host's name: ``TourSlot.agent`` stays empty.
- ``booked``: the showings already booked that have room, by unit.

Only times that pass the lead agent's own fixed rules (``within_showing_rules``:
its 24-hour floor, the window, blackout dates) are passed on, joins included.
``tours_for_case`` filters open times by the same rules again but not booked
tours, so a join two hours away, which the booking page may offer under a
shorter notice, must be left out here. The booking page's offers are unchanged.

With the booking source in use, ``lead_booking_for`` adds what is particular
to the lead's thread:

- ``booking_links``: each unit's booking page with the thread's own token
  (``zillow_leads.lead_links``), ``<public base URL>/showings/<unit>?t=<token>``.
  A booking made through it is tied to the thread (``bookings.book``).
- ``own_showings``: the thread's own upcoming showings, booked or cancelled,
  from the attendee rows tied to it. Unit, start, and whether it is still
  booked or who cancelled it; nothing about the host or anyone else booked.
"""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import logfire
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import bookings
from api.src.showing_bookings.models import Showing, ShowingAttendee
from api.src.showing_bookings.open_times import open_times
from api.src.showing_bookings.settings import read_settings
from api.src.zillow_leads import lead_links
from api.src.zillow_leads.case import (
    BookedTour,
    ListingData,
    OwnShowing,
    TourSlot,
    within_showing_rules,
)
from api.src.zillow_leads.tours import Tours, TourSource


class BookingTourSource:
    """Each unit's open times and joinable showings from the booking service."""

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

        slots: list[TourSlot] = []
        booked: list[BookedTour] = []
        for unit in found.units:
            for offer in unit.offers:
                if not within_showing_rules(offer.start, listing_data, now):
                    continue
                slots.append(TourSlot(start=offer.start, unit=unit.unit))
                if offer.kind == "join":
                    booked.append(BookedTour(unit=unit.unit, start=offer.start))
        return Tours(
            open_times=sorted(slots, key=lambda slot: (slot.start, slot.unit or "")),
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


@dataclass(frozen=True)
class LeadBooking:
    """What the booking service knows about one lead thread."""

    booking_links: dict[str, str]
    own_showings: list[OwnShowing]


def lead_link(unit: str, token: str) -> str:
    """A unit's booking page, with a lead thread's token."""
    base = bookings.public_base_url()
    return f"{base}/showings/{quote(unit, safe='')}?{urlencode({'t': token})}"


async def lead_booking_for(
    source: TourSource, thread_id: str, listing_data: ListingData, now: datetime
) -> LeadBooking | None:
    """The thread's links and own showings, only while the booking source is in use.

    None with any other source, so the case keeps its listings' own links and
    has no own-showings section. None too, logged, when the token or the
    showings cannot be read: the case then reads as it would without them.
    """
    if source is not BOOKING_TOUR_SOURCE:
        return None
    try:
        token = await lead_links.token_for_thread(thread_id, now=now)
        async with AsyncSessionFactory() as session:
            rows = (
                await session.execute(
                    select(ShowingAttendee, Showing)
                    .join(Showing, Showing.id == ShowingAttendee.showing_id)
                    .where(
                        ShowingAttendee.zillow_thread_id == thread_id,
                        Showing.starts_at > now,
                        # A place booked for a reply that was never sent: the
                        # lead never heard of it (``lead_bookings.py``).
                        ShowingAttendee.cancel_reason.is_distinct_from("reply_not_sent"),
                    )
                    .order_by(Showing.starts_at, ShowingAttendee.id)
                )
            ).all()
    except Exception:
        logfire.exception("zillow lead: lead's booking links and showings unreadable")
        return None
    return LeadBooking(
        booking_links={
            listing.unit: lead_link(listing.unit, token) for listing in listing_data.listings
        },
        own_showings=[_own(attendee, showing) for attendee, showing in rows],
    )


def _own(attendee: ShowingAttendee, showing: Showing) -> OwnShowing:
    if attendee.status == "booked" and showing.status == "scheduled":
        return OwnShowing(unit=showing.unit, start=showing.starts_at, status="booked")
    return OwnShowing(
        unit=showing.unit,
        start=showing.starts_at,
        status="cancelled",
        cancelled_by=attendee.cancel_reason or showing.cancel_reason,
    )
