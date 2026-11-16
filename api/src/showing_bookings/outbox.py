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

  1. Claim, just before this row's own provider call. The row is locked
     (``FOR UPDATE SKIP LOCKED``, so two runs at once never take the same
     row), the guard is asked again, ``attempts`` goes up and
     ``sending_started_at`` is set. This is committed before the provider is
     called. A run claims its sends one at a time, so a run that stops partway
     leaves the rows it had not reached pending and unclaimed.
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
fails loudly instead of reaching Quo or Google. The live calendar is the
connected Google account whose showings calendar the notice names
(``connections.calendar_for_showings_calendar``).

A host event notice (``host_event.KINDS``) is rendered again when it is
settled or claimed, under a lock on its showing (``_prepare``): its
summary and description come from the attendees booked now, and it creates
the showing's event when there is none yet, else updates it. While another
host notice for the showing is being sent it waits a minute, so a showing
never gets two events. A showing with no one booked, or cancelled, cancels
the notice, and so does an earlier create whose outcome is unknown. What was
rendered is saved on the row.

A cancellation of the host's event or of a prospect's invite
(``host_event.CANCEL_KINDS``) is checked the same way when it is settled or
claimed: it waits while the event is being created, goes ahead when the event
exists (or its create was captured, so capture mode keeps it too), and is
cancelled when there is no event to cancel. A cancellation never creates one.
The old host's event after a change of host (``host_event.MOVED_KIND``) is
checked the same way, and is applied to the event the notice names, never to
the showing's current one.

A resident's text (``residents.TEXT_KINDS``) is checked too: the new-showing
text is cancelled once the showing is, either is cancelled once the showing
has started, and one due outside 8 AM to 9 PM in its time zone waits for 8 AM.
The cancellation text never overtakes the announcement: it waits a minute while
the announcement is being sent, then goes only if the announcement went out
(sent, captured or unknown), and is cancelled otherwise.
A group text (``TextPayload.also_to``) goes out as one text to every number,
and the guard counts every number in it.
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
from api.src.showing_bookings import connections, google_http, host_event, residents
from api.src.showing_bookings.guard import Decision, decide
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
    EmailPayload,
    NoticeStatus,
    TextPayload,
    normalize_email,
    normalize_us_mobile,
)
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
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
    payload: TextPayload | CalendarPayload | EmailPayload,
    now: datetime,
    attendee_id: int | None = None,
    not_before: datetime | None = None,
    waits_for_notice_id: int | None = None,
) -> ShowingNotice:
    """Save a notice in the caller's transaction, or return the one already saved.

    With ``waits_for_notice_id`` the row is saved ``held``: no run delivers it
    until ``holds.release_held`` makes it pending, once the notice it waits
    for (a prospect's confirmation text) is reported delivered.

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
    elif channel == "email":
        if not isinstance(payload, EmailPayload):
            raise ValueError("An email notice carries an EmailPayload.")
        address = normalize_email(recipient)
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
            # A plain text is saved as {"body": ...}, as before group texts.
            payload=payload.model_dump(
                mode="json", exclude_defaults=isinstance(payload, TextPayload)
            ),
            status="pending" if waits_for_notice_id is None else "held",
            waits_for_notice_id=waits_for_notice_id,
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
    await _deliver_one(
        notice_id, now=_aware(now), texts=texts, calendar=calendar, report=DrainReport()
    )
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
    """Deliver every notice due by ``now``, oldest first. Safe to run twice at once.

    Rows the guard skips or captures are settled together. Rows it would send
    are claimed one at a time, each just before its own provider call.
    """
    to_send, report = await _settle_due(now=_aware(now), limit=limit)
    for notice_id in to_send:
        await _deliver_one(notice_id, now=now, texts=texts, calendar=calendar, report=report)
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
    """Everyone the notice reaches: every number texted (a group text has several),
    the address emailed, or every guest of an event."""
    if notice.channel == "sms":
        return [notice.recipient, *notice.payload.get("also_to", [])]
    if notice.channel == "email":
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


def _due_rows(now: datetime):
    """Due, pending rows that no run is sending, oldest first, locked for the
    transaction. Rows another run has locked are skipped, never waited on."""
    return (
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
        .with_for_update(of=ShowingNotice, skip_locked=True)
    )


_GuardInputs = tuple[ShowingBookingSettings, str, frozenset[str]]


async def _guard_inputs(session: AsyncSession) -> _GuardInputs:
    return await settings_for_delivery(session), current_environment(), read_test_recipients()


def _guard(notice: ShowingNotice, unit: str, inputs: _GuardInputs, now: datetime) -> Decision:
    """Ask the guard about one locked row, and settle it when the answer is skip or capture."""
    settings, environment, test_recipients = inputs
    decision = decide(
        settings=settings,
        environment=environment,
        unit=unit,
        addresses=_addresses(notice),
        test_recipients=test_recipients,
    )
    if decision.action != "send":
        notice.mode = settings.send_mode
        notice.guard_reason = decision.reason
        notice.status = "skipped" if decision.action == "skip" else "captured"
        notice.delivered_at = now
    return decision


def _count(report: DrainReport, status: str) -> None:
    field = "retrying" if status == "retry" else status
    setattr(report, field, getattr(report, field) + 1)


_NOT_NOW = object()
HOST_EVENT_BUSY_DELAY = timedelta(minutes=1)


def _settle_cancel_check(notice: ShowingNotice, verdict: str, now: datetime) -> bool:
    """Apply ``host_event.cancel_check``'s answer; True when the row goes ahead."""
    if verdict == "go":
        return True
    if verdict == "wait":
        notice.not_before = now + HOST_EVENT_BUSY_DELAY
    else:
        notice.status = "cancelled"
        notice.last_error = verdict
    return False


async def _prepare(session: AsyncSession, notice: ShowingNotice, now: datetime):
    """Get a notice ready to deliver, from things as they are now.

    A notice for one booking (its confirmation text or its invite,
    ``host_event.FOR_A_BOOKING_KINDS``) is cancelled when the attendee is no
    longer booked or the showing no longer scheduled. That covers a retry: a
    send that was under way when the booking was cancelled, then failed and
    was put back to pending, never goes out later.

    A host event notice is rendered from its showing under the showing's
    lock; a cancellation (the host's event or a prospect's invite) is checked
    for an event to cancel (``host_event.cancel_check``).

    Returns ``_NOT_NOW`` when the row is not delivered in this run: cancelled
    (no one booked, an earlier create may have gone out, or there is no event
    to cancel) or put off a minute (another notice for the event is being
    sent). Otherwise the event id it applies to, or None when there is none
    yet, so it is created. Other rows are returned unchanged with None.
    """
    if notice.kind in residents.TEXT_KINDS:
        return await _prepare_resident_text(session, notice, now)
    if notice.kind in host_event.FOR_A_BOOKING_KINDS:
        row = (
            await session.execute(
                select(ShowingAttendee.status, Showing.status)
                .join(Showing, Showing.id == ShowingAttendee.showing_id)
                .where(ShowingAttendee.id == notice.attendee_id)
            )
        ).first()
        if row is not None and (row[0] != "booked" or row[1] != "scheduled"):
            notice.status = "cancelled"
            notice.last_error = host_event.BOOKING_CANCELLED
            return _NOT_NOW
        return None
    if notice.kind == host_event.INVITE_CANCEL_KIND:
        event_id = await session.scalar(
            select(ShowingAttendee.calendar_event_id).where(
                ShowingAttendee.id == notice.attendee_id
            )
        )
        verdict = await host_event.cancel_check(session, notice, event_id=event_id)
        return event_id if _settle_cancel_check(notice, verdict, now) else _NOT_NOW
    if notice.kind not in host_event.SHOWING_LOCKED_KINDS:
        return None
    showing = await session.scalar(
        select(Showing).where(Showing.id == notice.showing_id).with_for_update()
    )
    others = select(ShowingNotice.id).where(
        ShowingNotice.showing_id == notice.showing_id,
        ShowingNotice.kind.in_(host_event.SHOWING_LOCKED_KINDS),
        ShowingNotice.id != notice.id,
    )
    if await session.scalar(
        others.where(
            ShowingNotice.status == "pending", ShowingNotice.sending_started_at.is_not(None)
        ).limit(1)
    ):
        notice.not_before = now + HOST_EVENT_BUSY_DELAY
        return _NOT_NOW
    if notice.kind == host_event.CANCEL_KIND:
        verdict = await host_event.cancel_check(session, notice, event_id=showing.host_event_id)
        return showing.host_event_id if _settle_cancel_check(notice, verdict, now) else _NOT_NOW
    if notice.kind == host_event.MOVED_KIND:
        # The old host's event, named on the notice: never the showing's current one.
        verdict = await host_event.moved_check(session, notice)
        return (
            notice.payload.get("event_id")
            if _settle_cancel_check(notice, verdict, now)
            else _NOT_NOW
        )
    rendered = await host_event.render(
        session, showing, calendar_id=notice.payload.get("calendar_id") or "unknown"
    )
    note = host_event.NO_ONE_BOOKED if rendered is None else None
    if rendered is not None and rendered.operation == "create":
        unsure = await session.scalar(
            others.where(
                ShowingNotice.status == "unknown",
                ShowingNotice.provider_id.is_(None),
                host_event.since_last_move(notice.showing_id),
            ).limit(1)
        )
        if unsure:
            note = host_event.EARLIER_UNKNOWN
    if note is not None:
        notice.status = "cancelled"
        notice.last_error = note
        return _NOT_NOW
    notice.payload = rendered.model_dump(mode="json")
    return showing.host_event_id


async def _prepare_resident_text(session: AsyncSession, notice: ShowingNotice, now: datetime):
    """A resident's text goes only while its showing is ahead, and only in the daytime."""
    status, starts_at = (
        await session.execute(
            select(Showing.status, Showing.starts_at).where(Showing.id == notice.showing_id)
        )
    ).one()
    note = None
    if notice.kind == residents.NEW_SHOWING_KIND and status != "scheduled":
        note = residents.SHOWING_CANCELLED_NOTE
    elif starts_at <= now:
        note = residents.TOO_LATE_NOTE
    if note is None and notice.kind == residents.CANCELLED_KIND:
        # Never ahead of the announcement: wait while it is being sent, then go
        # only if it went out (or may have).
        told = (
            await session.execute(
                select(ShowingNotice.status, ShowingNotice.sending_started_at).where(
                    ShowingNotice.showing_id == notice.showing_id,
                    ShowingNotice.kind == residents.NEW_SHOWING_KIND,
                )
            )
        ).first()
        if told is not None and told[0] == "pending" and told[1] is not None:
            notice.not_before = now + HOST_EVENT_BUSY_DELAY
            return _NOT_NOW
        if told is None or told[0] not in ("sent", "captured", "unknown"):
            note = residents.NEVER_TOLD_NOTE
    if note is not None:
        notice.status = "cancelled"
        notice.last_error = note
        return _NOT_NOW
    timezone = notice.payload.get("daytime_timezone")
    later = residents.next_daytime(now, timezone) if timezone else None
    if later is not None:
        notice.not_before = later
        return _NOT_NOW
    return None


async def _settle_due(*, now: datetime, limit: int) -> tuple[list[int], DrainReport]:
    """Settle the due rows the guard skips or captures, and list the ones it would send.

    The rows to send are not claimed here: each is claimed just before its own
    provider call, so a run that stops partway leaves the rest pending.
    """
    report = DrainReport()
    to_send: list[int] = []
    async with AsyncSessionFactory() as session, session.begin():
        report.interrupted = await _mark_interrupted(session, now)
        rows = (await session.execute(_due_rows(now).limit(limit))).all()
        if rows:
            # The showings of the host notices, locked together in id order, so
            # two runs at once never take them in opposite orders.
            host_showings = sorted(
                {
                    notice.showing_id
                    for notice, *_ in rows
                    if notice.kind in host_event.SHOWING_LOCKED_KINDS
                }
            )
            if host_showings:
                await session.execute(
                    select(Showing.id)
                    .where(Showing.id.in_(host_showings))
                    .order_by(Showing.id)
                    .with_for_update()
                )
            inputs = await _guard_inputs(session)
            for notice, unit, _host_event_id, _invite_event_id in rows:
                if await _prepare(session, notice, now) is _NOT_NOW:
                    continue
                if _guard(notice, unit, inputs, now).action == "send":
                    to_send.append(notice.id)
                else:
                    _count(report, notice.status)
    return to_send, report


async def _claim_one(notice_id: int, *, now: datetime) -> tuple[_Claimed | None, str | None]:
    """Claim one due row for sending, asking the guard again, and commit the claim.

    Returns the claimed row; or, when the guard now skips or captures it, the
    status it was settled with; or neither, when the row is not due, already
    delivered, or held by another run.
    """
    async with AsyncSessionFactory() as session, session.begin():
        row = (await session.execute(_due_rows(now).where(ShowingNotice.id == notice_id))).first()
        if row is None:
            return None, None
        notice, unit, host_event_id, invite_event_id = row
        prepared = await _prepare(session, notice, now)
        if prepared is _NOT_NOW:
            return None, None
        if notice.kind == host_event.MOVED_KIND:
            host_event_id = prepared
        elif prepared is not None and notice.channel == "calendar_event":
            host_event_id = prepared
        elif prepared is not None:
            invite_event_id = prepared
        inputs = await _guard_inputs(session)
        decision = _guard(notice, unit, inputs, now)
        if decision.action != "send":
            return None, notice.status
        notice.mode = inputs[0].send_mode
        notice.guard_reason = decision.reason
        notice.attempts += 1
        notice.sending_started_at = now
        return (
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
                stored_event_id={
                    "calendar_event": host_event_id,
                    "calendar_invite": invite_event_id,
                }.get(notice.channel),
            ),
            None,
        )


async def _deliver_one(
    notice_id: int,
    *,
    now: datetime,
    texts: TextSender | None,
    calendar: CalendarSource | None,
    report: DrainReport,
) -> None:
    """Claim one row, call its provider, save the answer, and count what happened."""
    item, settled = await _claim_one(notice_id, now=now)
    if settled is not None:
        _count(report, settled)
    if item is None:
        return
    outcome = await _send(item, texts=texts, calendar=calendar)
    await _record(item, outcome, now=now)
    _count(report, outcome.status)


def _live_text_sender() -> TextSender:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError("Tests pass a stand-in TextSender; the live one is never used in tests.")
    return QuoTextSender()


async def _live_calendar(calendar_id: str) -> CalendarSource:
    """The connected Google account that holds ``calendar_id``, its showings calendar.

    Raises ``NotSent`` when no connection holds it. A connection that cannot be
    used here (lost, revoked, or no token key, as in a preview) refuses the
    change the same way, so the notice fails visibly instead of waiting.
    """
    return await connections.calendar_for_showings_calendar(calendar_id)


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
            text = TextPayload.model_validate(item.payload)
            if text.also_to:
                sent = await sender.send_group_text([item.recipient, *text.also_to], text.body)
            else:
                sent = await sender.send_text(item.recipient, text.body)
            return _Outcome(
                "sent", provider_id=sent.provider_id, provider_status=sent.provider_status
            )
        if item.channel == "email":
            # Captured like any other notice; one the guard would send cannot go yet.
            raise NotSent("no_email_sender", "There is no email sender yet.")
        if calendar is not None:
            source = calendar
        else:
            source = await _live_calendar(CalendarPayload.model_validate(item.payload).calendar_id)
        event_id = await _change_calendar(source, item)
        return _Outcome("sent", provider_id=event_id)
    except NotSent as refused:
        if refused.retryable and item.attempts < MAX_ATTEMPTS:
            return _Outcome("retry", error=refused.detail)
        return _Outcome("failed", provider_status=refused.reason, error=refused.detail)
    except google_http.LiveGoogleInTests:
        # A test without its mock transport fails instead of saving a status.
        raise
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
    saves_invite_id = (
        outcome.status == "sent"
        and item.payload.get("operation") == "create"
        and item.channel == "calendar_invite"
        and item.attendee_id is not None
    )
    async with AsyncSessionFactory() as session, session.begin():
        # Lock order across the module: the attendee, then its notices, then
        # the showing. An invite's id is saved on the attendee, so that row is
        # locked before the notice is updated.
        if saves_invite_id:
            await session.execute(
                select(ShowingAttendee.id)
                .where(ShowingAttendee.id == item.attendee_id)
                .with_for_update()
            )
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
