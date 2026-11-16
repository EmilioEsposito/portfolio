"""Open times: when each unit can be shown right now, and who would host.

Two calls are what other code uses:

- ``offers_for_unit(unit, now=...)``: the unit's offers as of ``now``, in the
  order the booking page and the lead agent's case show them. Each offer is
  a start time, whether it joins a showing already booked or is a new time,
  whether it is preferred, the places left, and the host.
- ``recheck(unit, start, now=...)``: one unit and start checked again with a
  fresh free/busy read, for a booking about to be saved. It answers the host
  or a refusal that says why.

``open_times`` does the same for several units at once and says how each
agent's calendar was read; the Booking settings tab and the lead agent's tour
source (``lead_tours.py``) use it.

The rules, from the design's "Open times":

1. Start times every 30 minutes inside the showing window, in the showing
   rules' time zone, from the notice period out (the rules' notice, or the
   listing's own when it is longer) to 14 days out. Blackout dates are
   skipped, and so is every day from the listing's ``off_market_on``.
2. A start is open when at least one active agent is within their hours
   (no hours means the whole showing window), free on their calendars, and
   not in another showing, with 30 minutes' travel between different
   addresses and none at the same address. An agent whose calendars cannot
   be read counts as busy, never as free, and is logged as an error.
3. A showing already booked at the unit that has room is offered as a join,
   without reading the host's calendar again: that busy time is our showing.
   A full one is not offered, and no second showing starts beside it.
4. Order: joins, then times the unit or an available agent prefers, then the
   rest, each group by time.
5. The host of a new time is an agent already at the same address just before
   or after, then one who prefers that time, then the lower priority number,
   then whoever has fewer showings that week.

Calendars come through ``connections.calendar_for_connection``, one free/busy
read per connection covering its agents, kept for about 90 seconds per
connection and set of calendars. Outside production, an agent with no usable
connection is read from the made-up week in ``seed.py`` instead, and the
answer says so. That never happens in production.
"""

import re
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import PreferredWindow, RentalListing, ShowingRules
from api.src.showing_bookings import agents as agents_module
from api.src.showing_bookings import connections, seed
from api.src.showing_bookings.guard import PRODUCTION
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
)
from api.src.showing_bookings.providers import BusyBlock, CalendarSource, CalendarUnreadable
from api.src.showing_bookings.schema import (
    DAYS_AHEAD,
    DEFAULT_CAPACITY,
    FREE_BUSY_CACHE_SECONDS,
    SHOWING_MINUTES,
    START_EVERY_MINUTES,
    TRAVEL_MINUTES,
)
from api.src.showing_bookings.settings import current_environment

SLOT = timedelta(minutes=SHOWING_MINUTES)
START_EVERY = timedelta(minutes=START_EVERY_MINUTES)
TRAVEL = timedelta(minutes=TRAVEL_MINUTES)
HORIZON = timedelta(days=DAYS_AHEAD)

OfferKind = Literal["join", "new"]
CalendarStatus = Literal["read", "sample", "unreadable", "inactive"]


class Offer(BaseModel):
    """One time a unit can be booked for."""

    start: datetime
    # "join": a showing already booked that has room; "new": a new showing.
    kind: OfferKind
    # The unit, or for a new time an available agent, prefers it.
    preferred: bool
    places_left: int
    host_id: int
    host_name: str
    # The showing a join adds to.
    showing_id: int | None = None


class UnitOffers(BaseModel):
    unit: str
    address: str
    notice_hours: int
    offers: list[Offer]
    # When there are no offers, why, in a sentence for the team.
    why_none: str | None = None


class AgentCalendar(BaseModel):
    """How one agent's calendars were read for these open times."""

    agent_id: int
    name: str
    status: CalendarStatus
    # The connection that read them, by account; empty for a sample or none.
    connection_email: str | None = None
    problem: str | None = None


class OpenTimes(BaseModel):
    now: datetime
    timezone: str
    units: list[UnitOffers]
    agents: list[AgentCalendar]
    # Outside production, at least one agent's busy times are the made-up week.
    sample_calendar_in_use: bool = False


RefusalCode = Literal[
    "unknown_unit",
    "not_a_start_time",
    "outside_window",
    "blackout_date",
    "off_market",
    "inside_notice",
    "too_far_ahead",
    "full",
    "no_agent_free",
]


class HostCheck(BaseModel):
    """The answer of ``recheck``: the host and kind, or why the time cannot be booked."""

    ok: bool
    kind: OfferKind | None = None
    host_id: int | None = None
    host_name: str | None = None
    showing_id: int | None = None
    places_left: int | None = None
    refusal: RefusalCode | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# The rules, as pure functions of what is known
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentAvailability:
    """An active agent as the rules see them. ``busy`` None means unreadable: busy."""

    id: int
    name: str
    priority: int
    weekly_hours: Sequence[PreferredWindow]
    preferred_windows: Sequence[PreferredWindow]
    busy: Sequence[BusyBlock] | None


@dataclass(frozen=True)
class BookedShowing:
    """A scheduled showing, with how many people are booked into it."""

    id: int
    unit: str
    address: str
    starts_at: datetime
    ends_at: datetime
    agent_id: int
    host_name: str
    capacity: int
    booked: int


def notice_hours(listing: RentalListing, rules: ShowingRules) -> int:
    """The rules' notice, or the listing's own when it is longer."""
    return max(rules.showing_notice_hours, listing.showing_notice_hours or 0)


def start_problem(
    start: datetime, listing: RentalListing, rules: ShowingRules, now: datetime
) -> tuple[RefusalCode, str] | None:
    """Why ``start`` is not a start time this unit may offer at ``now``, or None."""
    if start.tzinfo is None:
        return "not_a_start_time", "The time has no time zone."
    zone = ZoneInfo(rules.timezone)
    local = start.astimezone(zone)
    wall = local.replace(tzinfo=None)
    if not rules.earliest_start <= wall.time() <= rules.latest_start:
        return "outside_window", "The time is outside the showing window."
    since_first = wall - datetime.combine(wall.date(), rules.earliest_start)
    if since_first % START_EVERY:
        return "not_a_start_time", "Showings start on the half hour."
    if local.date() in rules.blackout_dates:
        return "blackout_date", "That day is a blackout date."
    if listing.off_market_on is not None and local.date() >= listing.off_market_on:
        return "off_market", f"The unit is off the market from {listing.off_market_on}."
    if start < now + timedelta(hours=notice_hours(listing, rules)):
        return "inside_notice", "The time is inside the notice period."
    if start > now + HORIZON:
        return "too_far_ahead", f"Showings are offered up to {DAYS_AHEAD} days out."
    return None


def _grid(rules: ShowingRules, now: datetime) -> Iterator[datetime]:
    """Every start in the showing window from today to 14 days out, in local time.

    A wall time that does not exist on a daylight-saving change is skipped.
    """
    zone = ZoneInfo(rules.timezone)
    day = now.astimezone(zone).date()
    last_day = (now + HORIZON).astimezone(zone).date()
    while day <= last_day:
        moment = datetime.combine(day, rules.earliest_start)
        last = datetime.combine(day, rules.latest_start)
        while moment <= last:
            start = moment.replace(tzinfo=zone)
            if start.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == moment:
                yield start
            moment += START_EVERY
        day += timedelta(days=1)


def _place(text: str) -> str:
    return " ".join(re.findall(r"[0-9a-z]+", text.casefold()))


def same_address(unit: str, address: str, other_unit: str, other_address: str) -> bool:
    """Whether two showings are at one address, so no travel time is needed between them."""
    if unit.casefold() == other_unit.casefold():
        return True
    return bool(_place(address)) and _place(address) == _place(other_address)


def _in_hours(agent: AgentAvailability, local: datetime) -> bool:
    # No hours saved means the agent can show whenever the showing window allows.
    return not agent.weekly_hours or any(window.covers(local) for window in agent.weekly_hours)


def _prefers(agent: AgentAvailability, local: datetime) -> bool:
    return any(window.covers(local) for window in agent.preferred_windows)


def _calendar_free(agent: AgentAvailability, start: datetime, end: datetime) -> bool:
    if agent.busy is None:
        return False
    return not any(block.start < end and block.end > start for block in agent.busy)


def offers_for(
    listing: RentalListing,
    rules: ShowingRules,
    agents: Sequence[AgentAvailability],
    showings: Sequence[BookedShowing],
    now: datetime,
    *,
    only: datetime | None = None,
) -> tuple[list[Offer], str | None]:
    """The unit's offers in order, and when there are none, why.

    ``agents`` are the active agents. ``showings`` are the scheduled showings
    around the next 14 days, at every unit. ``only`` limits the answer to one
    start (``recheck``).
    """
    zone = ZoneInfo(rules.timezone)
    today = now.astimezone(zone).date()
    if listing.off_market_on is not None and listing.off_market_on <= today:
        return [], f"Off the market from {listing.off_market_on}."
    candidates = [only] if only is not None else list(_grid(rules, now))
    starts = [start for start in candidates if start_problem(start, listing, rules, now) is None]
    if not starts:
        return [], (
            "No start time is inside the showing window, past the notice period and within "
            f"{DAYS_AHEAD} days, outside blackout dates and the off-market date."
        )

    at_unit = [s for s in showings if s.unit.casefold() == listing.unit.casefold()]
    by_agent: dict[int, list[BookedShowing]] = defaultdict(list)
    for showing in showings:
        by_agent[showing.agent_id].append(showing)

    def clash(agent: AgentAvailability, start: datetime, end: datetime) -> bool:
        for other in by_agent[agent.id]:
            gap = (
                timedelta(0)
                if same_address(listing.unit, listing.address, other.unit, other.address)
                else TRAVEL
            )
            if other.starts_at < end + gap and other.ends_at + gap > start:
                return True
        return False

    def next_door(agent: AgentAvailability, start: datetime, end: datetime) -> bool:
        return any(
            (other.ends_at == start or other.starts_at == end)
            and same_address(listing.unit, listing.address, other.unit, other.address)
            for other in by_agent[agent.id]
        )

    def that_week(agent: AgentAvailability, local: datetime) -> int:
        week = local.isocalendar()[:2]
        return sum(
            other.starts_at.astimezone(zone).isocalendar()[:2] == week
            for other in by_agent[agent.id]
        )

    offers: list[Offer] = []
    any_in_hours = False
    for start in starts:
        end = start + SLOT
        local = start.astimezone(zone)
        unit_prefers = any(window.covers(local) for window in listing.preferred_showing_times)
        booked = next((s for s in at_unit if s.starts_at == start), None)
        if booked is not None:
            left = booked.capacity - booked.booked
            if left > 0:
                host = next((agent for agent in agents if agent.id == booked.agent_id), None)
                offers.append(
                    Offer(
                        start=start,
                        kind="join",
                        preferred=unit_prefers or (host is not None and _prefers(host, local)),
                        places_left=left,
                        host_id=booked.agent_id,
                        host_name=booked.host_name,
                        showing_id=booked.id,
                    )
                )
            continue
        # One showing at a unit at a time.
        if any(s.starts_at < end and s.ends_at > start for s in at_unit):
            continue
        in_hours = [agent for agent in agents if _in_hours(agent, local)]
        any_in_hours = any_in_hours or bool(in_hours)
        free = [
            agent
            for agent in in_hours
            if _calendar_free(agent, start, end) and not clash(agent, start, end)
        ]
        if not free:
            continue
        host = min(
            free,
            key=lambda agent: (
                not next_door(agent, start, end),
                not _prefers(agent, local),
                agent.priority,
                that_week(agent, local),
                agent.id,
            ),
        )
        offers.append(
            Offer(
                start=start,
                kind="new",
                preferred=unit_prefers or any(_prefers(agent, local) for agent in free),
                places_left=DEFAULT_CAPACITY,
                host_id=host.id,
                host_name=host.name,
            )
        )

    offers.sort(key=lambda offer: (_group(offer), offer.start))
    if offers:
        return offers, None
    if not agents:
        return [], "No active showing agents."
    if all(agent.busy is None for agent in agents):
        return [], "No agent's calendar can be read, so every agent counts as busy."
    if not any_in_hours:
        return [], "No agent's hours cover a start time in the showing window."
    return [], "Every agent is busy, in another showing or outside their hours at each start."


def _group(offer: Offer) -> int:
    if offer.kind == "join":
        return 0
    return 1 if offer.preferred else 2


# ---------------------------------------------------------------------------
# Reading calendars
# ---------------------------------------------------------------------------


@dataclass
class _Read:
    start: datetime
    end: datetime
    busy: dict[str, list[BusyBlock]]
    unreadable: dict[str, str]
    good_until: float = 0.0


# Free/busy answers by (connection id, calendars), in this process only.
_FREE_BUSY: dict[tuple[int, frozenset[str]], _Read] = {}


def forget_free_busy() -> None:
    """Drop every free/busy answer held in memory. For tests."""
    _FREE_BUSY.clear()


def _clock() -> float:
    return time.monotonic()


async def _read_calendars(
    source: CalendarSource,
    key: tuple[int, frozenset[str]] | None,
    calendar_ids: list[str],
    start: datetime,
    end: datetime,
    *,
    fresh: bool,
) -> _Read:
    """Busy times for ``calendar_ids``, from memory when a recent answer covers the window."""
    held = _FREE_BUSY.get(key) if key is not None and not fresh else None
    if held and held.good_until > _clock() and held.start <= start and held.end >= end:
        return held
    try:
        busy = await source.busy_blocks(calendar_ids, start, end)
        unreadable: dict[str, str] = {}
    except CalendarUnreadable as failure:
        busy, unreadable = dict(failure.busy), dict(failure.unreadable)
    except Exception:
        logfire.exception("showing bookings: free/busy read failed")
        busy = {}
        unreadable = {
            calendar_id: "The calendar could not be read." for calendar_id in calendar_ids
        }
    for calendar_id in calendar_ids:
        if calendar_id not in busy and calendar_id not in unreadable:
            unreadable[calendar_id] = "The calendar was left out of the answer."
    if unreadable:
        # Error level, so the alert on error records reaches the team: these
        # agents count as busy and their times disappear from the booking page.
        logfire.error(
            "showing bookings: calendars unreadable, their agents count as busy",
            connection_id=key[0] if key else None,
            unreadable=len(unreadable),
            calendars=len(calendar_ids),
        )
    read = _Read(start=start, end=end, busy=busy, unreadable=unreadable)
    # A fresh read covers one start only, so it is not kept for the next list.
    if key is not None and not fresh:
        read.good_until = _clock() + FREE_BUSY_CACHE_SECONDS
        _FREE_BUSY[key] = read
    return read


@dataclass
class _AgentRead:
    record: ShowingAgentRecord
    status: CalendarStatus
    busy: list[BusyBlock] | None = None
    connection_email: str | None = None
    problem: str | None = None


async def _agent_calendars(
    agents: Sequence[ShowingAgentRecord],
    rows: Sequence[CalendarConnection],
    start: datetime,
    end: datetime,
    *,
    fresh: bool = False,
) -> dict[int, _AgentRead]:
    """How each active agent's calendars read over ``start`` to ``end``."""
    production = current_environment() == PRODUCTION
    found: dict[int, _AgentRead] = {}
    groups: dict[int | None, list[_AgentRead]] = defaultdict(list)
    by_id = {row.id: row for row in rows}
    for record in agents:
        if not record.active:
            found[record.id] = _AgentRead(record, "inactive")
            continue
        if not record.busy_calendar_ids:
            found[record.id] = _AgentRead(
                record, "unreadable", problem="No calendars are listed for this agent."
            )
            continue
        connection = agents_module.connection_for(record, rows)
        usable = connection is not None and connections.unusable_reason(connection) is None
        if usable or (connection is not None and production):
            item = _AgentRead(record, "read", connection_email=connection.google_email)
            groups[connection.id].append(item)
        elif not production:
            item = _AgentRead(record, "sample")
            groups[None].append(item)
        else:
            item = _AgentRead(
                record, "unreadable", problem="No calendar connection is set up to read it."
            )
        found[record.id] = item

    for connection_id, items in groups.items():
        calendar_ids = list(
            dict.fromkeys(cid for item in items for cid in item.record.busy_calendar_ids)
        )
        if connection_id is None:
            source: CalendarSource = seed.sample_calendar(start, end)
            key = None
        else:
            source = connections.calendar_for_connection(by_id[connection_id])
            key = (connection_id, frozenset(calendar_ids))
        read = await _read_calendars(source, key, calendar_ids, start, end, fresh=fresh)
        for item in items:
            problems = [
                read.unreadable[c] for c in item.record.busy_calendar_ids if c in read.unreadable
            ]
            if problems:
                item.status = "unreadable"
                item.problem = problems[0]
                continue
            item.busy = [b for c in item.record.busy_calendar_ids for b in read.busy.get(c, [])]
    return found


# ---------------------------------------------------------------------------
# Loading what the rules need
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _session(session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    if session is not None:
        yield session
        return
    async with AsyncSessionFactory() as own:
        yield own


@dataclass
class _Known:
    rules: ShowingRules
    listings: list[RentalListing]
    agents: list[ShowingAgentRecord]
    connections: list[CalendarConnection]
    showings: list[BookedShowing] = field(default_factory=list)


async def _load(session: AsyncSession, now: datetime) -> _Known:
    """Listings, rules, agents, connections, and the scheduled showings around the next 14 days.

    Raises ``RentalDataInvalid`` when the listings or rules fail their checks.
    """
    rules = await listings_service.read_showing_rules(session)
    listings = await listings_service.read_listings(session)
    agent_rows = list(await session.scalars(select(ShowingAgentRecord)))
    connection_rows = list(await session.scalars(select(CalendarConnection)))
    booked = (
        select(ShowingAttendee.showing_id, func.count().label("booked"))
        .where(ShowingAttendee.status == "booked")
        .group_by(ShowingAttendee.showing_id)
        .subquery()
    )
    # A week either side, so "fewer showings that week" counts the whole week.
    rows = await session.execute(
        select(Showing, ShowingAgentRecord.name, func.coalesce(booked.c.booked, 0))
        .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
        .outerjoin(booked, booked.c.showing_id == Showing.id)
        .where(
            Showing.status == "scheduled",
            Showing.ends_at > now - timedelta(days=8),
            Showing.starts_at < now + HORIZON + timedelta(days=8),
        )
    )
    showings = [
        BookedShowing(
            id=showing.id,
            unit=showing.unit,
            address=showing.address,
            starts_at=showing.starts_at,
            ends_at=showing.ends_at,
            agent_id=showing.agent_id,
            host_name=name,
            capacity=showing.capacity,
            booked=count,
        )
        for showing, name, count in rows
    ]
    return _Known(rules, listings, agent_rows, connection_rows, showings)


def _availability(reads: dict[int, _AgentRead]) -> list[AgentAvailability]:
    return [
        AgentAvailability(
            id=item.record.id,
            name=item.record.name,
            priority=item.record.priority,
            weekly_hours=[PreferredWindow.model_validate(w) for w in item.record.weekly_hours],
            preferred_windows=[
                PreferredWindow.model_validate(w) for w in item.record.preferred_windows
            ],
            busy=item.busy,
        )
        for item in reads.values()
        if item.status != "inactive"
    ]


def on_offer(listing: RentalListing, today: date) -> bool:
    return listing.off_market_on is None or listing.off_market_on > today


# ---------------------------------------------------------------------------
# What other code calls
# ---------------------------------------------------------------------------


async def open_times(
    *,
    now: datetime,
    units: Sequence[str] | None = None,
    session: AsyncSession | None = None,
) -> OpenTimes:
    """Offers for ``units`` (by default every unit on offer), and how each agent was read.

    Units are matched without regard to case; one with no listing is left out.
    Raises ``RentalDataInvalid`` when the listings or rules fail their checks.
    """
    async with _session(session) as db:
        known = await _load(db, now)
    zone = ZoneInfo(known.rules.timezone)
    today = now.astimezone(zone).date()
    if units is None:
        chosen = [listing for listing in known.listings if on_offer(listing, today)]
    else:
        wanted = [unit.casefold() for unit in units]
        chosen = sorted(
            (listing for listing in known.listings if listing.unit.casefold() in wanted),
            key=lambda listing: wanted.index(listing.unit.casefold()),
        )
    reads = await _agent_calendars(
        known.agents, known.connections, now, now + HORIZON + timedelta(days=1)
    )
    available = _availability(reads)
    results = []
    for listing in chosen:
        offers, why = offers_for(listing, known.rules, available, known.showings, now)
        results.append(
            UnitOffers(
                unit=listing.unit,
                address=listing.address,
                notice_hours=notice_hours(listing, known.rules),
                offers=offers,
                why_none=why,
            )
        )
    return OpenTimes(
        now=now,
        timezone=known.rules.timezone,
        units=results,
        agents=[
            AgentCalendar(
                agent_id=item.record.id,
                name=item.record.name,
                status=item.status,
                connection_email=item.connection_email,
                problem=item.problem,
            )
            for item in sorted(
                reads.values(), key=lambda item: (item.record.priority, item.record.name)
            )
        ],
        sample_calendar_in_use=any(item.status == "sample" for item in reads.values()),
    )


async def offers_for_unit(
    unit: str, *, now: datetime, session: AsyncSession | None = None
) -> UnitOffers:
    """The unit's offers as of ``now``, in order. Raises ``LookupError`` for no such listing."""
    found = await open_times(now=now, units=[unit], session=session)
    if not found.units:
        raise LookupError(f"No rental listing for {unit!r}.")
    return found.units[0]


async def recheck(
    unit: str, start: datetime, *, now: datetime, session: AsyncSession | None = None
) -> HostCheck:
    """Check one start again before a booking is saved, reading calendars afresh.

    A join is answered from the showing itself. A new time reads the agents'
    calendars with no cached answer. Pass the booking's ``session`` to see
    the showings as that transaction does.
    """
    async with _session(session) as db:
        known = await _load(db, now)
    listing = next(
        (item for item in known.listings if item.unit.casefold() == unit.casefold()), None
    )
    if listing is None:
        return HostCheck(ok=False, refusal="unknown_unit", message=f"No rental listing for {unit}.")
    problem = start_problem(start, listing, known.rules, now)
    if problem is not None:
        return HostCheck(ok=False, refusal=problem[0], message=problem[1])
    booked = next(
        (
            s
            for s in known.showings
            if s.unit.casefold() == listing.unit.casefold() and s.starts_at == start
        ),
        None,
    )
    if booked is not None and booked.booked >= booked.capacity:
        return HostCheck(ok=False, refusal="full", message="That showing is full.")
    if booked is not None:
        return HostCheck(
            ok=True,
            kind="join",
            host_id=booked.agent_id,
            host_name=booked.host_name,
            showing_id=booked.id,
            places_left=booked.capacity - booked.booked,
        )
    reads = await _agent_calendars(known.agents, known.connections, start, start + SLOT, fresh=True)
    offers, why = offers_for(
        listing, known.rules, _availability(reads), known.showings, now, only=start
    )
    if not offers:
        return HostCheck(
            ok=False, refusal="no_agent_free", message=why or "No agent is free at that time."
        )
    offer = offers[0]
    return HostCheck(
        ok=True,
        kind=offer.kind,
        host_id=offer.host_id,
        host_name=offer.host_name,
        showing_id=offer.showing_id,
        places_left=offer.places_left,
    )
