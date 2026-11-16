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
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from api.src.rental_listings.schema import PreferredWindow
from api.src.zillow_leads.thread import LeadThread

# Current residents get a day's notice before a showing.
MIN_NOTICE = timedelta(hours=24)
# Showings start no earlier than 9:00 and, at 30 minutes long, no later than 18:00.
# These are the defaults; listing data can set another window.
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
    # use ``ListingData.booking_url``, and if that is empty too, offer times.
    booking_url: str = ""
    # Whatever else is true of the unit, one fact per line: bedrooms, floor,
    # utilities, laundry, parking, appliances, pets. The model answers unit
    # questions from this and nothing else.
    facts: str = ""
    # What the model should know to decide but must not tell the lead, one note
    # per line: a resident who wants two days' notice, a lease start that can
    # move under a condition. Low-stakes only; the policy says how to use them.
    internal_notes: str = ""
    # When we would like this unit shown, if there is a choice (the resident
    # asked for afternoons, say). Of the times that may be offered, the ones
    # inside these windows are listed in the case as preferred.
    preferred_times: list[PreferredWindow] = Field(default_factory=list)

    @field_validator("facts", "internal_notes", mode="before")
    @classmethod
    def _text_from_lines(cls, value: object) -> object:
        """Accept a list of lines, which is easier to edit by hand in JSON."""
        if isinstance(value, list) and all(isinstance(line, str) for line in value):
            return "\n".join(value)
        return value


class AgentAvailability(BaseModel):
    """The times one person can show units, as a weekly pattern."""

    agent: str
    weekday_times: list[time] = Field(default_factory=list)
    weekend_times: list[time] = Field(default_factory=list)


class ShowingWindow(BaseModel):
    """The outer limits on when a showing may start, every day."""

    earliest_start: time = EARLIEST_START
    latest_start: time = LATEST_START


class ListingData(BaseModel):
    """What changes week to week in season: listings, schedules, days off."""

    timezone: str = "America/New_York"
    listings: list[Listing] = Field(default_factory=list)
    rented_units: list[str] = Field(default_factory=list)
    # The self-serve booking page for showings, when one exists.
    booking_url: str = ""
    showing_window: ShowingWindow = Field(default_factory=ShowingWindow)
    # A weekly pattern of tour times kept by hand. The evals' season file has
    # one. Production has none: there, tour times come from a ``TourSource``
    # (see ``tours.py``).
    availability: list[AgentAvailability] = Field(default_factory=list)
    blackout_dates: list[date] = Field(default_factory=list)


class TourSlot(BaseModel):
    start: datetime  # local time, timezone-aware
    # Internal: who would host, from a weekly pattern. Never shown to the lead
    # and never saved with a case; empty from the booking service.
    agent: str = ""
    # The unit this time is open for. Empty means any unit on offer, as with a
    # weekly pattern; the booking service gives each unit its own times.
    unit: str | None = None


class BookedTour(BaseModel):
    unit: str
    start: datetime


# Why a lead's own showing was cancelled, keyed by the booking service's
# ``showing_attendees.cancel_reason``, in the words the case shows.
CANCELLED_BY = {
    "prospect": "cancelled by the lead",
    "team": "cancelled by the team",
    "off_market": "cancelled by the team: the unit is no longer on offer",
    "number_refused": "not booked: the mobile number given could not be texted",
}


class OwnShowing(BaseModel):
    """A showing this lead's thread is booked into, or was until it was cancelled.

    Only the lead's own, tied to the thread through its booking link. Nothing
    about the host or anyone else in the showing.
    """

    unit: str
    start: datetime
    status: Literal["booked", "cancelled"]
    # A key of ``CANCELLED_BY`` for a cancelled one; empty while booked.
    cancelled_by: str | None = None


class PreferredTime(BaseModel):
    """A time that may be offered and that falls in a unit's preferred windows."""

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
    preferred_times: list[PreferredTime] = Field(default_factory=list)
    # This lead's own upcoming showings, booked or cancelled. None while the
    # booking service is not in use (the switch ``lead_agent_uses_open_times``
    # is off): the rendered case then has no such section at all.
    own_showings: list[OwnShowing] | None = None


def within_showing_rules(start: datetime, listing_data: ListingData, now: datetime) -> bool:
    """Whether a showing may start at ``start``, as far as the fixed rules go.

    The rules with exact answers, whatever supplied the time: not in the past
    or inside the notice period, not outside the showing window, not on a
    blackout date. A time without a UTC offset cannot be placed and is refused.
    """
    if start.tzinfo is None:
        return False
    local = start.astimezone(ZoneInfo(listing_data.timezone))
    window = listing_data.showing_window
    return (
        start >= now + MIN_NOTICE
        and window.earliest_start <= local.time() <= window.latest_start
        and local.date() not in listing_data.blackout_dates
    )


def allowed_tour_times(case: "LeadCase", unit: str | None = None) -> set[datetime]:
    """The tour times a reply may offer: the times the case offers.

    The one calculation behind both the automatic-send check
    (``auto_reply.reply_problems``) and the evals' ``ReplyChecks``.

    Without the booking service, times have no unit and are open at every
    unit: any of them, or any booked showing, may be offered, exactly as
    before. With it (the case then has times by unit, or its own-showings
    section), each time belongs to one unit, so when ``unit`` (the reply's
    ``lead.unit``) is a unit the case lists, a time must be open, or a showing
    booked, at that unit. The lead's own booked showing may be repeated back to
    them, even when it has no room left for others.
    """
    by_unit = case.own_showings is not None or any(slot.unit for slot in case.offerable_slots)
    known = {listing.unit.casefold() for listing in case.listings}
    named = unit.strip().casefold() if unit and by_unit else ""
    if named not in known:
        named = ""
    return {
        slot.start
        for slot in case.offerable_slots
        if not named or slot.unit is None or slot.unit.casefold() == named
    } | {
        tour.start
        for tour in [
            *case.booked_tours,
            *(own for own in case.own_showings or [] if own.status == "booked"),
        ]
        if not named or tour.unit.casefold() == named
    }


def times_allowed(case: "LeadCase", offered: list[datetime], unit: str | None = None) -> bool:
    """Whether every offered time is one the case offers. A time without a UTC offset fails."""
    allowed = allowed_tour_times(case, unit)
    return all(start.tzinfo is not None and start in allowed for start in offered)


def offerable_slots(
    listing_data: ListingData, now: datetime, *, days_ahead: int = DEFAULT_DAYS_AHEAD
) -> list[TourSlot]:
    """Every tour time that may be offered at ``now``, earliest first.

    Applies the rules that have exact answers: nothing in the past or inside
    the notice period, nothing outside showing hours, nothing on a blackout
    date. Whether and how to offer them is the decision step's job.
    """
    zone = ZoneInfo(listing_data.timezone)
    local_now = now.astimezone(zone)
    earliest = local_now + MIN_NOTICE
    blackout = set(listing_data.blackout_dates)
    window = listing_data.showing_window

    slots: list[TourSlot] = []
    for offset in range(days_ahead + 2):  # +2: notice pushes the window out a day
        day = local_now.date() + timedelta(days=offset)
        if day in blackout:
            continue
        weekend = day.weekday() >= 5
        for person in listing_data.availability:
            for start_time in person.weekend_times if weekend else person.weekday_times:
                if not window.earliest_start <= start_time <= window.latest_start:
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
    listing_data: ListingData,
    *,
    upto: int | None = None,
    now: datetime | None = None,
    booked_tours: list[BookedTour] | None = None,
    slots: list[TourSlot] | None = None,
    booking_links: dict[str, str] | None = None,
    own_showings: list[OwnShowing] | None = None,
) -> LeadCase:
    """Assemble the case for a thread as it stood after message ``upto``.

    ``upto`` defaults to the whole thread (a live run). For a historical
    thread, pass a decision point: later messages are cut off and ``now``
    defaults to shortly after that message, so the case holds only what was
    knowable then.

    ``slots`` are the tour times that may be offered, when something other than
    the weekly pattern in ``listing_data`` knows them (a calendar, a booking
    service). The case stores the times either way, never where they came from.

    ``booking_links`` replace units' booking links (the booking service's page
    with this thread's own token), and ``own_showings`` are the thread's own
    showings. Both come from the booking service, only while it is in use;
    without them the case is exactly what it was before either existed.
    """
    last = len(thread.messages) - 1 if upto is None else upto
    visible = thread.model_copy(update={"messages": thread.messages[: last + 1]})
    if now is None:
        now = thread.messages[last].sent_at + DECISION_DELAY if upto is not None else _utc_now()
    zone = ZoneInfo(listing_data.timezone)
    today = now.astimezone(zone).date()
    rented = [item for item in listing_data.listings if item.rented_on and item.rented_on <= today]
    links = booking_links or {}
    listed = [
        item.model_copy(
            update={
                "booking_url": links.get(item.unit) or item.booking_url or listing_data.booking_url
            }
        )
        for item in listing_data.listings
        if item not in rented
    ]
    offerable = offerable_slots(listing_data, now) if slots is None else slots
    return LeadCase(
        case_id=f"{thread.thread_id}-{last}",
        now=now,
        timezone=listing_data.timezone,
        thread=visible,
        listings=listed,
        rented_units=listing_data.rented_units + [item.unit for item in rented],
        offerable_slots=offerable,
        booked_tours=[tour for tour in (booked_tours or []) if tour.start > now],
        preferred_times=_preferred_times(listed, offerable, zone),
        own_showings=(
            None
            if own_showings is None
            else sorted(
                (item for item in own_showings if item.start > now),
                key=lambda item: (item.start, item.unit),
            )
        ),
    )


def _preferred_times(
    listings: list[Listing], slots: list[TourSlot], zone: ZoneInfo
) -> list[PreferredTime]:
    """The times that may be offered that fall in each unit's preferred windows.

    A time open for one unit only (from the booking service) counts for that
    unit alone; a time with no unit counts for every unit, as before.
    """
    return [
        PreferredTime(unit=listing.unit, start=start)
        for listing in listings
        for start in sorted({slot.start for slot in slots if slot.unit in (None, listing.unit)})
        if any(window.covers(start.astimezone(zone)) for window in listing.preferred_times)
    ]


# What a saved case leaves out: the names of staff in a weekly pattern, and
# the lead's email addresses. Neither is in the rendered case, so a saved case
# holds nothing private that the conversation it is saved with does not show.
_NOT_SAVED: dict = {
    "offerable_slots": {"__all__": {"agent"}},
    "thread": {"messages": {"__all__": {"from_address", "reply_to"}}},
}


def case_record(case: LeadCase) -> dict:
    """The case as JSON, to save with its outcome and rebuild it later.

    ``case_from_record`` gives back a case that renders exactly as this one,
    so a past decision can be replayed on the input it had, and a production
    lead can become an eval scenario.
    """
    return case.model_dump(mode="json", exclude=_NOT_SAVED)


def case_from_record(record: dict) -> LeadCase:
    """A case saved by ``case_record``. The left-out addresses come back empty."""
    thread = record["thread"]
    messages = [{"from_address": "", "reply_to": None, **message} for message in thread["messages"]]
    return LeadCase.model_validate({**record, "thread": {**thread, "messages": messages}})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")


def _day(moment: datetime) -> str:
    return f"{moment.strftime('%A, %B')} {moment.day}"


def tour_time_words(start: datetime, timezone: str) -> str:
    """A tour time as the policy has a confirming reply state it: "Wednesday, April 14 at 4:00 PM".

    Day of the week, month, day, then the hour without a leading zero, in the
    case's time zone. The same words the case uses for a booked showing.
    """
    local = start.astimezone(ZoneInfo(timezone))
    return f"{_day(local)} at {_clock(local)}"


def _fact_lines(text: str) -> list[str]:
    """Free-text facts as clean lines, whatever bullets the author used."""
    return [line.strip().lstrip("-*• ").strip() for line in text.splitlines() if line.strip(" -*•")]


# How a unit's internal notes are introduced in the case. The policy says what
# the model may do with them; this label repeats the one rule that matters.
INTERNAL_NOTES_LABEL = "Internal notes (for your decisions only, never tell the lead):"
# The section with this lead's own showings, booked through their own link.
OWN_SHOWINGS_HEADING = "## This lead's own showings"


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
        if notes := _fact_lines(listing.internal_notes):
            lines.append(f"  - {INTERNAL_NOTES_LABEL}")
            lines += [f"    - {note}" for note in notes]
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
    # Times for no particular unit read as they always have. The booking
    # service's times each belong to one unit, so each line names its unit.
    by_unit = any(slot.unit for slot in case.offerable_slots)
    by_day: dict[tuple[str, date], list[str]] = {}
    for slot in case.offerable_slots:
        local = slot.start.astimezone(zone)
        times = by_day.setdefault(((slot.unit or "") if by_unit else "", local.date()), [])
        if _clock(local) not in times:  # two people free at once is one time to the lead
            times.append(_clock(local))
    keys = list(by_day)
    if by_unit:
        order = {listing.unit: place for place, listing in enumerate(case.listings)}
        keys.sort(key=lambda key: (order.get(key[0], len(order)), key[0], key[1]))
    for unit, day in keys:
        label = _day(datetime.combine(day, time(), tzinfo=zone))
        where = f"{unit or 'Any unit'}, " if by_unit else ""
        lines.append(f"- {where}{label}: {', '.join(by_day[(unit, day)])}")
    if not by_day:
        lines.append("- (none)")

    lines += ["", "## Tours already booked"]
    for tour in case.booked_tours:
        local = tour.start.astimezone(zone)
        lines.append(f"- {tour.unit}: {_day(local)} at {_clock(local)}")
    if not case.booked_tours:
        lines.append("- (none)")

    # Only while the booking service is in use (see ``LeadCase.own_showings``).
    if case.own_showings is not None:
        lines += ["", OWN_SHOWINGS_HEADING]
        for own in case.own_showings:
            local = own.start.astimezone(zone)
            state = (
                "booked"
                if own.status == "booked"
                else CANCELLED_BY.get(own.cancelled_by or "", "cancelled")
            )
            lines.append(f"- {own.unit}: {_day(local)} at {_clock(local)}, {state}")
        if not case.own_showings:
            lines.append("- (none)")

    # Only when there are some, so a case without preferences reads exactly as
    # it did before they existed and earlier eval runs stay comparable.
    if case.preferred_times:
        lines += ["", "## Preferred showing times"]
        preferred: dict[tuple[str, date], list[str]] = {}
        for item in case.preferred_times:
            local = item.start.astimezone(zone)
            preferred.setdefault((item.unit, local.date()), []).append(_clock(local))
        for (unit, day), times in preferred.items():
            label = _day(datetime.combine(day, time(), tzinfo=zone))
            lines.append(f"- {unit}: {label} at {', '.join(times)}")

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
