"""What the booking code needs from the outside world, as two small interfaces.

- ``TextSender``: send a text or a group text, and read a sent text's status. Quo in
  production (``quo.py``); ``fakes.RecordingTextSender`` in tests.
- ``CalendarSource``: busy times for a set of calendars, and events with
  invited guests. Google later; ``fakes.InMemoryCalendar`` in tests.

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
from typing import Protocol

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


class TextSender(Protocol):
    async def send_text(self, to: str, body: str) -> SentText:
        """Send ``body`` to one number in E.164. Raises ``NotSent`` when nothing went out."""
        ...

    async def send_group_text(self, to: Sequence[str], body: str) -> SentText:
        """Send ``body`` to 2 to 10 numbers in E.164 as one group text, so they share
        one thread. Raises ``NotSent`` when nothing went out."""
        ...

    async def text_status(self, provider_id: str) -> str:
        """The provider's current status for a sent text, such as "delivered" or "undelivered"."""
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


class EventDraft(BaseModel):
    """An event as a calendar should hold it, with the people it invites."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    description: str = ""
    location: str = ""
    starts_at: datetime
    ends_at: datetime
    attendees: list[str]


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
