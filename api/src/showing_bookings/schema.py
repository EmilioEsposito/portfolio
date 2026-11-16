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
# is invited to (one per prospect, so guests never see each other), or an email.
Channel = Literal["sms", "calendar_event", "calendar_invite", "email"]
# pending: waiting to be delivered. held: waiting for the notice it waits for
# (``waits_for_notice_id``, a prospect's confirmation text) to be reported
# delivered; ``holds.release_held`` makes it pending or cancels it. Then
# exactly one of: captured (rendered and kept, no provider called), sent,
# failed (the provider refused or never got it), unknown (it may have gone
# out; never retried), skipped (send mode off), cancelled (never sent: the
# booking was withdrawn or its confirmation text was not delivered).
NoticeStatus = Literal[
    "pending", "held", "captured", "sent", "failed", "unknown", "skipped", "cancelled"
]
SendMode = Literal["off", "capture", "live"]

CONNECTION_STATUSES: tuple[str, ...] = get_args(ConnectionStatus)
SHOWING_STATUSES: tuple[str, ...] = get_args(ShowingStatus)
ATTENDEE_STATUSES: tuple[str, ...] = get_args(AttendeeStatus)
ATTENDANCES: tuple[str, ...] = get_args(Attendance)
ATTENDEE_SOURCES: tuple[str, ...] = get_args(AttendeeSource)
CHANNELS: tuple[str, ...] = get_args(Channel)
NOTICE_STATUSES: tuple[str, ...] = get_args(NoticeStatus)
SEND_MODES: tuple[str, ...] = get_args(SendMode)

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
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1600)


class EmailPayload(BaseModel):
    """An email. Nothing sends email yet: the outbox captures these, and one the
    guard would send is saved as failed."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


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
    mode: SendMode | None
    guard_reason: str | None
    not_before: datetime
    attempts: int
    provider_status: str | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None


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
