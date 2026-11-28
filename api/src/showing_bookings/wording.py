"""Every word a public booking shows or sends, as fixed templates with versions.

- The confirmations the booking form asks for (``confirmations_for``): the
  lease start, built from the listing's dates, only when it has any; the pet
  rule, the same on every unit; and consent to texts, with its small print.
  ``CONFIRMATIONS_VERSION`` and ``TEXTS_CONSENT_VERSION`` are saved on the
  attendee row with the wording, so a later change of wording or of a
  listing's dates does not change what a person agreed to.
- What a booking sends (``NOTICES_VERSION``): the prospect's confirmation
  text and the host's calendar event; the prospect's own invite, its move and
  its withdrawal (``INVITE_VERSION``).
- The prospect's text 1 hour before the showing (``HOUR_REMINDER_VERSION``).
- The confirmation text when a lead moved their showing by email and the new
  place keeps their details (``MOVED_VERSION``).
- What a cancellation sends (``CANCELLATION_VERSION``): the prospect's text
  confirming their own cancellation, the text when the team cancels (or the
  unit goes off the market), and the cancelled event and invite.
- What a unit's residents are texted (``RESIDENT_VERSION``): a new showing at
  their home, and its cancellation. Both are written again when they go out
  (``residents.text_for``), so a change here reaches texts already queued.
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
NOTICES_VERSION = "v2"
MOVED_VERSION = "v1"
CANCELLATION_VERSION = "v1"
RESIDENT_VERSION = "v3"
REMINDER_VERSION = "v2"
HOUR_REMINDER_VERSION = "v1"
INVITE_VERSION = "v2"
LEAD_EMAIL_VERSION = "v1"
HOST_TEXT_VERSION = "v2"
HOST_MORNING_VERSION = "v2"
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
    # The team's page for the Zillow inquiry they came from, when they came from one.
    inquiry_url: str | None = None
    # The lead's name as the lead agent recorded it from that inquiry, when it did.
    lead_name: str | None = None


_HOW_BOOKED = {
    "page": "Booked online",
    "team": "Booked by the team",
    "lead_agent": "Booked by email",
}


def _local(moment: datetime, timezone: str) -> datetime:
    return moment.astimezone(ZoneInfo(timezone))


def _clock(moment: datetime) -> str:
    return f"{moment:%I:%M %p}".lstrip("0")


def _day_and_time(moment: datetime, timezone: str) -> str:
    start = _local(moment, timezone)
    return f"{start:%a}, {start:%b} {start.day} at {_clock(start)}"


def when(visit: Visit) -> str:
    """ "Wed, Apr 14 at 4:00 PM", in the showing's time zone."""
    return _day_and_time(visit.starts_at, visit.timezone)


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


def moved_confirmation_text(visit: Visit, replaces: Sequence[datetime], cancel_url: str) -> str:
    """The confirmation text for a showing a lead moved by email, when the new place
    keeps their details: the new day, time and place, the showing it replaces
    (by its start; rarely more than one), and the cancel link (``MOVED_VERSION``)."""
    old = [_day_and_time(start, visit.timezone) for start in sorted(replaces)]
    earlier = f"showing on {old[0]}" if len(old) == 1 else f"showings on {' and '.join(old)}"
    return (
        f"Sernia Capital: your in-person showing has moved to {when(visit)} "
        f"at {place(visit)}. It replaces your {earlier}.\n"
        f"To cancel: {cancel_url}\nReply STOP to opt out."
    )


def host_event_summary(visit: Visit, guests: Sequence[Guest]) -> str:
    people = "1 person" if len(guests) == 1 else f"{len(guests)} people"
    return f"Showing: {visit.unit} ({people})"


def _same_person(name: str, other: str) -> bool:
    """Whether two names are one person's, by the first name: "Sam" and "Sam Rivera"
    are; "Sam Rivera" and "Alex Parker" are not."""
    first, second = name.split(), other.split()
    return bool(first and second) and first[0].casefold() == second[0].casefold()


def _how_booked(guest: Guest) -> str:
    """ "Booked online from Sam's Zillow inquiry", "Booked by the team, gets no texts"."""
    line = _HOW_BOOKED.get(guest.source, "Booked")
    if guest.inquiry_url:
        if not guest.lead_name:
            whose = "a"
        elif _same_person(guest.name, guest.lead_name):
            whose = "their"
        else:
            whose = f"{guest.lead_name}'s"
        line += f" from {whose} Zillow inquiry"
    if not guest.texts:
        line += ", gets no texts"
    return line


def host_event_description(guests: Sequence[Guest], showing_url: str | None = None) -> str:
    """Who is coming, how to reach them, how each booked (with a link to the Zillow
    inquiry they came from), what they confirmed, and a link to the showing on the
    team's site (``NOTICES_VERSION``)."""
    lines = ["Booked for this showing:"]
    for guest in guests:
        lines.append(f"- {guest.name}, {display_phone(guest.mobile)}, {guest.email or 'no email'}")
        lines.append(f"  {_how_booked(guest)}")
        if guest.inquiry_url:
            lines.append(f"  Inquiry: {guest.inquiry_url}")
        for confirmed in guest.confirmations:
            lines.append(f"  Confirmed: {confirmed}")
    if showing_url:
        lines += ["", f"Manage this showing: {showing_url}"]
    return "\n".join(lines)


def street(visit: Visit) -> str:
    """ "324 S Main St Apt 11": the address up to the first comma, or the unit when
    there is no address."""
    return visit.address.split(",")[0].strip() if visit.address else f"unit {visit.unit}"


def _questions(team_phone: str | None) -> list[str]:
    return [f"Questions? Text or call us at {display_phone(team_phone)}."] if team_phone else []


def invite_summary(visit: Visit, name: str) -> str:
    """The prospect's invite: "Showing: Pat Example - 324 S Main St Apt 11". The
    name is as they gave it; the event's location holds the full address
    (``INVITE_VERSION``)."""
    return f"Showing: {' '.join(name.split()) or 'Prospect'} - {street(visit)}"


def invite_description(
    visit: Visit,
    cancel_url: str,
    team_phone: str | None,
    replaces: Sequence[datetime] = (),
) -> str:
    """Who meets them, the cancel link, that declining cancels nothing, and the team
    number. With ``replaces`` it first says which showing this one replaces."""
    lines = []
    if replaces:
        old = [_day_and_time(start, visit.timezone) for start in sorted(replaces)]
        earlier = "showing on " + old[0] if len(old) == 1 else "showings on " + " and ".join(old)
        lines.append(f"Your showing has moved to this time. It replaces your {earlier}.")
    lines += [
        "A Sernia agent will meet you there.",
        f"To cancel, use this link: {cancel_url}",
        "Declining this invite does not cancel the showing. Only the link does.",
        *_questions(team_phone),
    ]
    return "\n".join(lines)


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


def invite_cancelled_summary(visit: Visit, name: str) -> str:
    return f"Cancelled: {invite_summary(visit, name)}"


def invite_cancelled_description(visit: Visit, team_phone: str | None) -> str:
    """The withdrawn invite. Google's own cancellation email carries the event; this
    is what the Messages list shows of it."""
    return "\n".join(
        [
            f"Your in-person showing on {when(visit)} at {street(visit)} is cancelled.",
            *_questions(team_phone),
        ]
    )


# ---------------------------------------------------------------------------
# What a unit's residents are texted (RESIDENT_VERSION)
# ---------------------------------------------------------------------------


def resident_new_showing_text(visit: Visit) -> str:
    """To the residents of a unit, once, when a new showing of it is booked."""
    return (
        "Automated showing alert: We've scheduled an in-person showing of your unit on "
        f"{when(visit)}. A Sernia agent will be there the whole time. You may be present or "
        "we can let ourselves in if you are not home."
    )


RESIDENT_ALERT = "Automated showing alert:"
RESIDENT_CLOSING = (
    "A Sernia agent will be there the whole time. You may be present or we can let "
    "ourselves in if you are not home."
)


@dataclass(frozen=True)
class Upcoming:
    """One of a unit's upcoming showings a residents' text lists; ``new`` when this
    text is the first to tell them of it."""

    visit: Visit
    new: bool = False


def resident_text(
    *,
    upcoming: Sequence[Upcoming],
    cancelled: Sequence[Visit] = (),
    today: Sequence[Visit] = (),
) -> str:
    """Everything a unit's residents are told at once (``RESIDENT_VERSION``).

    ``upcoming``: the unit's upcoming showings they have been told of, with the
    ones this text tells them of first (``new``). ``cancelled``: the showings
    this text says are cancelled. ``today``: the day's showings, for the
    reminder at 8 AM.

    - One new showing and nothing else to say: the announcement as it has
      always read (``resident_new_showing_text``).
    - New showings among others: "We've scheduled another in-person showing of
      your unit. Upcoming showings:" and the dated list, marking "(new)" only
      when the list mixes new and earlier ones.
    - Cancellations come first; with no new showing they end with "Still
      scheduled:" and the list, or with nothing when none remain.
    - The 8 AM reminder names the day's times.
    """
    upcoming = sorted(upcoming, key=lambda item: item.visit.starts_at)
    new = [item for item in upcoming if item.new]
    if len(new) == 1 and len(upcoming) == 1 and not cancelled and not today:
        return resident_new_showing_text(new[0].visit)
    sentences = [
        f"The in-person showing of your unit on {when(visit)} is cancelled."
        for visit in sorted(cancelled, key=lambda visit: visit.starts_at)
    ]
    if today:
        today = sorted(today, key=lambda visit: visit.starts_at)
        sentences.append(
            f"Reminder: an in-person showing of your unit is scheduled today at {_times(today)}."
            if len(today) == 1
            else f"Reminder: in-person showings of your unit are scheduled today at {_times(today)}."
        )
    if new:
        earlier = len(new) < len(upcoming)
        if earlier:
            sentences.append(
                "We've scheduled another in-person showing of your unit."
                if len(new) == 1
                else "We've scheduled more in-person showings of your unit."
            )
        else:
            sentences.append(
                "We've scheduled an in-person showing of your unit."
                if len(new) == 1
                else "We've scheduled in-person showings of your unit."
            )
        items = [
            f"- {when(item.visit)}{' (new)' if item.new and earlier else ''}" for item in upcoming
        ]
        return "\n".join(
            [f"{RESIDENT_ALERT} {' '.join(sentences)} Upcoming showings:", *items, RESIDENT_CLOSING]
        )
    if today:
        return f"{RESIDENT_ALERT} {' '.join(sentences)} {RESIDENT_CLOSING}"
    if upcoming:
        items = [f"- {when(item.visit)}" for item in upcoming]
        return "\n".join([f"{RESIDENT_ALERT} {' '.join(sentences)} Still scheduled:", *items])
    return f"{RESIDENT_ALERT} {' '.join(sentences)}"


# ---------------------------------------------------------------------------
# The team's actions (CANCELLATION_VERSION)
# ---------------------------------------------------------------------------


def host_event_moved_summary(visit: Visit) -> str:
    """The old host's event, cancelled when the team gives the showing to another host."""
    return f"Moved to another host: {visit.unit}"


def host_event_moved_description() -> str:
    return "The team gave this showing to another host. You are no longer hosting it."


# ---------------------------------------------------------------------------
# Reminders on the day (REMINDER_VERSION)
# ---------------------------------------------------------------------------


def prospect_reminder_text(visit: Visit, cancel_url: str) -> str:
    """To a prospect still booked, at 8 AM on the day: when, where, and the cancel link."""
    start = _clock(_local(visit.starts_at, visit.timezone))
    return (
        f"Sernia Capital: a reminder of your in-person showing today at {start} "
        f"at {place(visit)}.\nTo cancel: {cancel_url}\nReply STOP to opt out."
    )


def hour_reminder_text(visit: Visit, name: str) -> str:
    """To a prospect 1 hour before their showing, in Emilio's words: "Hi Pat. Reminder:
    You have an in-person showing at 324 S Main St Apt 11 in 1 hour at 1:30 PM"
    (``HOUR_REMINDER_VERSION``)."""
    first = name.split()[0] if name.split() else ""
    hello = f"Hi {first}." if first else "Hi."
    start = _clock(_local(visit.starts_at, visit.timezone))
    return (
        f"{hello} Reminder: You have an in-person showing at {street(visit)} in 1 hour at {start}"
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


# ---------------------------------------------------------------------------
# The host's texts (HOST_TEXT_VERSION) and morning text (HOST_MORNING_VERSION)
# ---------------------------------------------------------------------------
#
# A host is texted on every change to their showings: one sentence per change
# (``host_*_line``), then their upcoming showings in order (``host_text``).
# Changes due together (after hours, or at 8 AM) go as one text, and at 8 AM
# they open the morning text, which lists the day's showings instead
# (``host_morning_text``).


def host_joined_line(visit: Visit, name: str) -> str:
    """Someone booked the host's showing, a new one or a join."""
    return f"{name} booked your showing of {place(visit)} on {when(visit)}."


def host_left_line(visit: Visit, name: str, *, by_team: bool, booked: int) -> str:
    """Someone cancelled, or the team removed them; ``booked`` is how many are left."""
    showing = f"your showing of {place(visit)} on {when(visit)}"
    opening = (
        f"The team removed {name} from {showing}." if by_team else f"{name} cancelled {showing}."
    )
    if booked == 0:
        return f"{opening} No one is booked now, so the showing is cancelled."
    return opening


def host_moved_to_line(visit: Visit) -> str:
    """The team gave the host a showing."""
    return f"The showing of {place(visit)} on {when(visit)} is now yours to host."


def host_moved_from_line(visit: Visit) -> str:
    """The team gave the host's showing to someone else."""
    return (
        f"The showing of {place(visit)} on {when(visit)} has moved to another host. "
        "You are no longer hosting it."
    )


def host_showing_cancelled_line(visit: Visit, reason: str | None) -> str:
    """The team cancelled the showing, or its unit went off the market."""
    why = _REASONS.get(reason or "", "")
    return f"Your showing of {place(visit)} on {when(visit)} is cancelled. {why}".strip()


@dataclass(frozen=True)
class DayShowing:
    """One of the host's showings, as their texts list it."""

    visit: Visit
    booked: int


def _people(count: int) -> str:
    return "1 person" if count == 1 else f"{count} people"


# The longest text a notice holds (``schema.TextPayload.body``).
TEXT_LIMIT = 1600


def _more(count: int, *, today: bool) -> str:
    showings = f"showing{'' if count == 1 else 's'}"
    return f"…and {count} more {showings}{' today' if today else ''}."


def _fit(head: list[str], full: list[str], short: list[str], *, today: bool, limit: int) -> str:
    """``head`` then the list, never longer than ``limit``: the full lines, else the
    short ones, else as many short ones as fit and how many more there are."""
    for items in (full, short):
        text = "\n".join([*head, *items])
        if len(text) <= limit:
            return text
    kept: list[str] = []
    for index, line in enumerate(short):
        left = len(short) - index - 1
        candidate = "\n".join([*head, *kept, line] + ([_more(left, today=today)] if left else []))
        if len(candidate) > limit:
            break
        kept.append(line)
    return "\n".join([*head, *kept, _more(len(short) - len(kept), today=today)])[:limit]


def _changes(lines: Sequence[str]) -> list[str]:
    """What changed, as a text's opening: "Sernia Capital: " and one change per line."""
    return [f"Sernia Capital: {lines[0]}", *lines[1:]] if lines else []


def host_text(
    lines: Sequence[str], upcoming: Sequence[DayShowing], *, limit: int = TEXT_LIMIT
) -> str:
    """To a host when their showings change: what changed, then their upcoming
    showings in order with how many people are booked. The list names no one
    and no phone number."""
    upcoming = sorted(upcoming, key=lambda item: item.visit.starts_at)
    head = [*_changes(lines), ""]
    if not upcoming:
        return "\n".join([*head, "You have no upcoming showings."])
    head.append("Your upcoming showings:")
    full = [
        f"- {when(item.visit)} at {place(item.visit)}, {_people(item.booked)}" for item in upcoming
    ]
    short = [
        f"- {when(item.visit)}, unit {item.visit.unit}, {_people(item.booked)}" for item in upcoming
    ]
    return _fit(head, full, short, today=False, limit=limit)


def host_morning_text(
    showings: Sequence[DayShowing], *, lines: Sequence[str] = (), limit: int = TEXT_LIMIT
) -> str:
    """To a host at 8 AM: any changes due then, then the day's showings in order,
    each with its time, place and how many people are booked. The list names no
    one and no phone number.

    Never longer than ``limit``: when the full list is too long, the lines are
    compacted (the address once when every showing shares it, then time, unit
    and count per line); when that is still too long, it lists as many as fit
    and ends with how many more there are."""
    showings = sorted(showings, key=lambda item: item.visit.starts_at)
    first = _local(showings[0].visit.starts_at, showings[0].visit.timezone)
    day = f"{first:%a}, {first:%b} {first.day}"

    def clock(item: DayShowing) -> str:
        return _clock(_local(item.visit.starts_at, item.visit.timezone))

    opening = [*_changes(lines), ""] if lines else []
    title = "Your showings today" if lines else "Sernia Capital: your showings today"
    full = [f"- {clock(item)} at {place(item.visit)}, {_people(item.booked)}" for item in showings]
    text = "\n".join([*opening, f"{title}, {day}:", *full])
    if len(text) <= limit:
        return text
    addresses = {item.visit.address for item in showings}
    if len(addresses) == 1 and showings[0].visit.address:
        header = f"{title}, {day}, at {showings[0].visit.address}:"
    else:
        header = f"{title}, {day}:"
    short = [
        f"- {clock(item)}, unit {item.visit.unit}, {_people(item.booked)}" for item in showings
    ]
    return _fit([*opening, header], short, short, today=True, limit=limit)


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
    "prospect_reminder": "Prospect's reminder: 8 AM on the day",
    "prospect_hour_reminder": "Prospect's text: 1 hour before",
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
    "resident_reminder": "Residents' reminder: 8 AM on the day",
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


def notice_text(channel: str, payload: dict) -> str:
    """What a notice says: a text's body, an email's subject and body, or an event's details."""
    if channel == "sms":
        return payload.get("body", "")
    if channel == "email":
        return f"Subject: {payload.get('subject', '')}\n\n{payload.get('body', '')}"
    action = {"create": "New event", "update": "Changed event", "cancel": "Cancelled event"}
    lines = [f"{action.get(payload.get('operation'), 'Event')}: {payload.get('summary', '')}"]
    if payload.get("location"):
        lines.append(payload["location"])
    if payload.get("description"):
        lines.append(payload["description"])
    if payload.get("attendees"):
        lines.append("Guests: " + ", ".join(payload["attendees"]))
    return "\n".join(lines)


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
