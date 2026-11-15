"""The notice outbox: the only code in ``showing_bookings`` that hands a notice to a provider.

Every text, calendar event and invite a booking owes is first a row in
``showing_notices``, written by ``enqueue`` in the same transaction as the
booking. ``deliver`` (one row, right after the booking commits) and ``drain``
(every due row, from a job or the team's "Send pending now") then take each
row through the guard in ``guard.py``:

- skip: send mode is off. Saved as ``skipped``.
- capture: the row already holds the rendered message. Saved as
  ``captured``; no provider is called.
- send: in three steps, so a row is never sent twice.

  1. Claim. The row is locked (``FOR UPDATE SKIP LOCKED``, so two runs at once
     take different rows), ``attempts`` goes up and ``sending_started_at`` is
     set. This is committed before the provider is called.
  2. Call the provider, outside any transaction.
  3. Save the answer: ``sent``; ``failed`` when the provider refused it (an
     opted-out or invalid number); ``pending`` again with a later
     ``not_before`` when nothing went out and trying later may work, until
     ``MAX_ATTEMPTS``; ``unknown`` when it may have gone out (a timeout, a lost
     answer). An unknown row is never retried.

  A row still marked as sending ``INTERRUPTED_AFTER`` later was cut off
  mid-send, by a restart for example. It may have gone out, so it becomes
  ``unknown`` too.

Every function that depends on the time takes ``now``. Tests pass the stand-ins
from ``fakes.py``; left out, the live senders are used, and under pytest that
fails loudly instead of reaching Quo.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import logfire
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings.guard import decide
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import (
    CalendarSource,
    EventDraft,
    NotSent,
    TextSender,
)
from api.src.showing_bookings.quo import QuoTextSender
from api.src.showing_bookings.schema import (
    CalendarPayload,
    Channel,
    DrainReport,
    NoticeStatus,
    TextPayload,
    normalize_email,
    normalize_us_mobile,
)
from api.src.showing_bookings.settings import (
    current_environment,
    read_test_recipients,
    settings_for_delivery,
)

MAX_ATTEMPTS = 5
INTERRUPTED_AFTER = timedelta(minutes=10)
DRAIN_LIMIT = 100
INTERRUPTED = (
    "The send was cut off before the provider's answer was saved. "
    "It may have gone out; check before sending it again."
)
_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("Times must be timezone aware.")
    return moment


async def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    channel: Channel,
    showing_id: int,
    recipient: str,
    recipient_key: str,
    payload: TextPayload | CalendarPayload,
    now: datetime,
    attendee_id: int | None = None,
    not_before: datetime | None = None,
) -> ShowingNotice:
    """Save a notice in the caller's transaction, or return the one already saved.

    A notice is identified by its kind, showing and recipient key: queuing the
    same one again returns the existing row, whatever else differs, so a retry
    or a later attendee cannot text the same person twice. Nothing is sent and
    nothing is committed here.
    """
    _aware(now)
    if not_before is not None:
        _aware(not_before)
    if not _KIND.fullmatch(kind):
        raise ValueError("A notice kind is lowercase letters, digits and underscores.")
    if not recipient_key.strip():
        raise ValueError("A notice needs a recipient key.")
    if channel == "sms":
        if not isinstance(payload, TextPayload):
            raise ValueError("A text notice carries a TextPayload.")
        address = normalize_us_mobile(recipient)
    else:
        if not isinstance(payload, CalendarPayload):
            raise ValueError("A calendar notice carries a CalendarPayload.")
        address = normalize_email(recipient)
    if address is None:
        raise ValueError(f"Not a valid recipient for a {channel} notice.")

    new_id = await session.scalar(
        insert(ShowingNotice)
        .values(
            kind=kind,
            channel=channel,
            showing_id=showing_id,
            attendee_id=attendee_id,
            recipient=address,
            recipient_key=recipient_key,
            payload=payload.model_dump(mode="json"),
            status="pending",
            not_before=not_before or now,
        )
        .on_conflict_do_nothing(index_elements=["kind", "showing_id", "recipient_key"])
        .returning(ShowingNotice.id)
    )
    if new_id is not None:
        return await session.get_one(ShowingNotice, new_id)
    return await session.scalar(
        select(ShowingNotice).where(
            ShowingNotice.kind == kind,
            ShowingNotice.showing_id == showing_id,
            ShowingNotice.recipient_key == recipient_key,
        )
    )


async def deliver(
    notice_id: int,
    *,
    now: datetime,
    texts: TextSender | None = None,
    calendar: CalendarSource | None = None,
) -> NoticeStatus | None:
    """Deliver one due notice now, such as a confirmation text while the person waits.

    Returns the row's status afterwards, or None when there is no such row.
    A row that is not due, already delivered or being sent by another run
    is left as it is.
    """
    claimed, _ = await _claim(now=_aware(now), limit=1, notice_id=notice_id)
    for item in claimed:
        await _record(item, await _send(item, texts=texts, calendar=calendar), now=now)
    async with AsyncSessionFactory() as session:
        return await session.scalar(
            select(ShowingNotice.status).where(ShowingNotice.id == notice_id)
        )


async def drain(
    *,
    now: datetime,
    texts: TextSender | None = None,
    calendar: CalendarSource | None = None,
    limit: int = DRAIN_LIMIT,
) -> DrainReport:
    """Deliver every notice due by ``now``, oldest first. Safe to run twice at once."""
    claimed, report = await _claim(now=_aware(now), limit=limit)
    for item in claimed:
        outcome = await _send(item, texts=texts, calendar=calendar)
        await _record(item, outcome, now=now)
        if outcome.status == "retry":
            report.retrying += 1
        else:
            setattr(report, outcome.status, getattr(report, outcome.status) + 1)
    if report.total:
        logfire.info("showing notices delivered", **report.model_dump())
    return report


async def refresh_text_status(notice_id: int, *, texts: TextSender | None = None) -> str | None:
    """Read a sent text's status from the provider and save it on the row.

    Returns the status, such as "delivered" or "undelivered", or None for a
    row that is not a sent text.
    """
    async with AsyncSessionFactory() as session:
        notice = await session.get(ShowingNotice, notice_id)
        if notice is None or notice.channel != "sms" or notice.status != "sent":
            return None
        provider_id = notice.provider_id
    if not provider_id:
        return None
    status = await (texts if texts is not None else _live_text_sender()).text_status(provider_id)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id == notice_id)
            .values(provider_status=status)
        )
    return status


# ---------------------------------------------------------------------------
# Claiming, sending and saving the answer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Claimed:
    """A row claimed for sending, with what the send needs."""

    id: int
    kind: str
    channel: str
    recipient: str
    payload: dict
    showing_id: int
    attendee_id: int | None
    attempts: int
    started_at: datetime
    # The event a calendar change applies to when the payload names none.
    stored_event_id: str | None


@dataclass(frozen=True)
class _Outcome:
    status: str  # "sent", "failed", "unknown" or "retry"
    provider_id: str | None = None
    provider_status: str | None = None
    error: str | None = None


def _addresses(notice: ShowingNotice) -> list[str]:
    """Everyone the notice reaches: the number texted, or every guest of an event."""
    if notice.channel == "sms":
        return [notice.recipient]
    return [notice.recipient, *notice.payload.get("attendees", [])]


async def _mark_interrupted(session: AsyncSession, now: datetime) -> int:
    """Rows claimed long ago and never answered become unknown. Their marker is kept,
    so an answer that does arrive late is still saved. Rows another run holds are
    skipped, as in the claim, so two runs never wait on each other."""
    stale = (
        await session.scalars(
            select(ShowingNotice.id)
            .where(
                ShowingNotice.status == "pending",
                ShowingNotice.sending_started_at < now - INTERRUPTED_AFTER,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    if stale:
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id.in_(stale))
            .values(status="unknown", last_error=INTERRUPTED, delivered_at=now)
        )
    return len(stale)


async def _claim(
    *, now: datetime, limit: int, notice_id: int | None = None
) -> tuple[list[_Claimed], DrainReport]:
    """Lock due rows, settle the ones the guard skips or captures, and claim the rest."""
    report = DrainReport()
    claimed: list[_Claimed] = []
    async with AsyncSessionFactory() as session, session.begin():
        report.interrupted = await _mark_interrupted(session, now)
        query = (
            select(
                ShowingNotice,
                Showing.unit,
                Showing.host_event_id,
                ShowingAttendee.calendar_event_id,
            )
            .join(Showing, Showing.id == ShowingNotice.showing_id)
            .outerjoin(ShowingAttendee, ShowingAttendee.id == ShowingNotice.attendee_id)
            .where(
                ShowingNotice.status == "pending",
                ShowingNotice.not_before <= now,
                ShowingNotice.sending_started_at.is_(None),
            )
            .order_by(ShowingNotice.not_before, ShowingNotice.id)
            .limit(limit)
            .with_for_update(of=ShowingNotice, skip_locked=True)
        )
        if notice_id is not None:
            query = query.where(ShowingNotice.id == notice_id)
        rows = (await session.execute(query)).all()
        if not rows:
            return claimed, report

        settings = await settings_for_delivery(session)
        environment = current_environment()
        test_recipients = read_test_recipients()
        for notice, unit, host_event_id, invite_event_id in rows:
            decision = decide(
                settings=settings,
                environment=environment,
                unit=unit,
                addresses=_addresses(notice),
                test_recipients=test_recipients,
            )
            notice.mode = settings.send_mode
            notice.guard_reason = decision.reason
            if decision.action in ("skip", "capture"):
                notice.status = "skipped" if decision.action == "skip" else "captured"
                notice.delivered_at = now
                setattr(report, notice.status, getattr(report, notice.status) + 1)
                continue
            notice.attempts += 1
            notice.sending_started_at = now
            claimed.append(
                _Claimed(
                    id=notice.id,
                    kind=notice.kind,
                    channel=notice.channel,
                    recipient=notice.recipient,
                    payload=dict(notice.payload),
                    showing_id=notice.showing_id,
                    attendee_id=notice.attendee_id,
                    attempts=notice.attempts,
                    started_at=now,
                    stored_event_id=(
                        host_event_id if notice.channel == "calendar_event" else invite_event_id
                    ),
                )
            )
    return claimed, report


def _live_text_sender() -> TextSender:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError("Tests pass a stand-in TextSender; the live one is never used in tests.")
    return QuoTextSender()


def _live_calendar() -> CalendarSource:
    # The Google adapter comes with the calendar connections. Until then a
    # calendar notice the guard would send fails, visibly, instead of waiting.
    raise NotSent("calendar_not_connected", "Calendar sending is not built yet.")


async def _send(
    item: _Claimed, *, texts: TextSender | None, calendar: CalendarSource | None
) -> _Outcome:
    # Chosen outside the try, so a test that forgot its stand-in fails instead
    # of saving a status.
    sender = None
    if item.channel == "sms":
        sender = texts if texts is not None else _live_text_sender()
    try:
        if sender is not None:
            sent = await sender.send_text(
                item.recipient, TextPayload.model_validate(item.payload).body
            )
            return _Outcome(
                "sent", provider_id=sent.provider_id, provider_status=sent.provider_status
            )
        source = calendar if calendar is not None else _live_calendar()
        event_id = await _change_calendar(source, item)
        return _Outcome("sent", provider_id=event_id)
    except NotSent as refused:
        if refused.retryable and item.attempts < MAX_ATTEMPTS:
            return _Outcome("retry", error=refused.detail)
        return _Outcome("failed", provider_status=refused.reason, error=refused.detail)
    except Exception as exc:
        # It may have gone out before the error: never retried.
        return _Outcome("unknown", error=f"{type(exc).__name__}: {exc}"[:500])


async def _change_calendar(source: CalendarSource, item: _Claimed) -> str:
    payload = CalendarPayload.model_validate(item.payload)
    draft = EventDraft(
        summary=payload.summary,
        description=payload.description,
        location=payload.location,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        attendees=payload.attendees,
    )
    if payload.operation == "create":
        return await source.create_event(payload.calendar_id, draft)
    event_id = payload.event_id or item.stored_event_id
    if not event_id:
        raise NotSent("no_event_yet", "The event to change is not created yet.", retryable=True)
    if payload.operation == "update":
        await source.update_event(payload.calendar_id, event_id, draft)
    else:
        await source.cancel_event(payload.calendar_id, event_id)
    return event_id


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(minutes=min(2 ** (attempts - 1), 30))


async def _record(item: _Claimed, outcome: _Outcome, *, now: datetime) -> None:
    """Save the provider's answer on the row this run claimed."""
    if outcome.status == "retry":
        values = {
            "status": "pending",
            "not_before": now + _retry_delay(item.attempts),
            "last_error": outcome.error,
        }
    else:
        values = {
            "status": outcome.status,
            "provider_id": outcome.provider_id,
            "provider_status": outcome.provider_status,
            "last_error": outcome.error,
            "delivered_at": now,
        }
    async with AsyncSessionFactory() as session, session.begin():
        saved = await session.execute(
            update(ShowingNotice)
            .where(
                ShowingNotice.id == item.id,
                ShowingNotice.sending_started_at == item.started_at,
            )
            .values(sending_started_at=None, **values)
        )
        if saved.rowcount == 0:
            logfire.warn("showing notice changed while sending", notice_id=item.id)
            return
        if outcome.status == "sent" and item.payload.get("operation") == "create":
            # Later changes to this event find it here.
            if item.channel == "calendar_event":
                await session.execute(
                    update(Showing)
                    .where(Showing.id == item.showing_id)
                    .values(host_event_id=outcome.provider_id)
                )
            elif item.channel == "calendar_invite" and item.attendee_id is not None:
                await session.execute(
                    update(ShowingAttendee)
                    .where(ShowingAttendee.id == item.attendee_id)
                    .values(calendar_event_id=outcome.provider_id)
                )
    log = logfire.info if outcome.status in ("sent", "retry") else logfire.warn
    log(
        "showing notice {status}",
        status=outcome.status,
        notice_id=item.id,
        kind=item.kind,
        channel=item.channel,
        attempts=item.attempts,
        recipient_suffix=item.recipient[-4:],
        # The reason key or the exception's type; the provider's own text can
        # quote a phone number, so it stays on the row only.
        reason=outcome.provider_status or (outcome.error or "").split(":", 1)[0] or None,
    )
