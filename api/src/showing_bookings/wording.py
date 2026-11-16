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
    lines = ["Booked on the Sernia Capital booking page:"]
    for guest in guests:
        lines.append(f"- {guest.name}, {display_phone(guest.mobile)}, {guest.email or 'no email'}")
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
}


def host_event_cancelled_description(reason: str | None) -> str:
    return f"This showing is cancelled. {_REASONS.get(reason or '', '')}".strip()


def invite_cancelled_summary(visit: Visit) -> str:
    return f"Cancelled: {invite_summary(visit)}"


def invite_cancelled_description() -> str:
    return "Your in-person showing with Sernia Capital is cancelled."


# ---------------------------------------------------------------------------
# The team's actions (CANCELLATION_VERSION)
# ---------------------------------------------------------------------------


def host_event_moved_summary(visit: Visit) -> str:
    """The old host's event, cancelled when the team gives the showing to another host."""
    return f"Moved to another host: {visit.unit}"


def host_event_moved_description() -> str:
    return "The team gave this showing to another host. You are no longer hosting it."
