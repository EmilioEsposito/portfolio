"""Removing prospects' details 12 months after their showing.

The design keeps a prospect's details for 12 months after the showing unless
they apply. Applications live in another system, which keeps an applicant's
details itself, so here everyone's are removed.

``remove_old_details`` takes the showings that started 12 months ago or more
and still hold details, a batch at a time, and in one transaction per batch:

- each attendee row: ``name`` becomes ``REMOVED_NAME``; ``mobile``, ``email``,
  ``user_agent``, ``signature_agent``, ``client_ip_hash``, ``lead_token`` and
  ``texts_consent_page`` are emptied; ``requirement_confirmations`` becomes an
  empty list; ``cancel_token`` becomes a new random value that was never sent
  to anyone, so the old link answers "not found";
- each notice of the showing, whoever it was to (prospects, residents and the
  host): ``recipient`` becomes ``REMOVED_RECIPIENT``, ``payload`` keeps only
  a ``REMOVED`` marker in place of the text, email or event's details (an
  email reminder's relay address, subject, conversation and message ids
  included), and ``last_error``, the one free-form field, which can quote a
  provider's message naming a number or an address, becomes ``REMOVED``.

Everything else stays: the rows, their ids, times, statuses, counts,
attendance, sources, consent times and versions, cancel reasons, and the
notices' kinds, statuses, provider ids and statuses, guard reasons and
delivery times (all structured codes or opaque ids), so the history of
showings is kept. A row counts as cleared only when every one of these fields
is, so a row cleared before a field was added here is taken again.

The cut-off is 12 months before the database's own clock (``now()``). A test
may pass an earlier ``now``, never a later one: the cut-off is 12 months before
whichever is earlier, so no caller can remove a younger showing's details. No
route calls this. ``scheduled_run`` is the daily job ``showing-details-removal``,
registered at startup only when background automation is on. Running it again
finds nothing left to remove.
"""

from dataclasses import dataclass
from datetime import datetime

import logfire
from sqlalchemy import String, and_, cast, exists, func, literal, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.schedulers.job_info import JobInfo
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice

JOB_ID = "showing-details-removal"
KEEP_FOR = "12 months"
BATCH = 100
MAX_BATCHES = 50

REMOVED = "Removed 12 months after the showing."
REMOVED_NAME = "Removed"
REMOVED_RECIPIENT = "removed"
REMOVED_KEY = "removed"
REMOVED_TOKEN_PREFIX = "removed-"


@dataclass
class RemovalReport:
    showings: int = 0
    attendees: int = 0
    notices: int = 0


def _cutoff(now: datetime | None):
    """12 months before the database's clock, or before ``now`` when it is earlier."""
    clock = func.now() if now is None else func.least(literal(now), func.now())
    return clock - text(f"interval '{KEEP_FOR}'")


def _attendee_holds_details():
    return or_(
        ShowingAttendee.name != REMOVED_NAME,
        ShowingAttendee.mobile.is_not(None),
        ShowingAttendee.email.is_not(None),
        ShowingAttendee.user_agent.is_not(None),
        ShowingAttendee.signature_agent.is_not(None),
        ShowingAttendee.client_ip_hash.is_not(None),
        ShowingAttendee.lead_token.is_not(None),
        ShowingAttendee.texts_consent_page.is_not(None),
        ShowingAttendee.requirement_confirmations != text("'[]'::jsonb"),
        ~ShowingAttendee.cancel_token.startswith(REMOVED_TOKEN_PREFIX),
    )


def _notice_holds_details():
    return or_(
        ShowingNotice.recipient != REMOVED_RECIPIENT,
        ~ShowingNotice.payload.has_key(REMOVED_KEY),
        and_(ShowingNotice.last_error.is_not(None), ShowingNotice.last_error != REMOVED),
    )


def _notice_cleared(notice: ShowingNotice) -> bool:
    return (
        notice.recipient == REMOVED_RECIPIENT
        and REMOVED_KEY in (notice.payload or {})
        and notice.last_error in (None, REMOVED)
    )


def _still_holding(now: datetime | None):
    """Showings at or past the cut-off with an attendee or notice not yet cleared."""
    attendee_left = exists().where(
        ShowingAttendee.showing_id == Showing.id, _attendee_holds_details()
    )
    notice_left = exists().where(ShowingNotice.showing_id == Showing.id, _notice_holds_details())
    return (
        select(Showing.id)
        .where(Showing.starts_at <= _cutoff(now), or_(attendee_left, notice_left))
        .order_by(Showing.id)
        .limit(BATCH)
    )


def _removed_payload(channel: str) -> dict:
    if channel in ("calendar_event", "calendar_invite"):
        return {"summary": REMOVED, REMOVED_KEY: True}
    return {"body": REMOVED, REMOVED_KEY: True}


async def _remove_batch(session: AsyncSession, now: datetime | None) -> RemovalReport:
    report = RemovalReport()
    ids = list(await session.scalars(_still_holding(now)))
    if not ids:
        return report
    # The module's lock order: attendees, then notices, then showings, each by id.
    await session.execute(
        select(ShowingAttendee.id)
        .where(ShowingAttendee.showing_id.in_(ids))
        .order_by(ShowingAttendee.id)
        .with_for_update()
    )
    notices = list(
        await session.scalars(
            select(ShowingNotice)
            .where(ShowingNotice.showing_id.in_(ids))
            .order_by(ShowingNotice.id)
            .with_for_update()
        )
    )
    locked = list(
        await session.scalars(
            select(Showing.id)
            .where(Showing.id.in_(ids), Showing.starts_at <= _cutoff(now))
            .order_by(Showing.id)
            .with_for_update()
        )
    )
    attendees = await session.execute(
        update(ShowingAttendee)
        .where(ShowingAttendee.showing_id.in_(locked), _attendee_holds_details())
        .values(
            name=REMOVED_NAME,
            mobile=None,
            email=None,
            user_agent=None,
            signature_agent=None,
            client_ip_hash=None,
            lead_token=None,
            texts_consent_page=None,
            requirement_confirmations=text("'[]'::jsonb"),
            cancel_token=func.concat(REMOVED_TOKEN_PREFIX, cast(func.gen_random_uuid(), String)),
        )
        .execution_options(synchronize_session=False)
    )
    for notice in notices:
        if notice.showing_id in locked and not _notice_cleared(notice):
            notice.recipient = REMOVED_RECIPIENT
            notice.payload = _removed_payload(notice.channel)
            if notice.last_error is not None:
                notice.last_error = REMOVED
            report.notices += 1
    report.showings = len(locked)
    report.attendees = attendees.rowcount or 0
    return report


async def remove_old_details(*, now: datetime | None = None) -> RemovalReport:
    """Remove the details of every showing that started 12 months ago or more.

    In batches of ``BATCH`` showings, one transaction each, up to ``MAX_BATCHES``
    per run; the next run carries on. Safe to run again: a cleared showing is
    not taken again. ``now`` is for tests and can only make the cut-off earlier.
    """
    total = RemovalReport()
    for _ in range(MAX_BATCHES):
        async with AsyncSessionFactory() as session, session.begin():
            batch = await _remove_batch(session, now)
        if not batch.showings:
            break
        total.showings += batch.showings
        total.attendees += batch.attendees
        total.notices += batch.notices
    if total.showings:
        logfire.info(
            "showing details removed",
            showings=total.showings,
            attendees=total.attendees,
            notices=total.notices,
        )
    return total


async def scheduled_run() -> None:
    """The daily job. Uses the database's clock alone."""
    await remove_old_details()


# Shown on the team's Scheduler page (api/src/schedulers/job_info.py).
JOB_INFO = JobInfo(
    name="Showings: remove prospects' details after 12 months",
    summary=(
        "Removes the names, numbers and emails of prospects whose showing started 12 "
        "months ago or more."
    ),
    description=(
        "Finds the showings that started 12 months ago or more, by the database's clock, "
        "that still hold a prospect's details, and removes them. Each attendee's name "
        'becomes "Removed"; their mobile, email, browser details, hashed IP address, '
        "lead link token and requirement ticks are emptied; and their cancel link stops "
        "working. Every text, email, calendar event and invite recorded for the showing, "
        "to prospects, residents or the host, has its recipient and wording replaced by a "
        "note that it was removed."
        "\n\n"
        "The showings, their times, statuses and counts are kept, so the history of "
        "showings stays. It works through 100 showings at a time, up to 50 batches a run, "
        "and the next run carries on; a second run finds nothing left. It sends nothing "
        "and calls no outside service. The removal cannot be undone."
        "\n\n"
        "Paused or removed, prospects' details stay past the 12 months the booking service "
        "keeps them for."
    ),
)


def register_job() -> None:
    """Schedule ``scheduled_run`` daily at 3:30 AM Eastern. Called at startup when
    background automation is on (``api/index.py``)."""
    from api.src.apscheduler_service.service import get_scheduler, upsert_job

    upsert_job(
        get_scheduler(),
        info=JOB_INFO,
        id=JOB_ID,
        func=scheduled_run,
        trigger="cron",
        hour=3,
        minute=30,
        timezone="America/New_York",
        max_instances=1,
        coalesce=True,
    )
