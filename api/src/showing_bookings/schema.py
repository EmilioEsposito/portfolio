"""The shapes the showing booking code reads and writes, wherever they are stored.

The database rows (``models.py``) take their allowed values from here, and the
team API answers in these shapes, so a status or a channel is spelled one way
everywhere.
"""

import re
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from api.src.rental_listings.schema import PreferredWindow

ConnectionStatus = Literal["connected", "lost", "revoked"]
ShowingStatus = Literal["scheduled", "cancelled", "completed"]
AttendeeStatus = Literal["booked", "cancelled"]
Attendance = Literal["unknown", "attended", "no_show"]
# "lead_agent": a time the Zillow lead agent agreed in an email thread, which can
# be booked before the lead has given a mobile number or an email address.
AttendeeSource = Literal["page", "team", "lead_agent"]

# A text, the host's own event on the showings calendar, the event a prospect
# was invited to before 2026-11-28 (one per prospect, so guests never saw each
# other; only withdrawing one already sent uses it now), or an email.
Channel = Literal["sms", "calendar_event", "calendar_invite", "email"]
# pending: waiting to be delivered. held: waiting for the notice it waits for
# (``waits_for_notice_id``, a prospect's confirmation text) to be reported
# delivered; ``holds.release_held`` makes it pending or cancels it. Then
# exactly one of: captured (rendered and kept, no provider called; a person may
# still send it by hand, ``captured.py``), sent, failed (the provider refused or
# never got it), unknown (it may have gone out; never retried), skipped (rows
# from when the send mode could be off), cancelled (never sent: the booking was
# withdrawn or its confirmation text was not delivered).
NoticeStatus = Literal[
    "pending", "held", "captured", "sent", "failed", "unknown", "skipped", "cancelled"
]
SendMode = Literal["test", "capture", "live"]
# The mode saved on a delivered row. "off" is only on rows from when it was one.
NoticeMode = Literal["off", "test", "capture", "live"]
# Why a notice needs the team: it failed (or may have gone out) and will not be
# tried again, or it has not gone out ten minutes after it was due.
NoticeAttention = Literal["failed", "stuck"]

CONNECTION_STATUSES: tuple[str, ...] = get_args(ConnectionStatus)
SHOWING_STATUSES: tuple[str, ...] = get_args(ShowingStatus)
ATTENDEE_STATUSES: tuple[str, ...] = get_args(AttendeeStatus)
ATTENDANCES: tuple[str, ...] = get_args(Attendance)
ATTENDEE_SOURCES: tuple[str, ...] = get_args(AttendeeSource)
CHANNELS: tuple[str, ...] = get_args(Channel)
NOTICE_STATUSES: tuple[str, ...] = get_args(NoticeStatus)
NOTICE_MODES: tuple[str, ...] = get_args(NoticeMode)
NOTICE_ATTENTIONS: tuple[str, ...] = get_args(NoticeAttention)

# Showings last 30 minutes and take up to 4 parties unless a showing says otherwise.
SHOWING_MINUTES = 30
DEFAULT_CAPACITY = 4
# Open times: a start every 30 minutes inside the showing window, up to 14
# days out. An agent needs 30 minutes to get from one address to another, and
# none between showings at the same address. Free/busy answers are reused for
# about 90 seconds per connection and set of calendars.
START_EVERY_MINUTES = 30
DAYS_AHEAD = 14
TRAVEL_MINUTES = 30
FREE_BUSY_CACHE_SECONDS = 90
# A showing already booked can be joined until this many hours before its
# start, inside the notice period too: its residents were told when it was
# first booked. A new time still needs the full notice.
JOIN_CLOSES_HOURS = 2

_E164_US = re.compile(r"\+1[2-9]\d{2}[2-9]\d{6}")
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def normalize_us_mobile(value: str) -> str | None:
    """``+1XXXXXXXXXX`` for a ten-digit US number in any common spelling, else None."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        digits = "1" + digits
    candidate = f"+{digits}"
    return candidate if _E164_US.fullmatch(candidate) else None


def normalize_email(value: str) -> str | None:
    """The address lowercased and trimmed, or None if it is not one bare address."""
    candidate = (value or "").strip().lower()
    return candidate if _EMAIL.fullmatch(candidate) else None


def _mobile(value: str) -> str:
    normalized = normalize_us_mobile(value)
    if normalized is None:
        raise ValueError("Enter a ten-digit US mobile number.")
    return normalized


def _email(value: str) -> str:
    normalized = normalize_email(value)
    if normalized is None:
        raise ValueError("Enter one email address.")
    return normalized


Mobile = Annotated[str, AfterValidator(_mobile)]
Email = Annotated[str, AfterValidator(_email)]


class RequirementConfirmation(BaseModel):
    """One tick on the booking form, kept with the words the person saw."""

    model_config = ConfigDict(extra="forbid")

    key: str  # "lease_start" or "pets"
    wording: str
    confirmed_at: datetime
    # The version of the wording's template (``wording.CONFIRMATIONS_VERSION``).
    version: str | None = None


class ShowingAgent(BaseModel):
    """A person who can host showings. Hours and preferred windows use the listings' shape."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    email: Email
    mobile: Mobile | None = None
    connection_id: int | None = None
    # The calendars that make this agent busy, read through ``connection_id``.
    busy_calendar_ids: list[str] = Field(default_factory=list)
    weekly_hours: list[PreferredWindow] = Field(default_factory=list)
    preferred_windows: list[PreferredWindow] = Field(default_factory=list)
    # Lower comes first when several agents are free.
    priority: int = 1
    active: bool = True


# What a notice delivers. Stored on the notice row as rendered, so what a
# captured row shows is exactly what would have gone out.


class TextPayload(BaseModel):
    """A text. With ``also_to`` it is one group text to the notice's recipient and
    these numbers, who share one thread. With ``daytime_timezone`` it goes out only
    from 8 AM to 9 PM there, waiting for 8 AM otherwise (a resident's text or a
    reminder). ``showing_ids`` are the showings a residents' reminder lists; at
    delivery it lists only those still ahead."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1600)
    also_to: list[Mobile] = Field(default_factory=list, max_length=9)
    daytime_timezone: str | None = None
    showing_ids: list[int] = Field(default_factory=list)


class EmailPayload(BaseModel):
    """An email. Two sorts are sent:

    - The reminder in a Zillow lead's thread (``lead_email.py``), plain text.
      ``conversation_id`` is the lead conversation whose stored reply route the
      reminder goes to. ``reply_to_message_id`` is that route's message, which
      the email answers in the thread; it and the notice's recipient and subject
      are set from the conversation each time the email is about to go, never
      from a request or anything a lead wrote.
    - The prospect's emails (``prospect_email.py``): a new message from the
      shared mailbox to the address on the booking, as ``body`` (plain text) and
      ``html``.

    Any other email the guard would send is saved as failed. With
    ``daytime_timezone`` it goes out only from 8 AM to 9 PM there."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    html: str | None = None
    conversation_id: str | None = None
    reply_to_message_id: str | None = None
    daytime_timezone: str | None = None


class CalendarPayload(BaseModel):
    """An event to create, change or cancel, with the people it invites.

    ``event_id`` names the event to change or cancel. Left out, the event id
    saved on the showing (host's event) or the attendee (prospect's invite)
    is used, so a change can be queued before the event it changes is created.
    """

    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "update", "cancel"]
    calendar_id: str = Field(min_length=1)
    event_id: str | None = None
    summary: str = Field(min_length=1)
    description: str = ""
    location: str = ""
    starts_at: datetime
    ends_at: datetime
    attendees: list[Email] = Field(default_factory=list)


class Notice(BaseModel):
    """One notice row as the team's Messages list shows it."""

    id: int
    kind: str
    # What the notice is, in words (``wording.notice_label``).
    kind_label: str
    channel: Channel
    showing_id: int
    attendee_id: int | None
    unit: str
    showing_starts_at: datetime
    recipient: str
    recipient_key: str
    # The text's body, the email's subject and body, or the event's details.
    wording: str
    payload: dict
    status: NoticeStatus
    mode: NoticeMode | None
    guard_reason: str | None
    # Who sent a captured message by hand, and when; empty for any other.
    send_approved_by: str | None = None
    send_approved_at: datetime | None = None
    not_before: datetime
    attempts: int
    provider_status: str | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    # "failed" (failed, or may have gone out, and never tried again) or "stuck"
    # (not gone out ten minutes after it was due), as ``alerts.py`` decides;
    # empty when it needs no one.
    attention: NoticeAttention | None = None
    # When the team was alerted about it, once.
    alerted_at: datetime | None = None


class HoldReport(BaseModel):
    """What one run of ``holds.release_held`` did with the held notices."""

    released: int = 0
    stopped: int = 0


class DrainReport(BaseModel):
    """What one run of the sending step did, by outcome."""

    captured: int = 0
    sent: int = 0
    failed: int = 0
    unknown: int = 0
    skipped: int = 0
    # Nothing went out and it will be tried again after a pause.
    retrying: int = 0
    # Sends found started and never finished: marked unknown, never retried.
    interrupted: int = 0

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())
