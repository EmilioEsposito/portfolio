"""What the booking code needs from the outside world, as four small interfaces.

- ``TextSender``: send a text or a group text from one of the team's two lines
  (``lines.py`` picks it), and read a sent text's status. Quo in production
  (``quo.py``); ``fakes.RecordingTextSender`` in tests.
- ``CalendarSource``: busy times for a set of calendars, and events with
  invited guests. Google later; ``fakes.InMemoryCalendar`` in tests.
- ``EmailSender``: an email in a Zillow lead's thread, to the relay address
  stored on the lead conversation. Gmail, through the lead agent's own sending
  function (``gmail_thread.py``); ``fakes.RecordingEmailSender`` in tests.
- ``TeamAlerter``: an alert on the team's phones. The Sernia web push the
  approval flow uses, in production only (``push.py``);
  ``fakes.RecordingAlerter`` in tests.

Only ``outbox.py`` hands a notice to one of these, so nothing reaches a
person except through the guard. Reading busy times is not a notice and is
used by the open-times code.

How a provider reports trouble decides what the outbox does next:

- ``NotSent``: the provider answered and did not act, or the request never
  reached it. Nothing went out. Saved as failed, or tried again later when
  ``retryable``.
- Any other exception: the request may have gone out (a timeout, an answer
  that was lost). Saved as unknown and never retried, so nobody is texted twice.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class NotSent(Exception):
    """Nothing went out, and we know it.

    ``reason`` is a short key code can branch on, such as "opted_out",
    "invalid_number", "unreachable_number", "rejected" or "unavailable".
    ``detail`` is what the team reads.
    """

    def __init__(self, reason: str, detail: str = "", *, retryable: bool = False):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail or reason
        self.retryable = retryable


@dataclass(frozen=True)
class SentText:
    provider_id: str
    # The provider's status at the time of sending, such as "queued" or "sent".
    provider_status: str | None = None


# The team's two Quo lines: "team" is the shared team line, "ai" the Sernia AI
# line. ``lines.line_for`` decides which one sends each kind of text.
Line = Literal["team", "ai"]


class TextSender(Protocol):
    async def send_text(self, to: str, body: str, *, line: Line = "team") -> SentText:
        """Send ``body`` from ``line`` to one number in E.164. Raises ``NotSent`` when
        nothing went out."""
        ...

    async def send_group_text(
        self, to: Sequence[str], body: str, *, line: Line = "team"
    ) -> SentText:
        """Send ``body`` from ``line`` to 2 to 10 numbers in E.164 as one group text, so
        they share one thread. Raises ``NotSent`` when nothing went out."""
        ...

    async def text_status(self, provider_id: str) -> str:
        """The provider's current status for a sent text, such as "delivered" or
        "undelivered", whichever line sent it."""
        ...


@dataclass(frozen=True)
class BusyBlock:
    start: datetime
    end: datetime


class CalendarUnreadable(Exception):
    """One or more calendars could not be read.

    ``unreadable`` maps each such calendar to the reason. ``busy`` holds the
    calendars that were read, so one lost calendar does not hide everyone
    else's times. A caller counts the unreadable calendars' owners as busy.
    """

    def __init__(self, unreadable: dict[str, str], busy: dict[str, list[BusyBlock]]):
        names = ", ".join(sorted(unreadable))
        super().__init__(f"Could not read calendar(s): {names}")
        self.unreadable = unreadable
        self.busy = busy


# How reading one of Google's two lists for the calendar picker went:
# "not_granted" (the connection lacks the permission), "api_off" (the API is
# off in the app's Google Cloud project), "sharing_off" (the Workspace does not
# share its directory with apps), "refused" (Google said no, and not why) or
# "failed" (no answer, or a limit: trying again may work).
ListStatus = Literal["read", "not_granted", "api_off", "sharing_off", "refused", "failed"]


@dataclass(frozen=True)
class ListedCalendar:
    calendar_id: str  # for a person, their primary email address
    name: str
    # The connected account's own calendar.
    own: bool = False


@dataclass(frozen=True)
class CalendarList:
    """One of the lists behind the calendar picker, with the entries read before any trouble."""

    status: ListStatus
    entries: tuple[ListedCalendar, ...] = ()
    # Google has more than was read.
    more: bool = False


class EventDraft(BaseModel):
    """An event as a calendar should hold it, with the people it invites."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    description: str = ""
    location: str = ""
    starts_at: datetime
    ends_at: datetime
    attendees: list[str]


@dataclass(frozen=True)
class ThreadRoute:
    """Where an email in a lead's Zillow thread goes: the relay address, the
    thread's subject, and the message it answers. Always read from the lead
    conversation's stored route (``lead_email.route_of``)."""

    to: str
    subject: str
    reply_to_message_id: str


@dataclass(frozen=True)
class SentEmail:
    provider_id: str


class EmailSender(Protocol):
    async def send_in_thread(self, route: ThreadRoute, body: str) -> SentEmail:
        """Send ``body`` as plain text to ``route.to``, as a reply in the thread of
        ``route.reply_to_message_id``. Raises ``NotSent`` when nothing went out,
        including when the thread cannot be found, so it never goes unthreaded."""
        ...


class TeamAlerter(Protocol):
    async def send_alert(self, title: str, body: str, url: str) -> None:
        """Get the team's attention: ``title`` and ``body`` on their phones, opening
        ``url`` (a path on the site) when tapped. Never names a prospect's or a
        resident's number."""
        ...


class CalendarSource(Protocol):
    async def busy_blocks(
        self, calendar_ids: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[BusyBlock]]:
        """Each calendar's busy times between ``start`` and ``end``.

        Raises ``CalendarUnreadable`` when any calendar cannot be read.
        """
        ...

    async def create_event(self, calendar_id: str, event: EventDraft) -> str:
        """Create the event, invite its attendees, and return its id."""
        ...

    async def update_event(self, calendar_id: str, event_id: str, event: EventDraft) -> None:
        """Change the event and tell its attendees."""
        ...

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        """Cancel the event and tell its attendees."""
        ...
