"""Held notices: what waits for a prospect's confirmation text.

A booking's host event (or its update, when someone joins) and the
prospect's confirmation email are saved ``held``, each naming the confirmation text
it waits for (``waits_for_notice_id``). ``release_held`` decides, for each
confirmation that holds something:

- reported ``delivered`` (read from Quo through ``outbox.refresh_text_status``):
  release them, making them pending and due now;
- reported ``undelivered`` or ``failed``: stop them (``cancelled``) and mark
  the booking for the team (``attention_reason``);
- neither, five minutes after the text was sent: release them, because Quo
  does not promise a delivery report for every text;
- captured (there is no delivery report), or skipped (an old row): release them at
  once, so each is captured in turn;
- refused or otherwise not sent (``failed``), or itself cancelled: stop them
  and mark the booking. A booking whose number Quo refuses while the person
  waits is withdrawn by ``bookings.py`` instead, which stops them at once;
- still pending, or unknown for less than five minutes: wait.

``send_pending`` runs ``release_held``, ``reminders.queue_due``,
``daily_texts.queue_due`` (the host's morning text and the apply text),
``outbox.drain`` and ``alerts.check``, in that order: the job every minute
(``scheduled_run``, registered by ``register_job`` only when background
automation is on), and outside production, where that job is off, the team's
button on the tab. Each step runs
on its own: one that raises is logged at error level with the step's name and
the rest still run, so held notices are always released and due notices always
delivered. With nothing
held, due, failed, coming up in the next two days or owed an apply text, the
job returns after one query.

A captured notice a person sends by hand (``captured.py``) whose confirmation
text goes too is held again, and waits for it by the same rules.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import logfire
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.src.database.database import AsyncSessionFactory
from api.src.schedulers.job_info import JobInfo
from api.src.showing_bookings import delivery_checks, outbox, resident_notices
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.providers import (
    CalendarSource,
    EmailSender,
    TeamAlerter,
    TextSender,
)
from api.src.showing_bookings.schema import DrainReport, HoldReport

JOB_ID = "showing-notices"
T = TypeVar("T")
REPORT_WAIT = timedelta(minutes=5)
DELIVERED = {"delivered"}
UNDELIVERED = {"undelivered", "failed"}
NUMBER_REFUSALS = delivery_checks.NUMBER_REFUSALS
HOLDERS_PER_RUN = 100

UNDELIVERED_NOTE = "Not sent: the confirmation text was reported undelivered."
NOT_SENT_NOTE = "Not sent: the confirmation text was not sent."


class SendPendingReport(DrainReport):
    """One run of ``send_pending``: the held notices released or stopped, the
    reminders, morning texts and apply texts queued, the drain, and the team alerts."""

    released: int = 0
    stopped: int = 0
    prospect_reminders: int = 0
    resident_reminders: int = 0
    lead_email_reminders: int = 0
    host_mornings: int = 0
    apply_texts: int = 0
    alerts: int = 0


async def stop_waiting_on(
    session: AsyncSession, holder_id: int, *, note: str, include_released: bool = False
) -> int:
    """Cancel the notices held by ``holder_id`` in the caller's transaction.

    ``include_released`` also cancels those already released but not yet
    delivered (pending), as a withdrawn booking needs.
    """
    statuses = ["held", "pending"] if include_released else ["held"]
    result = await session.execute(
        update(ShowingNotice)
        .where(
            ShowingNotice.waits_for_notice_id == holder_id,
            ShowingNotice.status.in_(statuses),
            ShowingNotice.sending_started_at.is_(None),
        )
        .values(status="cancelled", last_error=note)
    )
    return result.rowcount or 0


async def _release(session: AsyncSession, holder_id: int | None, now: datetime) -> int:
    condition = (
        ShowingNotice.waits_for_notice_id.is_(None)
        if holder_id is None
        else ShowingNotice.waits_for_notice_id == holder_id
    )
    result = await session.execute(
        update(ShowingNotice)
        .where(condition, ShowingNotice.status == "held")
        .values(status="pending", not_before=now)
    )
    return result.rowcount or 0


async def _stop(holder: ShowingNotice, *, reason: str, note: str) -> int:
    async with AsyncSessionFactory() as session, session.begin():
        # Lock order, as everywhere in this module: the attendee, then its
        # notices, then the showing. A correction or a withdrawal locks the
        # attendee first too, so neither can deadlock against this.
        if holder.attendee_id is not None:
            await session.execute(
                select(ShowingAttendee.id)
                .where(ShowingAttendee.id == holder.attendee_id)
                .with_for_update()
            )
        # The residents' text waits for someone else's confirmation instead,
        # when someone else is booked; otherwise it is stopped with the rest.
        await resident_notices.hand_over(session, holder.id, holder.attendee_id)
        stopped = await stop_waiting_on(session, holder.id, note=note)
        if holder.attendee_id is not None:
            await session.execute(
                update(ShowingAttendee)
                .where(ShowingAttendee.id == holder.attendee_id, ShowingAttendee.status == "booked")
                .values(attention_reason=reason)
            )
    logfire.warn(
        "showing booking needs the team",
        reason=reason,
        notice_id=holder.id,
        attendee_id=holder.attendee_id,
        stopped=stopped,
    )
    return stopped


def _waited(holder: ShowingNotice, now: datetime) -> bool:
    started = holder.delivered_at or holder.created_at
    return started is not None and now - started >= REPORT_WAIT


async def _decide(holder: ShowingNotice, now: datetime, texts: TextSender | None) -> str:
    """ "release", "stop:<reason>" or "wait" for the notices ``holder`` holds."""
    if holder.status in ("captured", "skipped"):
        return "release"
    if holder.status == "sent":
        status = holder.provider_status
        if status not in DELIVERED | UNDELIVERED:
            try:
                status = await outbox.refresh_text_status(holder.id, texts=texts)
            except Exception as exc:
                logfire.warn(
                    "showing confirmation status could not be read",
                    notice_id=holder.id,
                    error=type(exc).__name__,
                )
        if status in DELIVERED:
            return "release"
        if status in UNDELIVERED:
            return "stop:confirmation_undelivered"
        return "release" if _waited(holder, now) else "wait"
    if holder.status == "unknown":
        return "release" if _waited(holder, now) else "wait"
    if holder.status == "failed":
        refused = holder.provider_status in NUMBER_REFUSALS
        return "stop:confirmation_refused" if refused else "stop:confirmation_not_sent"
    if holder.status == "cancelled":
        return "stop:confirmation_not_sent"
    return "wait"


async def release_held(*, now: datetime, texts: TextSender | None = None) -> HoldReport:
    """Release or stop the held notices whose confirmation text has an answer. Takes ``now``.

    A text not sent yet has none, and may wait for days (a place whose reply is
    not settled, ``delivery_checks.reply_unsettled``): it is left out before the
    limit, so those never fill a run.
    """
    report = HoldReport()
    confirmation = aliased(ShowingNotice)
    async with AsyncSessionFactory() as session, session.begin():
        holder_ids = list(
            await session.scalars(
                select(ShowingNotice.waits_for_notice_id)
                .outerjoin(confirmation, confirmation.id == ShowingNotice.waits_for_notice_id)
                .where(
                    ShowingNotice.status == "held",
                    confirmation.status.is_distinct_from("pending"),
                )
                .distinct()
                .limit(HOLDERS_PER_RUN)
            )
        )
        if not holder_ids:
            return report
        if None in holder_ids:
            # The notice it waited for is gone; nothing is left to wait for.
            report.released += await _release(session, None, now)
        holders = list(
            await session.scalars(
                select(ShowingNotice).where(
                    ShowingNotice.id.in_([i for i in holder_ids if i is not None])
                )
            )
        )
        session.expunge_all()
    for holder in holders:
        decision = await _decide(holder, now, texts)
        if decision == "release":
            async with AsyncSessionFactory() as session, session.begin():
                report.released += await _release(session, holder.id, now)
        elif decision.startswith("stop:"):
            reason = decision.removeprefix("stop:")
            note = UNDELIVERED_NOTE if reason == "confirmation_undelivered" else NOT_SENT_NOTE
            report.stopped += await _stop(holder, reason=reason, note=note)
    if report.released or report.stopped:
        logfire.info("showing held notices", **report.model_dump())
    return report


# Showings this close may be owed a reminder (``reminders.LOOKAHEAD``).
REMINDER_LOOKAHEAD = timedelta(days=2)
# Failed notices this recent may be owed an alert (``alerts.LOOKBACK``).
ALERT_LOOKBACK = timedelta(days=2)
# Showings that started in this window may owe an apply text.
APPLY_AFTER = delivery_checks.APPLY_AFTER
APPLY_LOOKBACK = delivery_checks.APPLY_LOOKBACK


async def anything_to_do(now: datetime) -> bool:
    """Whether any step of the job may have work: a notice held, or pending and
    due; a scheduled showing in the next two days, which may be owed a reminder
    or a morning text; a person who may be owed an apply text; or a notice that
    failed in the last two days and has not been alerted about. One query."""
    apply_sent = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == delivery_checks.APPLY_KIND,
        ShowingNotice.attendee_id.is_not(None),
    )
    async with AsyncSessionFactory() as session:
        return bool(
            await session.scalar(
                select(
                    or_(
                        exists().where(
                            or_(
                                ShowingNotice.status == "held",
                                (ShowingNotice.status == "pending")
                                & (ShowingNotice.not_before <= now),
                                ShowingNotice.status.in_(["failed", "unknown"])
                                & ShowingNotice.alerted_at.is_(None)
                                & (ShowingNotice.delivered_at >= now - ALERT_LOOKBACK),
                            )
                        ),
                        exists().where(
                            Showing.status == "scheduled",
                            Showing.starts_at > now,
                            Showing.starts_at <= now + REMINDER_LOOKAHEAD,
                        ),
                        exists().where(
                            Showing.id == ShowingAttendee.showing_id,
                            Showing.status != "cancelled",
                            Showing.starts_at <= now - APPLY_AFTER,
                            Showing.starts_at > now - APPLY_LOOKBACK,
                            ShowingAttendee.status == "booked",
                            ShowingAttendee.attendance != "no_show",
                            ShowingAttendee.texts_accepted.is_(True),
                            ShowingAttendee.mobile.is_not(None),
                            ShowingAttendee.id.not_in(apply_sent),
                        ),
                    )
                )
            )
        )


async def _step(name: str, run: Callable[[], Awaitable[T]], fallback: T) -> T:
    """Run one step of the job. A step that raises is logged at error level, naming
    the step, and counts as having done nothing, so the steps after it still run."""
    try:
        return await run()
    except Exception:
        logfire.exception("showing notices job step failed: {step}", step=name)
        return fallback


async def send_pending(
    *,
    now: datetime,
    texts: TextSender | None = None,
    calendar: CalendarSource | None = None,
    alerter: TeamAlerter | None = None,
    emails: EmailSender | None = None,
) -> SendPendingReport:
    """The job's steps in order: release or stop held notices, queue the reminders
    (by text and by email), morning texts and apply texts due by ``now``, deliver
    every due notice, then alert the team about any notice that failed or is stuck.

    Each step runs on its own: one that raises is logged with the step's name and
    the others still run, so releasing held notices and the drain are always
    attempted."""
    # Imported here: they import modules that import this one.
    from api.src.showing_bookings import alerts, daily_texts, reminders

    held = await _step("release_held", lambda: release_held(now=now, texts=texts), HoldReport())
    queued = await _step(
        "reminders", lambda: reminders.queue_due(now=now), reminders.ReminderReport()
    )
    daily = await _step(
        "daily_texts", lambda: daily_texts.queue_due(now=now), daily_texts.DailyTextReport()
    )
    drained = await _step(
        "drain",
        lambda: outbox.drain(now=now, texts=texts, calendar=calendar, emails=emails),
        DrainReport(),
    )
    alerted = await _step("alerts", lambda: alerts.check(now=now, alerter=alerter), 0)
    return SendPendingReport(
        **drained.model_dump(),
        **held.model_dump(),
        **queued.model_dump(),
        **daily.model_dump(),
        alerts=alerted,
    )


async def scheduled_run() -> None:
    """The job every minute. Returns after one query when no step has anything to do."""
    now = datetime.now(UTC)
    if not await anything_to_do(now):
        return
    await send_pending(now=now)


# Shown on the team's Scheduler page (api/src/schedulers/job_info.py).
JOB_INFO = JobInfo(
    name="Showings: send due texts, emails and alerts",
    summary=(
        "Sends the showing texts, emails, calendar events and reminders that are due, and alerts "
        "the team about any that failed."
    ),
    description=(
        "Runs the showing booking service's delivery steps in order. It releases a "
        "booking's held host event and prospect's confirmation email once the prospect's confirmation "
        "text is reported delivered (or five minutes after it went, without a report), and "
        "stops them and marks the booking for the team when the text was not delivered. It "
        "queues the reminders due the evening before a showing, the host's morning text, "
        "and the text with the link to apply that goes two hours after a showing starts. "
        "It delivers every notice that is due: texts through Quo from the team line, "
        "the host's calendar events through Google Calendar, and the prospect's emails and "
        "a Zillow lead's reminder by email through Gmail. Last, in production, it sends the team one web push for "
        "each notice that failed or is stuck."
        "\n\n"
        "Who can receive anything follows the send mode on the Booking settings tab: in "
        "capture mode notices are only recorded for the team to see, and outside "
        "production only the test list can be reached. With nothing held, due, recently "
        "failed, coming up in the next two days or owed an apply text, a run ends after "
        "one database query."
        "\n\n"
        "Paused or removed, held events and emails stay held, reminders, morning texts and apply "
        "texts are not sent, what a request left unfinished stays unfinished, and the team "
        "is not alerted about failures."
    ),
)


def register_job() -> None:
    """Schedule ``scheduled_run`` every minute. Called at startup when background
    automation is on (``api/index.py``)."""
    from api.src.apscheduler_service.service import get_scheduler, upsert_job

    upsert_job(
        get_scheduler(),
        info=JOB_INFO,
        id=JOB_ID,
        func=scheduled_run,
        trigger="interval",
        seconds=60,
        max_instances=1,
        coalesce=True,
    )
