"""Stand-ins for Quo and the calendars, for tests and local runs. Nothing here leaves the process.

- ``RecordingTextSender`` keeps every text that would have gone out, in
  order, and can be told to refuse a number, lose an answer or report a text
  undelivered.
- ``InMemoryCalendar`` holds busy times and events in memory, and can be told
  to lose a calendar.
"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from api.src.showing_bookings.providers import (
    BusyBlock,
    CalendarUnreadable,
    EventDraft,
    NotSent,
    SentText,
)

# Quo's answers to a refused number, by reason.
_REFUSALS = {
    "opted_out": "User Opted Out",
    "invalid_number": "Invalid `to` Phone Number",
    "unreachable_number": "Unreachable `to` Phone Number",
}


@dataclass(frozen=True)
class RecordedText:
    to: str
    body: str
    provider_id: str


class RecordingTextSender:
    """A ``TextSender`` that records texts instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[RecordedText] = []
        self.status_reads: list[str] = []
        self._refusals: dict[str, NotSent] = {}
        self._lost_answers: set[str] = set()
        self._statuses: dict[str, str] = {}
        self._count = 0

    def refuse(self, number: str, reason: str = "invalid_number", *, retryable: bool = False):
        """Refuse every text to ``number`` the way Quo does: nothing is recorded as sent."""
        self._refusals[number] = NotSent(reason, _REFUSALS.get(reason, reason), retryable=retryable)

    def lose_answer(self, number: str) -> None:
        """Send texts to ``number`` but fail before answering, as a timeout would."""
        self._lost_answers.add(number)

    def report(self, number: str, status: str) -> None:
        """Answer ``status`` (such as "undelivered") when a text to ``number`` is read back."""
        self._statuses[number] = status

    def to(self, number: str) -> list[str]:
        """The bodies of the texts sent to ``number``, in order."""
        return [text.body for text in self.sent if text.to == number]

    async def send_text(self, to: str, body: str) -> SentText:
        # Let other tasks run first, so concurrent deliveries really interleave.
        await asyncio.sleep(0)
        if to in self._refusals:
            raise self._refusals[to]
        self._count += 1
        provider_id = f"AC{self._count:04d}"
        self.sent.append(RecordedText(to=to, body=body, provider_id=provider_id))
        if to in self._lost_answers:
            raise TimeoutError("The text went out but its answer was lost.")
        return SentText(provider_id=provider_id, provider_status="queued")

    async def text_status(self, provider_id: str) -> str:
        self.status_reads.append(provider_id)
        number = next((text.to for text in self.sent if text.provider_id == provider_id), None)
        if number is None:
            raise LookupError(f"No text {provider_id} was sent.")
        return self._statuses.get(number, "delivered")


@dataclass
class StoredEvent:
    event_id: str
    calendar_id: str
    draft: EventDraft
    cancelled: bool = False


class InMemoryCalendar:
    """A ``CalendarSource`` kept in memory: busy blocks in, events out."""

    def __init__(self) -> None:
        self.busy: dict[str, list[BusyBlock]] = defaultdict(list)
        self.events: dict[str, StoredEvent] = {}
        # Every change in order: ("create" | "update" | "cancel", calendar id, event id).
        self.log: list[tuple[str, str, str]] = []
        self._lost: dict[str, str] = {}
        self._count = 0

    def add_busy(self, calendar_id: str, start: datetime, end: datetime) -> None:
        self.busy[calendar_id].append(BusyBlock(start=start, end=end))

    def lose(self, calendar_id: str, reason: str = "Access to the calendar was revoked.") -> None:
        """Make ``calendar_id`` unreadable and unwritable, as a revoked grant would."""
        self._lost[calendar_id] = reason

    def restore(self, calendar_id: str) -> None:
        self._lost.pop(calendar_id, None)

    def events_in(self, calendar_id: str) -> list[StoredEvent]:
        return [event for event in self.events.values() if event.calendar_id == calendar_id]

    async def busy_blocks(
        self, calendar_ids: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[BusyBlock]]:
        read: dict[str, list[BusyBlock]] = {}
        unreadable: dict[str, str] = {}
        for calendar_id in calendar_ids:
            if calendar_id in self._lost:
                unreadable[calendar_id] = self._lost[calendar_id]
                continue
            overlapping = [
                b for b in self.busy.get(calendar_id, []) if b.start < end and b.end > start
            ]
            read[calendar_id] = sorted(
                (BusyBlock(start=max(b.start, start), end=min(b.end, end)) for b in overlapping),
                key=lambda block: block.start,
            )
        if unreadable:
            raise CalendarUnreadable(unreadable, read)
        return read

    def _writable(self, calendar_id: str) -> None:
        if calendar_id in self._lost:
            raise NotSent("calendar_lost", self._lost[calendar_id])

    async def create_event(self, calendar_id: str, event: EventDraft) -> str:
        self._writable(calendar_id)
        self._count += 1
        event_id = f"evt{self._count:04d}"
        self.events[event_id] = StoredEvent(event_id=event_id, calendar_id=calendar_id, draft=event)
        self.log.append(("create", calendar_id, event_id))
        return event_id

    def _existing(self, calendar_id: str, event_id: str) -> StoredEvent:
        self._writable(calendar_id)
        stored = self.events.get(event_id)
        if stored is None or stored.calendar_id != calendar_id:
            raise NotSent("no_such_event", f"Event {event_id} is not on {calendar_id}.")
        return stored

    async def update_event(self, calendar_id: str, event_id: str, event: EventDraft) -> None:
        self._existing(calendar_id, event_id).draft = event
        self.log.append(("update", calendar_id, event_id))

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        self._existing(calendar_id, event_id).cancelled = True
        self.log.append(("cancel", calendar_id, event_id))
