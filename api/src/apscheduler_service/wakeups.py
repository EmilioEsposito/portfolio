"""Wake-up jobs: a queue's work runs when it is due, not on a polling interval.

A queue here is a feature's own table of work with a due time on each row
(``zillow_email_jobs.next_attempt_at``, for one). The table is the record of
what is waiting and when; nothing about the work is kept anywhere else.

Each queue has at most one **wake-up job** in APScheduler, under the queue's
job id. It is a one-time ``date`` job that carries no data: it only runs the
queue's own function at the time the earliest work in the table is due. That
function takes what is due from the table, exactly as it did when it ran on an
interval.

The wake-up is moved from the table:

- after every enqueue (``wake``), once the new row is committed;
- at the end of every run (``rearm``), so a retry, or work that arrived during
  the run, gets its own wake-up; with nothing left it is removed and the queue
  is idle. While a run is in progress, ``wake`` leaves the queue alone: the end
  of the run sees the new work. When that write fails, or APScheduler skips a
  wake-up because the last run was still finishing (and deletes it), ``wake``
  is tried again in memory until it works;
- 30 seconds after each startup (``register_queue``), which also replaces a job
  left under the same id by older code;
- by the catch-up job (``catch_up``) 2 minutes after each startup and every 15
  minutes after that, for a wake-up that was lost: a database blip between a
  commit and the job store write, a code path that adds work and forgets to
  call ``wake``, or a deploy, when the old server shares the job store.

``misfire_grace_time=None`` makes a wake-up that came due while no server was
running (a deploy) run as soon as one is. Without it APScheduler drops a
one-time job that is more than a minute late.

Why APScheduler, and not an in-process timer: its job store already keeps
jobs in the database, so a wake-up survives a restart with no code of ours,
and the Scheduler page shows when each queue runs next.

Work that should happen at once (an escalation's first attempt) stays in the
request's ``BackgroundTasks``; a wake-up covers what is due later.

This module imports the scheduler only inside functions, so a queue's module
can import it at the top without pulling in the scheduler's dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import logfire
from apscheduler.events import EVENT_JOB_MAX_INSTANCES, JobSubmissionEvent
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.date import DateTrigger

from api.src.apscheduler_service.tracing import scheduled_job
from api.src.schedulers.job_info import JobInfo, remember

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

CATCH_UP_JOB_ID = "wake-up-catch-up"
CATCH_UP_EVERY = timedelta(minutes=15)
# The catch-up's first run after the API starts. During a deploy the old server
# shares the job store for a while: it can start a wake-up without setting the
# next one (older code), and wake-ups it sets are not seen by this server's
# timer until its next run of any job. This run, once the old server is gone,
# sets every queue's wake-up from its table again.
CATCH_UP_AFTER_STARTUP = timedelta(minutes=2)
# A queue's first run after the API starts, once startup has settled.
STARTUP_DELAY = timedelta(seconds=30)
# The soonest a queue runs again after a run ends. Work that is due at once
# (more than one run's worth) waits this long; it also bounds how often a queue
# whose due time is wrong could run.
RERUN_GAP = timedelta(seconds=5)
# The same after a run that raised, so a failure that repeats (the database
# down) does not rerun every few seconds.
AFTER_ERROR = timedelta(seconds=60)
# A wake-up within this of the wanted time is left alone, not written again.
SAME_TIME = timedelta(seconds=1)


@dataclass(frozen=True)
class WakeQueue:
    """A queue with a wake-up job.

    job_id: the APScheduler job id of the wake-up, also the queue's name here.
    info: what the Scheduler page shows for it.
    run: the wake-up job's function. Decorate it with ``@scheduled_job`` and
        wrap its body in ``async with rearm(job_id)``. Its import path is
        stored with the job, so it must not move while a wake-up is stored.
    next_due_at: when the queue next has work, from its table; None when it
        has none. One small query: it runs after every enqueue and every run.
    """

    job_id: str
    info: JobInfo
    run: Callable[[], Awaitable[None]]
    next_due_at: Callable[[], Awaitable[datetime | None]]


_QUEUES: dict[str, WakeQueue] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
# Queues with a run in progress in this process (inside ``rearm``).
_RUNNING: set[str] = set()
# Queues with a retry of ``wake`` already timed in this process, and the tasks
# those retries start (kept so they are not garbage-collected mid-run).
_RETRYING: set[str] = set()
_RETRY_TASKS: set[asyncio.Task] = set()
_listening_to: object | None = None
_catch_up_registered = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _scheduler() -> AsyncIOScheduler:
    from api.src.apscheduler_service.service import get_scheduler

    return get_scheduler()


def queues() -> list[WakeQueue]:
    """The queues registered in this process, in registration order."""
    return list(_QUEUES.values())


def get_queue(job_id: str) -> WakeQueue | None:
    return _QUEUES.get(job_id)


def _put(scheduler: AsyncIOScheduler, queue: WakeQueue, run_at: datetime) -> None:
    """Set the queue's wake-up for ``run_at``, replacing whatever has its id."""
    from api.src.apscheduler_service.service import upsert_job

    upsert_job(
        scheduler,
        info=queue.info,
        id=queue.job_id,
        func=queue.run,
        trigger="date",
        run_date=run_at,
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
    )


def register_queue(queue: WakeQueue) -> None:
    """Register a queue at startup, before the scheduler starts.

    Its wake-up is set for shortly after startup, which runs whatever came due
    while the API was down and then sets the next wake-up from the table. It
    replaces any job stored under the same id, including the interval job a
    queue had before it moved to wake-ups.
    """
    _QUEUES[queue.job_id] = queue
    remember(queue.job_id, queue.info)
    scheduler = _scheduler()
    _listen_for_skips(scheduler)
    _put(scheduler, queue, _utcnow() + STARTUP_DELAY)


def _listen_for_skips(scheduler: AsyncIOScheduler) -> None:
    """Set a queue's wake-up again when APScheduler skips it.

    APScheduler runs one instance of a job at a time. A wake-up that comes due
    while its queue's previous run is still finishing (the run sets the next
    wake-up just before it returns) is skipped, and a one-time job that is
    skipped is deleted. Without this the queue would wait for the catch-up job.
    """
    global _listening_to
    if _listening_to is scheduler:
        return
    scheduler.add_listener(_on_skipped, EVENT_JOB_MAX_INSTANCES)
    _listening_to = scheduler


def _on_skipped(event: JobSubmissionEvent) -> None:
    if event.job_id in _QUEUES:
        logfire.info(
            "wake-up: {job_id} came due while its last run was finishing; setting it again",
            job_id=event.job_id,
        )
        _retry_later(event.job_id, after=RERUN_GAP)


def _retry_later(job_id: str, *, after: timedelta) -> None:
    """Call ``wake`` for the queue again after ``after``, in this process.

    For when the wake-up may be gone and the job store cannot be relied on to
    bring it back: ``wake`` failed (the database, most likely), or APScheduler
    skipped and deleted it. Kept in memory, so it does not need the database
    that just failed; if it fails again it times another retry. One retry per
    queue at a time.
    """
    if job_id in _RETRYING:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop here (a test calling synchronously)
    _RETRYING.add(job_id)

    def fire() -> None:
        _RETRYING.discard(job_id)
        task = loop.create_task(wake(job_id, min_delay=RERUN_GAP))
        _RETRY_TASKS.add(task)
        task.add_done_callback(_RETRY_TASKS.discard)

    loop.call_later(after.total_seconds(), fire)


def _place(
    scheduler: AsyncIOScheduler,
    queue: WakeQueue,
    due: datetime | None,
    not_before: datetime | None,
    min_delay: timedelta | None,
) -> None:
    """Make the queue's wake-up match ``due``: removed when None, else set for it.

    ``min_delay`` is counted from now, at the write: a wake-up set at the end of
    a run must not come due before the run has returned, however long the read
    before it took."""
    current = scheduler.get_job(queue.job_id)
    if due is None:
        if current is not None:
            try:
                scheduler.remove_job(queue.job_id)
            except JobLookupError:
                pass  # it started running in the meantime
            logfire.info("wake-up: {job_id} has nothing waiting", job_id=queue.job_id)
        return
    now = _utcnow()
    run_at = max(due, now + (min_delay or timedelta(0)), not_before or now)
    if (
        current is not None
        and isinstance(current.trigger, DateTrigger)
        and current.next_run_time is not None
        and abs(current.next_run_time - run_at) < SAME_TIME
    ):
        return
    _put(scheduler, queue, run_at)
    logfire.info("wake-up: {job_id} set for {run_at}", job_id=queue.job_id, run_at=run_at)


async def wake(
    job_id: str,
    *,
    not_before: datetime | None = None,
    min_delay: timedelta | None = None,
) -> None:
    """Move the queue's wake-up to its earliest due work, read from its table.

    Call it after committing new work. ``not_before`` keeps the wake-up from
    being earlier than that time; ``min_delay`` from being sooner than that
    from the moment it is written. Never raises: the work is already saved.
    When reading the table or writing the job fails, the same call is tried
    again in ``AFTER_ERROR``, in this process, until it works. Does nothing for
    a queue not registered here or while the scheduler is not running
    (background automation off, or startup still under way: the startup run
    covers it).
    """
    queue = _QUEUES.get(job_id)
    if queue is None:
        return
    if job_id in _RUNNING:
        # The run in progress sets the wake-up from the table when it ends, and
        # sees this work then. A wake-up set now could come due mid-run, and
        # APScheduler would skip it with a warning (one run per queue at a time).
        return
    scheduler = _scheduler()
    if not scheduler.running:
        return
    # Read and write as one step in this process: otherwise a run ending as an
    # email arrives could read "nothing waiting", lose the race and remove the
    # wake-up the email had just set.
    lock = _LOCKS.setdefault(job_id, asyncio.Lock())
    async with lock:
        try:
            due = await queue.next_due_at()
            _place(scheduler, queue, due, not_before, min_delay)
        except Exception:
            # A run's own wake-up is deleted when the run starts, so when the
            # write that replaces it fails, nothing is left to run the queue.
            logfire.exception(
                "wake-up: {job_id} could not be set; trying again in {seconds} s",
                job_id=job_id,
                seconds=AFTER_ERROR.total_seconds(),
            )
            _retry_later(job_id, after=AFTER_ERROR)
    _ensure_catch_up(scheduler)


@asynccontextmanager
async def rearm(job_id: str) -> AsyncGenerator[None]:
    """Wrap a queue's run: when it ends, set the next wake-up from the table.

    Not sooner than ``RERUN_GAP`` from now, or ``AFTER_ERROR`` when the run
    raised (the exception still propagates).
    """
    failed = False
    _RUNNING.add(job_id)
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        _RUNNING.discard(job_id)
        await wake(job_id, min_delay=AFTER_ERROR if failed else RERUN_GAP)


def run_soon(job_id: str) -> bool:
    """Set the queue's wake-up for now (the Scheduler page's "Run now").

    The run takes whatever is due and sets the next wake-up as usual. False when
    no queue has this id here.
    """
    queue = _QUEUES.get(job_id)
    if queue is None:
        return False
    _put(_scheduler(), queue, _utcnow())
    return True


@scheduled_job
async def catch_up() -> None:
    """The catch-up job: set every queue's wake-up again from its table."""
    for queue in queues():
        await wake(queue.job_id)


# Shown on the team's Scheduler page (api/src/schedulers/job_info.py).
CATCH_UP_JOB_INFO = JobInfo(
    name="Wake-ups: catch up on waiting work",
    summary=(
        "Every 15 minutes, and 2 minutes after each deploy, makes sure each job that runs "
        "when work is due is set for the work waiting in its table."
    ),
    description=(
        "The jobs on this page that say they run when work is due are not on a timer. "
        "Each feature keeps its waiting work in its own table with a due time, and its job "
        "is set to run at the earliest one: when work is recorded, and again at the end "
        "of every run. With nothing waiting the job is idle."
        "\n\n"
        "This job checks each of those tables and sets the job again. It only matters when "
        "a wake-up was lost: the database failed between saving the work and setting the "
        "job, or, during a deploy, the old server used a wake-up without setting the next. "
        "So it also runs 2 minutes after each deploy. It sends nothing and changes no work "
        "itself."
        "\n\n"
        "Removed, a lost wake-up leaves its work waiting until the next deploy. It is "
        "registered again on each startup and whenever another job notices it is missing."
    ),
)


def register_catch_up() -> None:
    """Schedule ``catch_up`` every 15 minutes. Called at startup when background
    automation is on (``api/index.py``)."""
    global _catch_up_registered
    from api.src.apscheduler_service.service import upsert_job

    upsert_job(
        _scheduler(),
        info=CATCH_UP_JOB_INFO,
        id=CATCH_UP_JOB_ID,
        func=catch_up,
        trigger="interval",
        seconds=int(CATCH_UP_EVERY.total_seconds()),
        next_run_time=_utcnow() + CATCH_UP_AFTER_STARTUP,
        coalesce=True,
        max_instances=1,
    )
    _catch_up_registered = True


def _ensure_catch_up(scheduler: AsyncIOScheduler) -> None:
    """Register the catch-up job again if it is gone from the job store.

    During a deploy the old server shares the job store, and APScheduler deletes
    a stored job whose function it cannot import. Older code has no
    ``catch_up``, so the old server can delete this job (the Scheduler page
    lists every job, which imports each one). Every wake-up checks for it.
    """
    if not _catch_up_registered:
        return
    try:
        if scheduler.get_job(CATCH_UP_JOB_ID) is None:
            register_catch_up()
            logfire.warn("wake-up: the catch-up job was missing and is registered again")
    except Exception:
        logfire.exception("wake-up: could not check the catch-up job")
