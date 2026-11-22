"""Captured messages, and sending them by hand from the Booking settings tab.

A captured message was written and kept, and no provider was called. A person
can send it later:

- ``list_captured`` is the tab's Captured messages list. Each message is written
  as it would be sent now, with a state: ``ready``; ``too_late``; or
  ``no_longer_applies`` with the reason (the booking was cancelled, the showing
  moved to another host, the number is known to be wrong, the event it changes
  was never sent). The last two are listed for two days after they were
  captured, then left to the Messages list.
- ``send`` sends the ones a person picked. In one transaction each row is locked,
  must still be captured and ready, and becomes ``pending``, or ``held`` while it
  waits for its confirmation text, with ``send_approved_by`` and
  ``send_approved_at`` saved on it. From there it is delivered by the outbox like
  any other: the same checks again as it goes, retries, alerts. So two people
  pressing at once send nothing twice, and a process that stops midway leaves
  rows the job every minute finishes.

The states come from the checks a delivery makes, run on a copy
(``outbox.preview``), and ``delivery_checks.too_late`` is the one rule for too
late, so the list, the send and the delivery agree.

A message waits for its confirmation text as it did when it was first queued
(``waits_for_notice_id``, ``holds.py``) when that text is sent with it, or is on
its way. When the text is left unsent the person chose that, and the message
goes by itself. When the text went and did not reach the number, the message no
longer applies, as today. ``_holder`` finds the person's latest text to the
number on the booking, should it have been corrected since.

Nothing here reaches a provider: the outbox does, behind the guard, which lets a
message sent by hand go in any mode in production and, elsewhere, only to the
test list.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal

import logfire
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import (
    delivery_checks,
    holds,
    host_event,
    outbox,
    residents,
    wording,
)
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import CalendarSource, EmailSender, TextSender
from api.src.showing_bookings.schema import Channel

State = Literal["ready", "too_late", "no_longer_applies"]
# sent; waiting (it goes by itself later); unknown (it may have gone out);
# not_sent, with why; kept (captured again: outside production only the test
# list is reached); handled (it was no longer captured when it was picked).
Outcome = Literal["sent", "waiting", "unknown", "not_sent", "kept", "handled"]

# Messages that can no longer be sent stay on the list this long.
KEEP_LISTED = timedelta(days=2)
LIST_LIMIT = 500
MAX_SENT_AT_ONCE = 200
MAX_ID = 2**31 - 1  # the column's own limit
# How long one request delivers for. Whatever is left is pending, and the job
# every minute takes it.
DELIVER_FOR_SECONDS = 45

NOT_REACHED = "Not sent: its confirmation text did not reach the person."
RESIDENTS_DAYTIME = "Residents get texts only between 8 AM and 9 PM."
DAYTIME = "Goes only between 8 AM and 9 PM."
EVENT_BUSY = "Goes in a minute: another change to the host's event is being sent."
KEPT = "Kept here: outside production only the test list can be reached."
RESIDENT_KINDS = residents.TEXT_KINDS | {delivery_checks.RESIDENT_REMINDER_KIND}


class CapturedNotice(BaseModel):
    """One captured message, as the Captured messages list shows it."""

    id: int
    kind: str
    kind_label: str
    channel: Channel
    unit: str
    showing_starts_at: datetime
    # "Alex Parker, 412-555-0117": who it goes to now.
    to: str
    # As it would be sent now.
    wording: str
    # The lead conversation an email answers in.
    conversation_id: str | None = None
    state: State
    # Why it cannot be sent, for any state but ready.
    reason: str | None = None
    # Why a ready one would wait, whatever else is sent with it.
    note: str | None = None
    # The confirmation text it waits for, when that is captured and ready too: it
    # waits for it only when both are sent.
    waits_for_notice_id: int | None = None
    # That text is on its way, so it waits for it.
    confirmation_on_its_way: bool = False
    # Sending it really sends it: in production always, elsewhere only to the test list.
    goes_out: bool
    captured_at: datetime


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[Annotated[int, Field(ge=1, le=MAX_ID)]] = Field(
        min_length=1, max_length=MAX_SENT_AT_ONCE
    )


class SendResult(BaseModel):
    """What happened to one message a person sent."""

    id: int
    kind_label: str
    unit: str
    showing_starts_at: datetime | None
    to: str
    outcome: Outcome
    # Why it waits, was not sent, or may have gone out; empty when it was sent.
    detail: str = ""
    # When a waiting one goes at the earliest, when that is known.
    waits_until: datetime | None = None


class SendReport(BaseModel):
    # In the order they were picked.
    results: list[SendResult]

    def count(self, outcome: Outcome) -> int:
        return sum(1 for result in self.results if result.outcome == outcome)


def why(note: str | None) -> str:
    """A row's note as the list says it: "The showing had started."."""
    words = (note or "").removeprefix("Not sent: ")
    return words[:1].upper() + words[1:]


# ---------------------------------------------------------------------------
# What each captured message would do
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Look:
    """One captured notice as it stands now."""

    state: State
    reason: str | None
    seen: outbox.Preview
    holder: ShowingNotice | None


async def _holder(session: AsyncSession, notice: ShowingNotice) -> ShowingNotice | None:
    """The confirmation text a notice waits for now, or None when it waits for none.

    The one it names; or, when that person's number was corrected since it was
    captured, their latest text to the number on the booking.
    """
    if notice.waits_for_notice_id is None:
        return None
    named = await session.get(ShowingNotice, notice.waits_for_notice_id)
    if named is None or named.kind != host_event.CONFIRMATION_KIND or named.attendee_id is None:
        return named
    current = await session.scalar(
        select(ShowingNotice)
        .join(ShowingAttendee, ShowingAttendee.id == ShowingNotice.attendee_id)
        .where(
            ShowingNotice.attendee_id == named.attendee_id,
            ShowingNotice.kind == host_event.CONFIRMATION_KIND,
            ShowingNotice.recipient == ShowingAttendee.mobile,
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    return current or named


def _did_not_reach(holder: ShowingNotice | None) -> bool:
    """The confirmation text went, or was tried, and did not reach the person."""
    if holder is None:
        return False
    if holder.status == "sent":
        return holder.provider_status in holds.UNDELIVERED
    return holder.status in ("failed", "cancelled")


def _on_its_way(holder: ShowingNotice | None) -> bool:
    """The confirmation text is going, or went and has not been reported delivered."""
    if holder is None:
        return False
    if holder.status == "sent":
        return holder.provider_status not in holds.DELIVERED
    return holder.status in ("pending", "held", "unknown")


async def _look(session: AsyncSession, notice: ShowingNotice, unit: str, now: datetime) -> _Look:
    late = await delivery_checks.too_late(session, notice, now)
    if late is not None:
        # Too late is the first thing a delivery checks, so nothing else is looked at.
        as_kept = outbox.Preview(late, None, False, notice.recipient, dict(notice.payload))
        return _Look("too_late", late, as_kept, None)
    seen = await outbox.preview(session, notice, unit, now=now)
    holder = await _holder(session, notice)
    if seen.problem is not None:
        return _Look("no_longer_applies", seen.problem, seen, holder)
    if _did_not_reach(holder):
        return _Look("no_longer_applies", NOT_REACHED, seen, holder)
    return _Look("ready", None, seen, holder)


def _note(notice: ShowingNotice, look: _Look) -> str | None:
    if look.seen.later is None:
        return None
    if not notice.payload.get("daytime_timezone"):
        return EVENT_BUSY
    return RESIDENTS_DAYTIME if notice.kind in RESIDENT_KINDS else DAYTIME


def _address(value: str) -> str:
    return wording.display_phone(value) if value.startswith("+1") else value


class _Names:
    """Who a notice goes to, by name, from what one request reads."""

    def __init__(self, hosts: dict[int, str], people: dict[int, str]) -> None:
        self.hosts = hosts
        self.people = people

    @classmethod
    async def read(cls, session: AsyncSession, attendee_ids: set[int]) -> "_Names":
        hosts = await session.execute(select(ShowingAgentRecord.id, ShowingAgentRecord.name))
        people = await session.execute(
            select(ShowingAttendee.id, ShowingAttendee.name).where(
                ShowingAttendee.id.in_(attendee_ids)
            )
        )
        return cls(dict(hosts.all()), dict(people.all()))

    def to(self, notice: ShowingNotice, host_id: int | None, recipient: str, payload: dict) -> str:
        """ "Alex Parker, 412-555-0117"."""
        addresses = [recipient, *payload.get("also_to", [])]
        if notice.recipient_key.startswith("agent:"):
            named = delivery_checks.named_agent(notice.recipient_key)
            # These name their own host; every other host notice goes to the host now.
            own = notice.kind in delivery_checks.HOST_MOVED_KINDS | {
                host_event.MOVED_KIND,
                delivery_checks.HOST_MORNING_KIND,
            }
            name = self.hosts.get(named if own or host_id is None else host_id, "")
            if notice.channel != "sms":
                addresses = payload.get("attendees") or addresses
        elif notice.recipient_key.startswith("resident"):
            name = "Residents"
        else:
            name = self.people.get(notice.attendee_id or 0, "")
        return ", ".join([item for item in [name, *map(_address, addresses)] if item])


async def list_captured(session: AsyncSession, *, now: datetime) -> list[CapturedNotice]:
    """The Captured messages list: the ready ones first, each by showing. Changes nothing."""
    rows = (
        await session.execute(
            select(ShowingNotice, Showing)
            .join(Showing, Showing.id == ShowingNotice.showing_id)
            .where(
                ShowingNotice.status == "captured",
                # Any that could be ready (the apply text is the last to be too late),
                # and the ones still listed because they were captured lately.
                or_(
                    Showing.starts_at > now - delivery_checks.APPLY_LOOKBACK,
                    ShowingNotice.delivered_at > now - KEEP_LISTED,
                ),
            )
            .order_by(Showing.starts_at, Showing.id, ShowingNotice.id)
            .limit(LIST_LIMIT)
        )
    ).all()
    names = await _Names.read(
        session, {notice.attendee_id for notice, _ in rows if notice.attendee_id is not None}
    )
    looks = {notice.id: await _look(session, notice, showing.unit, now) for notice, showing in rows}
    ready = {notice_id for notice_id, look in looks.items() if look.state == "ready"}
    listed: list[CapturedNotice] = []
    for notice, showing in rows:
        look = looks[notice.id]
        captured_at = notice.delivered_at or notice.created_at
        if look.state != "ready" and captured_at <= now - KEEP_LISTED:
            continue
        is_ready = look.state == "ready"
        holder = look.holder
        listed.append(
            CapturedNotice(
                id=notice.id,
                kind=notice.kind,
                kind_label=wording.notice_label(notice.kind),
                channel=notice.channel,
                unit=showing.unit,
                showing_starts_at=showing.starts_at,
                to=names.to(notice, showing.agent_id, look.seen.recipient, look.seen.payload),
                wording=wording.notice_text(notice.channel, look.seen.payload),
                conversation_id=look.seen.payload.get("conversation_id"),
                state=look.state,
                reason=why(look.reason) if look.reason else None,
                note=_note(notice, look) if is_ready else None,
                waits_for_notice_id=(
                    holder.id if is_ready and holder is not None and holder.id in ready else None
                ),
                confirmation_on_its_way=is_ready and _on_its_way(holder),
                goes_out=look.seen.goes_out,
                captured_at=captured_at,
            )
        )
    return sorted(listed, key=lambda item: item.state != "ready")


# ---------------------------------------------------------------------------
# Sending them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Picked:
    """What ``_approve`` decided about the messages a person picked."""

    # The ones now pending or held, in the order they are delivered.
    approved: list[int]
    # The others, with why: no longer captured, or cannot be sent.
    handled: set[int]
    refused: dict[int, str]


async def _approve(ids: list[int], *, by: str, now: datetime) -> _Picked:
    """Make the picked rows pending, or held behind their confirmation text, as sent
    by hand. One transaction: each row is locked and must still be captured and ready."""
    async with AsyncSessionFactory() as session, session.begin():
        rows = (
            await session.execute(
                select(ShowingNotice, Showing.unit)
                .join(Showing, Showing.id == ShowingNotice.showing_id)
                .where(ShowingNotice.id.in_(ids))
                .order_by(ShowingNotice.id)
                .with_for_update(of=ShowingNotice)
                .execution_options(populate_existing=True)
            )
        ).all()
        handled = {notice.id for notice, _ in rows if notice.status != "captured"}
        refused: dict[int, str] = {}
        looks: dict[int, _Look] = {}
        for notice, unit in rows:
            if notice.id in handled:
                continue
            look = await _look(session, notice, unit, now)
            if look.state == "ready":
                looks[notice.id] = look
            else:
                refused[notice.id] = why(look.reason)
        picked = [notice for notice, _ in rows if notice.id in looks]
        for notice in picked:
            holder = looks[notice.id].holder
            waits = holder is not None and (holder.id in looks or _on_its_way(holder))
            notice.status = "held" if waits else "pending"
            if waits:
                notice.waits_for_notice_id = holder.id
            notice.send_approved_by = by
            notice.send_approved_at = now
            notice.not_before = now
            # What it was captured with no longer holds.
            notice.mode = notice.guard_reason = notice.delivered_at = notice.last_error = None
    # The confirmation texts first, as others wait for them.
    order = sorted(picked, key=lambda n: (n.kind != host_event.CONFIRMATION_KIND, n.id))
    return _Picked([notice.id for notice in order], handled, refused)


async def _deliver(
    ids: list[int],
    *,
    now: datetime,
    until: float,
    texts: TextSender | None,
    calendar: CalendarSource | None,
    emails: EmailSender | None,
) -> None:
    for notice_id in ids:
        if time.monotonic() > until:
            return
        try:
            await outbox.deliver(notice_id, now=now, texts=texts, calendar=calendar, emails=emails)
        except Exception:
            # The row stays pending or is saved as unknown; the job takes it from there.
            logfire.exception("showing message sent by hand could not be delivered now")


async def send(
    ids: list[int],
    *,
    by: str,
    now: datetime,
    texts: TextSender | None = None,
    calendar: CalendarSource | None = None,
    emails: EmailSender | None = None,
) -> SendReport:
    """Send captured messages a person picked, and answer what happened to each.

    Delivers for up to ``DELIVER_FOR_SECONDS``; what is left is pending, for the
    job every minute.
    """
    ids = list(dict.fromkeys(ids))
    picked = await _approve(ids, by=by, now=now)
    until = time.monotonic() + DELIVER_FOR_SECONDS
    senders = {"texts": texts, "calendar": calendar, "emails": emails}
    await _deliver(picked.approved, now=now, until=until, **senders)
    if await _any_held(picked.approved):
        # As the job does: one whose confirmation has an answer already goes now.
        try:
            await holds.release_held(now=now, texts=texts)
        except Exception:
            logfire.exception("showing messages sent by hand: held ones not released now")
        await _deliver(picked.approved, now=now, until=until, **senders)
    return await _report(ids, picked, now)


async def _any_held(ids: list[int]) -> bool:
    async with AsyncSessionFactory() as session:
        return bool(
            await session.scalar(
                select(ShowingNotice.id)
                .where(ShowingNotice.id.in_(ids), ShowingNotice.status == "held")
                .limit(1)
            )
        )


def _outcome(notice: ShowingNotice, now: datetime) -> tuple[Outcome, str, datetime | None]:
    if notice.status == "sent":
        return "sent", "", None
    if notice.status == "captured":
        return "kept", KEPT, None
    if notice.status == "held":
        return "waiting", "Goes once the confirmation is delivered.", None
    if notice.status == "pending":
        if notice.sending_started_at is not None:
            return "waiting", "Being sent now.", None
        if notice.not_before <= now:
            return "waiting", "Goes within a minute.", None
        if notice.attempts:
            return "waiting", f"Nothing went out yet: {notice.last_error}", notice.not_before
        if notice.payload.get("daytime_timezone"):
            note = RESIDENTS_DAYTIME if notice.kind in RESIDENT_KINDS else DAYTIME
            return "waiting", note, notice.not_before
        return "waiting", EVENT_BUSY, notice.not_before
    if notice.status == "unknown":
        return "unknown", "It may have gone out. Check before sending it again.", None
    return "not_sent", why(notice.last_error) or "It was refused.", None


async def _report(ids: list[int], picked: _Picked, now: datetime) -> SendReport:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(ShowingNotice, Showing)
                .join(Showing, Showing.id == ShowingNotice.showing_id)
                .where(ShowingNotice.id.in_(ids))
            )
        ).all()
        names = await _Names.read(
            session, {notice.attendee_id for notice, _ in rows if notice.attendee_id is not None}
        )
    found = {notice.id: (notice, showing) for notice, showing in rows}
    results = []
    for notice_id in ids:
        if notice_id not in found:
            results.append(
                SendResult(
                    id=notice_id,
                    kind_label="Message",
                    unit="",
                    showing_starts_at=None,
                    to="",
                    outcome="not_sent",
                    detail="There is no such message.",
                )
            )
            continue
        notice, showing = found[notice_id]
        waits_until = None
        if notice_id in picked.refused:
            outcome, detail = "not_sent", picked.refused[notice_id]
        elif notice_id in picked.handled:
            outcome, detail = "handled", "It was already handled; see Messages."
        else:
            outcome, detail, waits_until = _outcome(notice, now)
        results.append(
            SendResult(
                id=notice_id,
                kind_label=wording.notice_label(notice.kind),
                unit=showing.unit,
                showing_starts_at=showing.starts_at,
                to=names.to(notice, showing.agent_id, notice.recipient, notice.payload),
                outcome=outcome,
                detail=detail,
                waits_until=waits_until,
            )
        )
    return SendReport(results=results)
