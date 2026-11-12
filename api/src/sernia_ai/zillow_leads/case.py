"""A lead case: everything the reply decision may depend on, gathered by code.

A case is plain data with no live lookups behind it, so the decision can be
replayed later on exactly the same input. The parts with exact answers — which
tour times may be offered right now, where a thread has a decision to make —
are computed here rather than left to the model.

Only what code or a grader reads is a typed field. Everything else the model
needs to know about a unit is free text (``Listing.facts``), so it can come
from a note, a listing page or a database without this module changing.

A case holds what differs from one lead or one week to the next. Anything that
is the same for every lead, such as how to apply, belongs in
``prompts/policy.md``.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from api.src.sernia_ai.zillow_leads.thread import LeadThread

# Current residents get a day's notice before a showing.
MIN_NOTICE = timedelta(hours=24)
# Showings start no earlier than 9:00 and, at 30 minutes long, no later than 18:00.
EARLIEST_START = time(9, 0)
LATEST_START = time(18, 0)
DEFAULT_DAYS_AHEAD = 7
# A lead's burst of messages is answered once, shortly after the last one.
DECISION_DELAY = timedelta(minutes=5)


class Listing(BaseModel):
    """A unit being shown: a few typed fields, and a fact sheet in plain text."""

    unit: str
    address: str = ""
    rent: int | None = None
    lease_start_earliest: date | None = None
    lease_start_latest: date | None = None
    virtual_tours: bool = False
    # From this date the unit is taken: it moves from the listings to the
    # "rented, do not offer" list. Dated so a past case sees the past state.
    rented_on: date | None = None
    # Where a lead can book a showing for this unit themselves. Empty means
    # use ``OperatingData.booking_url``, and if that is empty too, offer times.
    booking_url: str = ""
    # Whatever else is true of the unit, one fact per line: bedrooms, floor,
    # utilities, laundry, parking, appliances, pets. The model answers unit
    # questions from this and nothing else.
    facts: str = ""


class AgentAvailability(BaseModel):
    """The times one person can show units, as a weekly pattern."""

    agent: str
    weekday_times: list[time] = Field(default_factory=list)
    weekend_times: list[time] = Field(default_factory=list)


class OperatingData(BaseModel):
    """What changes week to week in season: listings, schedules, days off."""

    timezone: str = "America/New_York"
    listings: list[Listing] = Field(default_factory=list)
    rented_units: list[str] = Field(default_factory=list)
    # The self-serve booking page for showings, when one exists.
    booking_url: str = ""
    availability: list[AgentAvailability] = Field(default_factory=list)
    blackout_dates: list[date] = Field(default_factory=list)


class TourSlot(BaseModel):
    start: datetime  # local time, timezone-aware
    agent: str  # internal: who would host. Never shown to the lead.


class BookedTour(BaseModel):
    unit: str
    start: datetime


class LeadCase(BaseModel):
    """One decision point: the thread so far and the facts in force at that moment."""

    case_id: str
    now: datetime
    timezone: str
    thread: LeadThread
    listings: list[Listing]
    rented_units: list[str]
    offerable_slots: list[TourSlot]
    booked_tours: list[BookedTour]


def offerable_slots(
    operating: OperatingData, now: datetime, *, days_ahead: int = DEFAULT_DAYS_AHEAD
) -> list[TourSlot]:
    """Every tour time that may be offered at ``now``, earliest first.

    Applies the rules that have exact answers: nothing in the past or inside
    the notice period, nothing outside showing hours, nothing on a blackout
    date. Whether and how to offer them is the decision step's job.
    """
    zone = ZoneInfo(operating.timezone)
    local_now = now.astimezone(zone)
    earliest = local_now + MIN_NOTICE
    blackout = set(operating.blackout_dates)

    slots: list[TourSlot] = []
    for offset in range(days_ahead + 2):  # +2: notice pushes the window out a day
        day = local_now.date() + timedelta(days=offset)
        if day in blackout:
            continue
        weekend = day.weekday() >= 5
        for person in operating.availability:
            for start_time in person.weekend_times if weekend else person.weekday_times:
                if not EARLIEST_START <= start_time <= LATEST_START:
                    continue
                start = datetime.combine(day, start_time, tzinfo=zone)
                if earliest <= start <= local_now + timedelta(days=days_ahead + 1):
                    slots.append(TourSlot(start=start, agent=person.agent))
    return sorted(slots, key=lambda slot: (slot.start, slot.agent))


def decision_points(thread: LeadThread) -> list[int]:
    """Indexes of the messages after which a decision was owed.

    That is each lead message that is the last of its burst: followed by one
    of our replies, or by nothing. Consecutive lead messages are one decision.
    """
    messages = thread.messages
    return [
        index
        for index, message in enumerate(messages)
        if not message.from_sernia
        and (index == len(messages) - 1 or messages[index + 1].from_sernia)
    ]


def build_case(
    thread: LeadThread,
    operating: OperatingData,
    *,
    upto: int | None = None,
    now: datetime | None = None,
    booked_tours: list[BookedTour] | None = None,
    slots: list[TourSlot] | None = None,
) -> LeadCase:
    """Assemble the case for a thread as it stood after message ``upto``.

    ``upto`` defaults to the whole thread (a live run). For a historical
    thread, pass a decision point: later messages are cut off and ``now``
    defaults to shortly after that message, so the case holds only what was
    knowable then.

    ``slots`` are the tour times that may be offered, when something other than
    the weekly pattern in ``operating`` knows them (a calendar, a booking
    service). The case stores the times either way, never where they came from.
    """
    last = len(thread.messages) - 1 if upto is None else upto
    visible = thread.model_copy(update={"messages": thread.messages[: last + 1]})
    if now is None:
        now = thread.messages[last].sent_at + DECISION_DELAY if upto is not None else _utc_now()
    today = now.astimezone(ZoneInfo(operating.timezone)).date()
    rented = [item for item in operating.listings if item.rented_on and item.rented_on <= today]
    return LeadCase(
        case_id=f"{thread.thread_id}-{last}",
        now=now,
        timezone=operating.timezone,
        thread=visible,
        listings=[
            item.model_copy(update={"booking_url": item.booking_url or operating.booking_url})
            for item in operating.listings
            if item not in rented
        ],
        rented_units=operating.rented_units + [item.unit for item in rented],
        offerable_slots=offerable_slots(operating, now) if slots is None else slots,
        booked_tours=[tour for tour in (booked_tours or []) if tour.start > now],
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")


def _day(moment: datetime) -> str:
    return f"{moment.strftime('%A, %B')} {moment.day}"


def _fact_lines(text: str) -> list[str]:
    """Free-text facts as clean lines, whatever bullets the author used."""
    return [line.strip().lstrip("-*• ").strip() for line in text.splitlines() if line.strip(" -*•")]


def render_case(case: LeadCase) -> str:
    """The case as the decision step reads it. Staff names are left out."""
    zone = ZoneInfo(case.timezone)
    now = case.now.astimezone(zone)
    lines = [f"Current time: {_day(now)}, {now.year} at {_clock(now)} ({case.timezone})", ""]

    lines.append("## Listings")
    for listing in case.listings:
        facts = [listing.address or None, f"${listing.rent:,}/month" if listing.rent else None]
        first, final = listing.lease_start_earliest, listing.lease_start_latest
        if first and final and first != final:
            facts.append(
                f"lease can start {first:%B} {first.day} through {final:%B} {final.day}, {final.year}"
            )
        elif first or final:
            start = first or final
            facts.append(f"lease starts {start:%B} {start.day}, {start.year}")
        facts.append("virtual tours allowed" if listing.virtual_tours else "in-person tours only")
        lines.append(f"- {listing.unit}: " + "; ".join(fact for fact in facts if fact))
        lines += [f"  - {fact}" for fact in _fact_lines(listing.facts)]
    if not case.listings:
        lines.append("- (none on file)")
    if case.rented_units:
        lines.append(f"- Rented, do not offer: {', '.join(case.rented_units)}")

    lines += ["", "## Booking links"]
    lines += [
        f"- {listing.unit}: {listing.booking_url}"
        for listing in case.listings
        if listing.booking_url
    ] or ["- (none)"]

    lines += ["", "## Tour times that may be offered"]
    by_day: dict[date, list[str]] = {}
    for slot in case.offerable_slots:
        local = slot.start.astimezone(zone)
        times = by_day.setdefault(local.date(), [])
        if _clock(local) not in times:  # two people free at once is one time to the lead
            times.append(_clock(local))
    for day, times in by_day.items():
        label = _day(datetime.combine(day, time(), tzinfo=zone))
        lines.append(f"- {label}: {', '.join(times)}")
    if not by_day:
        lines.append("- (none)")

    lines += ["", "## Tours already booked"]
    for tour in case.booked_tours:
        local = tour.start.astimezone(zone)
        lines.append(f"- {tour.unit}: {_day(local)} at {_clock(local)}")
    if not case.booked_tours:
        lines.append("- (none)")

    lines += ["", "## Thread (oldest first)"]
    for number, message in enumerate(case.thread.messages, 1):
        sent = message.sent_at.astimezone(zone)
        who = "Sernia" if message.from_sernia else "Lead"
        lines += [
            "",
            f"### Message {number}: {who}, {_day(sent)} at {_clock(sent)}",
            f"Subject: {message.subject}",
            "",
            message.body or "(empty)",
        ]
    return "\n".join(lines)
