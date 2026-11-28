"""What the team's chat tools read about showings, in one document.

The Sernia agent's ``showings_read`` (``sernia_ai/tools/showing_tools.py``)
and the Sernia MCP service's tool of the same name (through
``mcp_routes.py``) both answer with ``read``, so a person asking either one
gets the same open times, booking links and booked showings, worded the same.

It reads, and changes nothing but one thing: given a Zillow thread id, the
thread's own booking link token is saved the first time it is read
(``lead_tours.lead_booking_for``), as when the lead agent reads it.
"""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import bookings, effects, lead_tours, open_times, wording
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
    current_environment,
    public_booking_on,
    read_settings,
)
from api.src.zillow_leads.listing_data import as_listing_data

# How many open times the read lists for each unit: a few for every unit, more for one.
TIMES_FOR_EACH_UNIT = 8
TIMES_FOR_ONE_UNIT = 60

# The refusal when the rental listing data fails its checks, so nothing can be offered.
UNAVAILABLE = "unavailable"


def no_link_because(
    settings: ShowingBookingSettings, listing: RentalListing, today: date, has_place: bool
) -> str | None:
    """Why a unit's booking page does not answer, or None when it does."""
    if not open_times.on_offer(listing, today):
        return f"{listing.unit} is off the market from {listing.off_market_on}, so it has no booking page."
    if lead_tours._page_answers(settings, listing.unit, has_place):
        return None
    if not public_booking_on(settings, current_environment()):
        return (
            "Public booking is switched off on the Booking settings tab, so no booking "
            "page answers. Someone on the team can still book with showing_book, or switch "
            "public booking on."
        )
    return (
        f"Bookings are in live mode and {listing.unit} is not ticked live on the Booking "
        "settings tab, so its booking page is closed. Someone on the team can still book "
        "with showing_book."
    )


def _offer_view(offer: open_times.Offer, timezone: str) -> dict[str, Any]:
    view: dict[str, Any] = {
        "start": offer.start.isoformat(),
        "when": effects.when_text(offer.start, timezone),
        "kind": "joins a showing already booked" if offer.kind == "join" else "new showing",
        "places_left": offer.places_left,
        "host": offer.host_name,
    }
    if offer.preferred:
        view["preferred"] = True
    return view


async def _booked(
    now: datetime, timezone: str, unit: str | None, thread_id: str | None
) -> list[dict[str, Any]]:
    """The scheduled showings that have not started, in the next 14 days, with who is in them."""
    async with AsyncSessionFactory() as session:
        query = (
            select(Showing, ShowingAgentRecord.name)
            .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
            .where(
                Showing.status == "scheduled",
                Showing.starts_at > now,
                Showing.starts_at <= now + open_times.HORIZON,
            )
            .order_by(Showing.starts_at, Showing.unit)
        )
        if unit is not None:
            query = query.where(func.lower(Showing.unit) == unit.strip().casefold())
        showings = (await session.execute(query)).all()
        people: dict[int, list[ShowingAttendee]] = {}
        if showings:
            for person in await session.scalars(
                select(ShowingAttendee)
                .where(
                    ShowingAttendee.showing_id.in_([showing.id for showing, _ in showings]),
                    ShowingAttendee.status == "booked",
                )
                .order_by(ShowingAttendee.id)
            ):
                people.setdefault(person.showing_id, []).append(person)
    booked = []
    for showing, host in showings:
        rows = []
        for person in people.get(showing.id, []):
            row: dict[str, Any] = {
                "attendee_id": person.id,
                "name": person.name,
                "mobile": wording.display_phone(person.mobile),
                "email": person.email or "no email",
                "takes_texts": person.texts_accepted,
                "booked_by": {
                    "page": "the booking page",
                    "team": "the team",
                    "lead_agent": "the lead agent, by email",
                }.get(person.source, person.source),
            }
            if person.attention_reason:
                row["needs_a_call"] = person.attention_reason
            if thread_id is not None and person.zillow_thread_id == thread_id:
                row["this_thread"] = True
            rows.append(row)
        booked.append(
            {
                "showing_id": showing.id,
                "unit": showing.unit,
                "start": showing.starts_at.isoformat(),
                "when": effects.when_text(showing.starts_at, timezone),
                "host": host,
                "capacity": showing.capacity,
                "people": rows,
            }
        )
    return booked


async def _thread_view(
    thread_id: str,
    on_offer: list[RentalListing],
    rules: ShowingRules,
    settings: ShowingBookingSettings,
    now: datetime,
) -> dict[str, Any]:
    """The thread's own links and showings, as the lead agent's case shows them."""
    if not await effects.known_lead_thread(thread_id):
        return {
            "zillow_thread_id": thread_id,
            "problem": effects.UNKNOWN_THREAD.format(thread=thread_id),
        }
    # Asked as the lead agent asks with the booking service in use, whatever its
    # switch says: the link and the thread's showings do not depend on it.
    found = await lead_tours.lead_booking_for(
        lead_tours.BOOKING_TOUR_SOURCE, thread_id, as_listing_data(on_offer, rules), now
    )
    if found is None:
        return {
            "zillow_thread_id": thread_id,
            "problem": "The thread's link and showings could not be read just now. Try again.",
        }
    booked_at = {own.unit.casefold() for own in found.own_showings if own.status == "booked"}
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    links: dict[str, Any] = {}
    for listing in on_offer:
        link = found.booking_links.get(listing.unit)
        links[listing.unit] = link or {
            "no_link_because": no_link_because(
                settings, listing, today, listing.unit.casefold() in booked_at
            )
        }
    view: dict[str, Any] = {
        "zillow_thread_id": thread_id,
        "booking_links": links,
        "own_showings": [
            {
                "unit": own.unit,
                "when": effects.when_text(own.start, rules.timezone),
                "status": own.status,
                **({"cancelled_by": own.cancelled_by} if own.cancelled_by else {}),
            }
            for own in found.own_showings
        ],
    }
    if not settings.lead_agent_uses_open_times:
        view["note"] = (
            "The lead agent's switch to use the booking service is off, so the lead agent "
            "itself offers no times or links and does not see the thread's showings. The "
            "link still works and ties a booking to this thread."
        )
    return view


async def read(
    *, now: datetime, unit: str | None = None, zillow_thread_id: str | None = None
) -> dict[str, Any]:
    """Open times, booking links and booked showings, for one unit or every unit on offer.

    Raises ``Refused``: 503 ``unavailable`` when the rental listing data fails
    its checks, 404 ``unknown_unit`` for a unit with no listing.
    """
    try:
        found = await open_times.open_times(
            now=now, units=[unit.strip()] if unit and unit.strip() else None
        )
        async with AsyncSessionFactory() as session:
            settings = await read_settings(session)
            listings = await listings_service.read_listings(session)
            rules = await listings_service.read_showing_rules(session)
    except RentalDataInvalid as error:
        raise Refused(
            503,
            UNAVAILABLE,
            f"The rental listing data fails its checks: {error}. Nothing can be booked until "
            "it is fixed.",
        ) from None
    if unit and unit.strip() and not found.units:
        names = ", ".join(item.unit for item in listings) or "none"
        raise Refused(
            404,
            "unknown_unit",
            f"No listing for a unit named {unit!r} is on file (on file: {names}).",
        )
    by_unit = {listing.unit.casefold(): listing for listing in listings}
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    limit = TIMES_FOR_ONE_UNIT if unit else TIMES_FOR_EACH_UNIT
    units = []
    for item in found.units:
        listing = by_unit[item.unit.casefold()]
        why = no_link_because(settings, listing, today, has_place=False)
        view: dict[str, Any] = {
            "unit": item.unit,
            "address": item.address,
            "booking_link": None if why else bookings.unit_url(listing.unit),
        }
        if why:
            view["no_link_because"] = why
        view["open_times"] = [_offer_view(offer, found.timezone) for offer in item.offers[:limit]]
        if len(item.offers) > limit:
            view["more_open_times"] = len(item.offers) - limit
        if item.why_none:
            view["no_open_times_because"] = item.why_none
        units.append(view)
    document: dict[str, Any] = {
        "now": effects.when_text(now, found.timezone),
        "timezone": found.timezone,
        "sending": effects.sending_sentence(settings),
        "public_booking": public_booking_on(settings, current_environment()),
        "lead_agent_uses_booking_service": settings.lead_agent_uses_open_times,
        "units": units,
        "booked": await _booked(now, found.timezone, unit, zillow_thread_id),
        "agent_calendars": [
            {
                "name": agent.name,
                "read": agent.status,
                **({"problem": agent.problem} if agent.problem else {}),
            }
            for agent in found.agents
        ],
    }
    if found.sample_calendar_in_use:
        document["sample_calendar"] = (
            "Outside production, agents with no calendar connected use a made-up week."
        )
    if not found.agents:
        document["no_agents"] = (
            "No showing agent is set up, so nothing can be booked. A person adds one on the "
            "Booking settings tab."
        )
    if zillow_thread_id and zillow_thread_id.strip():
        on_offer = [listing for listing in listings if open_times.on_offer(listing, today)]
        document["thread"] = await _thread_view(
            zillow_thread_id.strip(), on_offer, rules, settings, now
        )
    return document
