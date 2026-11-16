"""Every word a public booking shows or sends, as fixed templates with versions.

- The confirmations the booking form asks for (``confirmations_for``): the
  lease start, built from the listing's dates, only when it has any; the pet
  rule, the same on every unit; and consent to texts, with its small print.
  ``CONFIRMATIONS_VERSION`` and ``TEXTS_CONSENT_VERSION`` are saved on the
  attendee row with the wording, so a later change of wording or of a
  listing's dates does not change what a person agreed to.
- What a booking sends (``NOTICES_VERSION``): the prospect's confirmation
  text, the host's calendar event and the prospect's own invite.
- What a cancellation sends (``CANCELLATION_VERSION``): the prospect's text
  confirming their own cancellation, the text when the team cancels (or the
  unit goes off the market), and the cancelled event and invite.
- What a unit's residents are texted (``RESIDENT_VERSION``): a new showing at
  their home, and its cancellation.
- The reminders the evening before (``REMINDER_VERSION``): the prospect's,
  with the cancel link, and the residents' list of the next day's showings.
- The reminder by email in a lead's Zillow thread, for a showing agreed there
  by a lead who has not added their details (``LEAD_EMAIL_VERSION``).
- The host's texts when someone books or cancels, or the showing is
  cancelled, less than 2 hours ahead (``HOST_TEXT_VERSION``).
- The host's text on the morning of a day with showings (``HOST_MORNING_VERSION``).
- The prospect's text after a showing with the apply link (``APPLY_VERSION``).
- What each notice is called (``notice_label``) and the team's alert when a
  notice fails or is stuck (``ALERT_VERSION``), which names no phone number.

Change the wording here and raise the version beside it. Emilio reviews the
wording before launch.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from api.src.rental_listings.schema import RentalListing

CONFIRMATIONS_VERSION = "v1"
TEXTS_CONSENT_VERSION = "v1"
NOTICES_VERSION = "v1"
CANCELLATION_VERSION = "v1"
RESIDENT_VERSION = "v1"
REMINDER_VERSION = "v1"
LEAD_EMAIL_VERSION = "v1"
HOST_TEXT_VERSION = "v1"
HOST_MORNING_VERSION = "v1"
APPLY_VERSION = "v1"
ALERT_VERSION = "v1"

PETS_WORDING = "No pet dogs will live in the apartment."
TEXTS_WORDING = "Text me about this showing and my rental inquiry."
CONSENT_SMALL_PRINT = (
    "Sernia Capital will text you from our team number to confirm, remind and coordinate "
    "your showing. Message frequency varies. Message and data rates may apply. Reply STOP "
    "to opt out or HELP for help."
)
PRIVACY_PATH = "/privacy"
SMS_TERMS_PATH = "/sms-terms"


class Confirmation(BaseModel):
    """One box on the booking form: what it says, and the version of that wording."""

    key: str  # "lease_start", "pets" or "texts"
    wording: str
    version: str


def _day(value: date, *, year: bool) -> str:
    text = f"{value:%b} {value.day}"
    return f"{text}, {value.year}" if year else text


def lease_start_dates(earliest: date | None, latest: date | None) -> str | None:
    """ "between Jun 1 and Jul 1, 2027" for two dates, "on Jun 1, 2027" for one, None for
    none. The one rule for wording a listing's lease start, on the form's tick and in the
    list of units on offer."""
    dates = sorted({value for value in (earliest, latest) if value is not None})
    if not dates:
        return None
    if len(dates) == 1:
        return f"on {_day(dates[0], year=True)}"
    first, last = dates
    same_year = first.year == last.year
    return f"between {_day(first, year=not same_year)} and {_day(last, year=True)}"


def lease_start_wording(earliest: date | None, latest: date | None) -> str | None:
    """ "My lease can start between Jun 1 and Jul 1, 2027." for two dates, "on ..." for
    one, None for none."""
    dates = lease_start_dates(earliest, latest)
    return None if dates is None else f"My lease can start {dates}."


def confirmations_for(listing: RentalListing) -> list[Confirmation]:
    """The boxes the booking form shows for this listing, in order."""
    found = []
    lease = lease_start_wording(listing.next_lease_start_earliest, listing.next_lease_start_latest)
    if lease is not None:
        found.append(Confirmation(key="lease_start", wording=lease, version=CONFIRMATIONS_VERSION))
    found.append(Confirmation(key="pets", wording=PETS_WORDING, version=CONFIRMATIONS_VERSION))
    found.append(Confirmation(key="texts", wording=TEXTS_WORDING, version=TEXTS_CONSENT_VERSION))
    return found


# ---------------------------------------------------------------------------
# What a booking sends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Visit:
    """A showing as the templates need it."""

    unit: str
    address: str
    starts_at: datetime
    ends_at: datetime
    timezone: str


@dataclass(frozen=True)
class Guest:
    """One booked prospect, as the host's event lists them."""

    name: str
    mobile: str | None
    email: str | None
    confirmations: Sequence[str]
    # How they booked: "page", "team" or "lead_agent".
    source: str = "page"
    # Whether they take texts about the showing.
    texts: bool = True


_HOW_BOOKED = {
    "page": "booked on the booking page",
    "team": "booked by the team",
    "lead_agent": "booked by email with the lead agent",
}


def _local(moment: datetime, timezone: str) -> datetime:
    return moment.astimezone(ZoneInfo(timezone))


def _clock(moment: datetime) -> str:
    return f"{moment:%I:%M %p}".lstrip("0")


def when(visit: Visit) -> str:
    """ "Wed, Apr 14 at 4:00 PM", in the showing's time zone."""
    start = _local(visit.starts_at, visit.timezone)
    return f"{start:%a}, {start:%b} {start.day} at {_clock(start)}"


def place(visit: Visit) -> str:
    return f"{visit.address} (unit {visit.unit})" if visit.address else f"unit {visit.unit}"


def display_phone(e164: str | None) -> str:
    if not e164:
        return "no mobile"
    digits = e164.removeprefix("+1")
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def confirmation_text(visit: Visit, cancel_url: str) -> str:
    """The prospect's confirmation text: from whom, when, where, and the cancel link."""
    return (
        f"Sernia Capital: your in-person showing is booked for {when(visit)} "
        f"at {place(visit)}.\nTo cancel: {cancel_url}\nReply STOP to opt out."
    )


def host_event_summary(visit: Visit, guests: Sequence[Guest]) -> str:
    people = "1 person" if len(guests) == 1 else f"{len(guests)} people"
    return f"Showing: {visit.unit} ({people})"


def host_event_description(guests: Sequence[Guest]) -> str:
    """Who is coming and how each booked, true for every way a booking is made."""
    lines = ["Booked for this Sernia Capital showing:"]
    for guest in guests:
        how = _HOW_BOOKED.get(guest.source, "booked")
        if not guest.texts:
            how += ", no texts"
        lines.append(
            f"- {guest.name}, {display_phone(guest.mobile)}, {guest.email or 'no email'} ({how})"
        )
        for confirmed in guest.confirmations:
            lines.append(f"  Confirmed: {confirmed}")
    return "\n".join(lines)


def invite_summary(visit: Visit) -> str:
    return f"Showing of {place(visit)} with Sernia Capital"


def invite_description(cancel_url: str) -> str:
    return (
        "Your in-person showing with Sernia Capital.\n"
        f"To cancel, use this link: {cancel_url}\n"
        "Declining this invite does not cancel the showing; please use the link."
    )


# ---------------------------------------------------------------------------
# What a cancellation sends (CANCELLATION_VERSION)
# ---------------------------------------------------------------------------


def prospect_cancelled_text(visit: Visit, unit_url: str) -> str:
    """To a prospect who cancelled: which showing, and where to book another time."""
    return (
        f"Sernia Capital: your showing on {when(visit)} at {place(visit)} is cancelled. "
        f"To book another time: {unit_url}"
    )


def team_cancelled_text(visit: Visit, units_url: str) -> str:
    """To a prospect whose showing the team cancelled, or whose unit went off the market."""
    return (
        f"Sernia Capital: we're sorry, we had to cancel your showing on {when(visit)} "
        f"at {place(visit)}. Units open for showings: {units_url}"
    )


def host_event_cancelled_summary(visit: Visit) -> str:
    return f"Cancelled showing: {visit.unit}"


_REASONS = {
    "prospect": "Everyone booked cancelled.",
    "team": "The team cancelled it.",
    "off_market": "The unit went off the market.",
    "no_attendees": "No one is booked.",
    "reply_not_sent": "The email reply that booked it was not sent.",
}


def host_event_cancelled_description(reason: str | None) -> str:
    return f"This showing is cancelled. {_REASONS.get(reason or '', '')}".strip()


def invite_cancelled_summary(visit: Visit) -> str:
    return f"Cancelled: {invite_summary(visit)}"


def invite_cancelled_description() -> str:
    return "Your in-person showing with Sernia Capital is cancelled."


# ---------------------------------------------------------------------------
# What a unit's residents are texted (RESIDENT_VERSION)
# ---------------------------------------------------------------------------


def _minutes(visit: Visit) -> int:
    return round((visit.ends_at - visit.starts_at).total_seconds() / 60)


def resident_new_showing_text(visit: Visit) -> str:
    """To the residents of a unit, once, when a new showing of it is booked."""
    return (
        f"Hi, this is Sernia Capital. We've scheduled an in-person showing of your home at "
        f"{place(visit)} on {when(visit)}. It will take about {_minutes(visit)} minutes, and "
        "a Sernia agent will be there the whole time. Questions? Reply to this number."
    )


def resident_cancelled_text(visit: Visit) -> str:
    """To the residents who were told of a showing that is now cancelled."""
    return (
        f"Hi, this is Sernia Capital. The showing of your home at {place(visit)} on "
        f"{when(visit)} is cancelled, so no one will come by for it. "
        "Questions? Reply to this number."
    )


# ---------------------------------------------------------------------------
# The team's actions (CANCELLATION_VERSION)
# ---------------------------------------------------------------------------


def host_event_moved_summary(visit: Visit) -> str:
    """The old host's event, cancelled when the team gives the showing to another host."""
    return f"Moved to another host: {visit.unit}"


def host_event_moved_description() -> str:
    return "The team gave this showing to another host. You are no longer hosting it."


# ---------------------------------------------------------------------------
# Reminders the evening before (REMINDER_VERSION)
# ---------------------------------------------------------------------------


def prospect_reminder_text(visit: Visit, cancel_url: str) -> str:
    """To a prospect still booked, the evening before: when, where, and the cancel link."""
    return (
        f"Sernia Capital: a reminder of your in-person showing on {when(visit)} "
        f"at {place(visit)}.\nTo cancel: {cancel_url}\nReply STOP to opt out."
    )


def lead_email_reminder_text(visit: Visit, cancel_url: str, details_url: str) -> str:
    """To a lead whose showing was agreed in their Zillow email thread and who has
    not added their details, the evening before, as a reply in that thread:
    when, where, the cancel link, and their own link to add their details.
    Plain text (``LEAD_EMAIL_VERSION``)."""
    return (
        "Hello,\n\n"
        f"A reminder of your in-person showing on {when(visit)} at {place(visit)}.\n\n"
        f"If you can no longer make it, please cancel here: {cancel_url}\n\n"
        f"To get a text reminder and a calendar invite, add your details here: {details_url}\n\n"
        "Sernia Capital Team"
    )


def _times(visits: Sequence[Visit]) -> str:
    """ "4:00 PM", "4:00 PM and 5:30 PM", or "10:00 AM, 1:00 PM and 4:00 PM"."""
    clocks = [_clock(_local(visit.starts_at, visit.timezone)) for visit in visits]
    if len(clocks) == 1:
        return clocks[0]
    return f"{', '.join(clocks[:-1])} and {clocks[-1]}"


def resident_reminder_text(visits: Sequence[Visit]) -> str:
    """To a unit's residents, the evening before: the next day's showings there, in order."""
    visits = sorted(visits, key=lambda visit: visit.starts_at)
    first = visits[0]
    start = _local(first.starts_at, first.timezone)
    day = f"{start:%a}, {start:%b} {start.day}"
    if len(visits) == 1:
        booked = f"an in-person showing of your home at {place(first)} is booked for {day} at"
        length = f"It will take about {_minutes(first)} minutes"
    else:
        booked = f"in-person showings of your home at {place(first)} are booked for {day} at"
        length = f"Each will take about {_minutes(first)} minutes"
    return (
        f"Hi, this is Sernia Capital. A reminder: {booked} {_times(visits)}. {length}, and a "
        "Sernia agent will be there the whole time. Questions? Reply to this number."
    )


# ---------------------------------------------------------------------------
# The host's texts when something changes inside 2 hours (HOST_TEXT_VERSION)
# ---------------------------------------------------------------------------


def _booked(count: int) -> str:
    return "1 person is booked" if count == 1 else f"{count} people are booked"


def host_joined_text(visit: Visit, name: str, booked: int) -> str:
    """To the host, when someone books their showing less than 2 hours ahead."""
    return (
        f"Sernia Capital: {name} just booked your showing of {place(visit)} on {when(visit)}. "
        f"{_booked(booked)} now."
    )


def host_left_text(visit: Visit, name: str, *, by_team: bool, booked: int) -> str:
    """To the host, when someone cancels (or the team removes them) less than 2 hours ahead."""
    showing = f"your showing of {place(visit)} on {when(visit)}"
    if by_team:
        opening = f"Sernia Capital: the team removed {name} from {showing}."
    else:
        opening = f"Sernia Capital: {name} cancelled {showing}."
    if booked == 0:
        return f"{opening} No one is booked now, so the showing is cancelled."
    return f"{opening} {_booked(booked)} still."


def host_moved_to_text(visit: Visit, booked: int) -> str:
    """To the new host, when the team gives them a showing less than 2 hours ahead."""
    return (
        f"Sernia Capital: the showing of {place(visit)} on {when(visit)} is now yours to host. "
        f"{_booked(booked)}."
    )


def host_moved_from_text(visit: Visit) -> str:
    """To the old host, when the team gives their showing to someone else less than
    2 hours ahead."""
    return (
        f"Sernia Capital: the showing of {place(visit)} on {when(visit)} has moved to "
        "another host. You are no longer hosting it."
    )


def host_showing_cancelled_text(visit: Visit, reason: str | None) -> str:
    """To the host, when the team cancels the showing (or the unit goes off the market)
    less than 2 hours ahead."""
    return (
        f"Sernia Capital: your showing of {place(visit)} on {when(visit)} is cancelled. "
        f"{_REASONS.get(reason or '', '')}"
    ).strip()


# ---------------------------------------------------------------------------
# The host's morning text (HOST_MORNING_VERSION)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayShowing:
    """One of the host's showings on the day, as the morning text lists it."""

    visit: Visit
    booked: int


def _people(count: int) -> str:
    return "1 person" if count == 1 else f"{count} people"


# The longest text a notice holds (``schema.TextPayload.body``).
TEXT_LIMIT = 1600


def _more(count: int) -> str:
    return f"…and {count} more showing{'' if count == 1 else 's'} today."


def host_morning_text(showings: Sequence[DayShowing], *, limit: int = TEXT_LIMIT) -> str:
    """To a host at 8 AM: the day's showings in order, each with its time, place and
    how many people are booked. Names no one booked and no phone number.

    Never longer than ``limit``: when the full list is too long, the lines are
    compacted (the address once when every showing shares it, then time, unit
    and count per line); when that is still too long, it lists as many as fit
    and ends with how many more there are."""
    showings = sorted(showings, key=lambda item: item.visit.starts_at)
    first = _local(showings[0].visit.starts_at, showings[0].visit.timezone)
    day = f"{first:%a}, {first:%b} {first.day}"

    def clock(item: DayShowing) -> str:
        return _clock(_local(item.visit.starts_at, item.visit.timezone))

    full = [f"Sernia Capital: your showings today, {day}:"] + [
        f"- {clock(item)} at {place(item.visit)}, {_people(item.booked)}" for item in showings
    ]
    text = "\n".join(full)
    if len(text) <= limit:
        return text

    addresses = {item.visit.address for item in showings}
    if len(addresses) == 1 and showings[0].visit.address:
        header = f"Sernia Capital: your showings today, {day}, at {showings[0].visit.address}:"
    else:
        header = f"Sernia Capital: your showings today, {day}:"
    lines = [
        f"- {clock(item)}, unit {item.visit.unit}, {_people(item.booked)}" for item in showings
    ]
    text = "\n".join([header, *lines])
    if len(text) <= limit:
        return text

    kept: list[str] = []
    for index, line in enumerate(lines):
        left = len(lines) - index - 1
        candidate = "\n".join([header, *kept, line] + ([_more(left)] if left else []))
        if len(candidate) > limit:
            break
        kept.append(line)
    return "\n".join([header, *kept, _more(len(lines) - len(kept))])[:limit]


# ---------------------------------------------------------------------------
# The prospect's text after a showing (APPLY_VERSION)
# ---------------------------------------------------------------------------


def apply_text(visit: Visit, apply_url: str) -> str:
    """To a prospect two hours after their showing, unless marked a no-show."""
    return (
        f"Sernia Capital: thanks for coming to see {place(visit)}. If you'd like to rent "
        f"it, you can apply here: {apply_url}\nReply STOP to opt out."
    )


# ---------------------------------------------------------------------------
# What each notice is, in words, and the team's alert (ALERT_VERSION)
# ---------------------------------------------------------------------------

NOTICE_LABELS = {
    "prospect_confirmation": "Prospect's confirmation",
    "prospect_invite": "Prospect's invite",
    "prospect_reminder": "Prospect's reminder",
    "lead_email_reminder": "Lead's reminder by email, in their Zillow thread",
    "prospect_cancelled": "Prospect's cancellation",
    "prospect_cancelled_by_team": "Team's cancellation to the prospect",
    "prospect_invite_cancel": "Prospect's invite withdrawn",
    "host_event": "Host's event",
    "host_event_update": "Host's event update",
    "host_event_cancel": "Host's event cancelled",
    "host_late_join": "Host's text: someone booked",
    "host_late_cancel": "Host's text: someone cancelled",
    "host_showing_cancelled": "Host's text: showing cancelled",
    "resident_new_showing": "Residents' text: new showing",
    "resident_cancelled": "Residents' text: showing cancelled",
    "resident_reminder": "Residents' reminder",
    "host_event_moved": "Old host's event cancelled",
    "host_moved_to": "Host's text: showing now theirs",
    "host_moved_from": "Host's text: showing moved away",
    "host_morning": "Host's morning text: today's showings",
    "prospect_apply": "Prospect's text: apply link",
}


def notice_label(kind: str) -> str:
    """ "Prospect's reminder" for ``prospect_reminder``; an unknown kind in plain words."""
    if kind in NOTICE_LABELS:
        return NOTICE_LABELS[kind]
    words = kind.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def alert_title(reason: str) -> str:
    return "Showing message failed" if reason == "failed" else "Showing message stuck"


def alert_body(kind: str, visit: Visit, reason: str) -> str:
    """The team's alert: which notice, the unit and the showing's time. Never a number."""
    what = (
        "It failed and will not be tried again."
        if reason == "failed"
        else "It has waited more than 10 minutes to go out."
    )
    return (
        f"{notice_label(kind)} for unit {visit.unit}, showing {when(visit)}. {what} "
        "Check it under Messages on the Booking settings tab."
    )
