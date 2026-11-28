"""Wake-up jobs: each queue's job is set from its table, never polls, and is never lost.

Run against a real scheduler with an in-memory job store, started paused so no
job fires during a test.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from api.src.apscheduler_service import service, wakeups
from api.src.apscheduler_service.tracing import scheduled_job
from api.src.schedulers import job_info, routes
from api.src.schedulers.job_info import JobInfo

INFO = JobInfo(
    name="Example: a queue",
    summary="Works the example queue when its work is due.",
    description="Works the example queue when its work is due, and is idle otherwise.",
)


@scheduled_job
async def work_example_queue() -> None:
    """The example queue's run, as a real queue writes it."""
    async with wakeups.rearm("example-queue"):
        pass


class Table:
    """Stands in for a queue's table: the earliest due time, or None."""

    def __init__(self) -> None:
        self.due: datetime | None = None
        self.reads = 0

    async def next_due_at(self) -> datetime | None:
        self.reads += 1
        return self.due


def now() -> datetime:
    return datetime.now(UTC)


def close(a: datetime | None, b: datetime, within: timedelta = timedelta(seconds=2)) -> bool:
    return a is not None and abs(a - b) <= within


@pytest.fixture
def fresh(monkeypatch) -> Iterator[AsyncIOScheduler]:
    """A scheduler not yet started, in place of the app's, and empty registries."""
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    monkeypatch.setattr(service, "_scheduler", scheduler)
    monkeypatch.setattr(job_info, "_BY_JOB_ID", {})
    monkeypatch.setattr(wakeups, "_QUEUES", {})
    monkeypatch.setattr(wakeups, "_LOCKS", {})
    monkeypatch.setattr(wakeups, "_RUNNING", set())
    monkeypatch.setattr(wakeups, "_RETRYING", set())
    monkeypatch.setattr(wakeups, "_listening_to", None)
    monkeypatch.setattr(wakeups, "_catch_up_registered", False)
    yield scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)


@pytest_asyncio.fixture
async def scheduler(fresh) -> AsyncIOScheduler:
    """The same scheduler, running but paused: jobs are stored, none fires."""
    fresh.start(paused=True)
    return fresh


@pytest.fixture
def table() -> Table:
    return Table()


@pytest.fixture
def queue(table) -> wakeups.WakeQueue:
    return wakeups.WakeQueue(
        job_id="example-queue", info=INFO, run=work_example_queue, next_due_at=table.next_due_at
    )


@pytest.mark.asyncio
async def test_registering_a_queue_runs_it_shortly_after_startup(scheduler, queue):
    wakeups.register_queue(queue)
    job = scheduler.get_job("example-queue")
    assert isinstance(job.trigger, DateTrigger)
    assert close(job.next_run_time, now() + wakeups.STARTUP_DELAY)
    assert job.misfire_grace_time is None  # a wake-up due during a deploy still runs
    assert job.max_instances == 1 and job.coalesce
    assert job.func is work_example_queue
    assert job_info.info_for("example-queue") == INFO


@pytest.mark.asyncio
async def test_registering_replaces_the_interval_job_older_code_left(fresh, queue):
    """Production's job store still holds the queue's every-30-seconds job."""
    fresh.add_job(work_example_queue, "interval", seconds=30, id="example-queue")
    wakeups.register_queue(queue)  # before the scheduler starts, as at startup
    fresh.start(paused=True)
    job = fresh.get_job("example-queue")
    assert isinstance(job.trigger, DateTrigger)
    assert len(fresh.get_jobs()) == 1


@pytest.mark.asyncio
async def test_the_wake_up_is_set_for_the_earliest_due_work(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = now() + timedelta(minutes=5)
    await wakeups.wake("example-queue")
    job = scheduler.get_job("example-queue")
    assert isinstance(job.trigger, DateTrigger)
    assert close(job.next_run_time, table.due)
    assert job.misfire_grace_time is None


@pytest.mark.asyncio
async def test_work_already_due_runs_now(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = now() - timedelta(hours=1)
    await wakeups.wake("example-queue")
    assert close(scheduler.get_job("example-queue").next_run_time, now())


@pytest.mark.asyncio
async def test_with_nothing_waiting_the_queue_is_idle(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = None
    await wakeups.wake("example-queue")
    assert scheduler.get_job("example-queue") is None
    await wakeups.wake("example-queue")  # idle stays idle, without an error
    assert scheduler.get_job("example-queue") is None


@pytest.mark.asyncio
async def test_not_before_keeps_the_wake_up_from_being_sooner(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = now() - timedelta(minutes=1)
    await wakeups.wake("example-queue", not_before=now() + timedelta(seconds=60))
    assert close(scheduler.get_job("example-queue").next_run_time, now() + timedelta(seconds=60))
    # A later due time wins over a sooner not_before.
    table.due = now() + timedelta(minutes=10)
    await wakeups.wake("example-queue", not_before=now() + timedelta(seconds=5))
    assert close(scheduler.get_job("example-queue").next_run_time, table.due)


@pytest.mark.asyncio
async def test_a_wake_up_already_at_the_right_time_is_not_written_again(
    scheduler, queue, table, monkeypatch
):
    wakeups.register_queue(queue)
    table.due = now() + timedelta(minutes=5)
    await wakeups.wake("example-queue")
    puts = []
    monkeypatch.setattr(wakeups, "_put", lambda *args: puts.append(args))
    await wakeups.wake("example-queue")
    assert puts == []


@pytest.mark.asyncio
async def test_min_delay_counts_from_the_write_not_the_call(scheduler, queue, table, monkeypatch):
    """A slow read before the write must not leave the next wake-up due at once:
    at the end of a run it would come due while the run is still finishing, and
    APScheduler would skip and delete it."""
    import asyncio

    wakeups.register_queue(queue)
    table.due = now() - timedelta(minutes=1)
    read = table.next_due_at

    async def slow_read():
        await asyncio.sleep(0.6)
        return await read()

    slow = wakeups.WakeQueue(
        job_id="example-queue", info=INFO, run=work_example_queue, next_due_at=slow_read
    )
    monkeypatch.setitem(wakeups._QUEUES, "example-queue", slow)
    await wakeups.wake("example-queue", min_delay=timedelta(seconds=0.5))
    # Counted from before the read, it would already be past.
    assert scheduler.get_job("example-queue").next_run_time > now() + timedelta(seconds=0.3)


@pytest.mark.asyncio
async def test_a_failed_wake_is_tried_again_until_the_database_is_back(
    scheduler, queue, table, monkeypatch
):
    """The run's own wake-up was deleted when the run started; if the write that
    replaces it fails, an in-memory retry is the only thing left to run the queue
    before the catch-up job."""
    import asyncio

    monkeypatch.setattr(wakeups, "AFTER_ERROR", timedelta(seconds=0.2))
    monkeypatch.setattr(wakeups, "RERUN_GAP", timedelta(seconds=0))
    wakeups.register_queue(queue)
    scheduler.remove_job("example-queue")
    failures = [RuntimeError("database down"), RuntimeError("still down")]
    read = table.next_due_at

    async def flaky_read():
        if failures:
            raise failures.pop(0)
        return await read()

    flaky = wakeups.WakeQueue(
        job_id="example-queue", info=INFO, run=work_example_queue, next_due_at=flaky_read
    )
    monkeypatch.setitem(wakeups._QUEUES, "example-queue", flaky)
    table.due = now() + timedelta(minutes=3)
    await wakeups.wake("example-queue")
    assert scheduler.get_job("example-queue") is None
    for _ in range(30):
        if scheduler.get_job("example-queue") is not None:
            break
        await asyncio.sleep(0.05)
    assert failures == []  # tried twice more: one failure, then success
    assert close(scheduler.get_job("example-queue").next_run_time, table.due)


@pytest.mark.asyncio
async def test_wake_never_raises(scheduler, queue, monkeypatch):
    """The caller has already saved the work; a failure here must not undo it."""
    wakeups.register_queue(queue)
    broken = wakeups.WakeQueue(
        job_id="example-queue",
        info=INFO,
        run=work_example_queue,
        next_due_at=AsyncMock(side_effect=RuntimeError("database down")),
    )
    monkeypatch.setitem(wakeups._QUEUES, "example-queue", broken)
    await wakeups.wake("example-queue")
    assert scheduler.get_job("example-queue") is not None  # left as it was


@pytest.mark.asyncio
async def test_wake_does_nothing_where_the_scheduler_is_not_running(fresh, queue, table):
    """Background automation off (local, previews): no job is added anywhere."""
    wakeups._QUEUES["example-queue"] = queue
    table.due = now()
    await wakeups.wake("example-queue")
    assert fresh.get_jobs() == []
    assert table.reads == 0


@pytest.mark.asyncio
async def test_wake_ignores_a_queue_not_registered_here(scheduler):
    await wakeups.wake("no-such-queue")
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_each_run_sets_the_next_wake_up_a_little_later(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = now() - timedelta(minutes=1)  # more work is due at once
    await work_example_queue()
    assert close(scheduler.get_job("example-queue").next_run_time, now() + wakeups.RERUN_GAP)


@pytest.mark.asyncio
async def test_work_arriving_during_a_run_waits_for_the_runs_end(scheduler, queue, table):
    """A wake-up set mid-run could come due while the run goes on, and
    APScheduler skips a second run of the same job with a warning."""
    wakeups.register_queue(queue)
    async with wakeups.rearm("example-queue"):
        scheduler.remove_job("example-queue")  # the run's own wake-up is used up
        table.due = now()  # an email arrives
        await wakeups.wake("example-queue")
        assert scheduler.get_job("example-queue") is None
    assert close(scheduler.get_job("example-queue").next_run_time, now() + wakeups.RERUN_GAP)


@pytest.mark.asyncio
async def test_a_run_that_raises_waits_longer_before_the_next(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = now() - timedelta(minutes=1)
    with pytest.raises(RuntimeError):
        async with wakeups.rearm("example-queue"):
            raise RuntimeError("boom")
    assert close(scheduler.get_job("example-queue").next_run_time, now() + wakeups.AFTER_ERROR)


@pytest.mark.asyncio
async def test_catch_up_sets_every_queue_again(scheduler, queue, table):
    other_table = Table()
    other = wakeups.WakeQueue(
        job_id="other-queue", info=INFO, run=work_example_queue, next_due_at=other_table.next_due_at
    )
    wakeups.register_queue(queue)
    wakeups.register_queue(other)
    # Both wake-ups lost.
    scheduler.remove_job("example-queue")
    scheduler.remove_job("other-queue")
    table.due = now() + timedelta(minutes=3)
    other_table.due = now() - timedelta(minutes=3)
    await wakeups.catch_up()
    assert close(scheduler.get_job("example-queue").next_run_time, table.due)
    assert close(scheduler.get_job("other-queue").next_run_time, now())


@pytest.mark.asyncio
async def test_the_catch_up_job_runs_2_minutes_after_startup_then_every_15(scheduler):
    wakeups.register_catch_up()
    job = scheduler.get_job(wakeups.CATCH_UP_JOB_ID)
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(minutes=15)
    assert close(job.next_run_time, now() + timedelta(minutes=2))
    assert job.func is wakeups.catch_up


@pytest.mark.asyncio
async def test_a_deleted_catch_up_job_comes_back_at_the_next_wake(scheduler, queue, table):
    """During a deploy the old server can delete a job whose function it lacks."""
    wakeups.register_queue(queue)
    wakeups.register_catch_up()
    scheduler.remove_job(wakeups.CATCH_UP_JOB_ID)
    table.due = now() + timedelta(minutes=3)
    await wakeups.wake("example-queue")
    assert scheduler.get_job(wakeups.CATCH_UP_JOB_ID) is not None


@pytest.mark.asyncio
async def test_run_soon_sets_the_wake_up_for_now(scheduler, queue, table):
    wakeups.register_queue(queue)
    table.due = None
    await wakeups.wake("example-queue")  # idle
    assert wakeups.run_soon("example-queue") is True
    assert close(scheduler.get_job("example-queue").next_run_time, now())
    assert wakeups.run_soon("no-such-queue") is False


class TestSchedulerPage:
    @pytest.mark.asyncio
    async def test_an_idle_queue_is_still_listed(self, scheduler, queue, table):
        wakeups.register_queue(queue)
        table.due = None
        await wakeups.wake("example-queue")
        jobs = {j["id"]: j for j in await routes.get_jobs()}
        idle = jobs["example-queue"]
        assert idle["wake_up"] is True
        assert idle["next_run_time"] is None and idle["pending"] is False
        assert idle["name"] == INFO.name and idle["description"] == INFO.description
        assert idle["func_ref"].endswith("test_scheduler_wakeups:work_example_queue")

    @pytest.mark.asyncio
    async def test_a_waiting_queue_shows_its_next_run(self, scheduler, queue, table):
        wakeups.register_queue(queue)
        table.due = now() + timedelta(minutes=4)
        await wakeups.wake("example-queue")
        jobs = {j["id"]: j for j in await routes.get_jobs()}
        assert jobs["example-queue"]["wake_up"] is True
        assert jobs["example-queue"]["next_run_time"] is not None
        assert [j["id"] for j in await routes.get_jobs()].count("example-queue") == 1

    @pytest.mark.asyncio
    async def test_other_jobs_are_not_wake_ups(self, scheduler):
        wakeups.register_catch_up()
        jobs = {j["id"]: j for j in await routes.get_jobs()}
        assert jobs[wakeups.CATCH_UP_JOB_ID]["wake_up"] is False

    @pytest.mark.asyncio
    async def test_run_now_starts_an_idle_queue(self, scheduler, queue, table):
        wakeups.register_queue(queue)
        table.due = None
        await wakeups.wake("example-queue")
        result = await routes.run_job_now("apscheduler", "example-queue")
        assert result["service"] == "apscheduler"
        assert close(scheduler.get_job("example-queue").next_run_time, now())


RUNS: list[datetime] = []
LIVE_TABLE = Table()


@scheduled_job
async def work_live_queue() -> None:
    """Takes everything due, as a real queue does, then sets the next wake-up."""
    async with wakeups.rearm("live-queue"):
        RUNS.append(now())
        LIVE_TABLE.due = None


@pytest.mark.asyncio
async def test_a_live_scheduler_runs_the_queue_when_due_and_then_goes_idle(fresh):
    """The real thing, unpaused: the wake-up fires, the run takes the work, and
    the end of the run finds nothing waiting and leaves the queue idle."""
    import asyncio

    RUNS.clear()
    live = wakeups.WakeQueue(
        job_id="live-queue", info=INFO, run=work_live_queue, next_due_at=LIVE_TABLE.next_due_at
    )
    fresh.start()
    wakeups._QUEUES["live-queue"] = live
    LIVE_TABLE.due = now() + timedelta(milliseconds=300)
    await wakeups.wake("live-queue")
    assert fresh.get_job("live-queue") is not None

    for _ in range(50):
        if RUNS and fresh.get_job("live-queue") is None:
            break
        await asyncio.sleep(0.1)
    assert len(RUNS) == 1
    assert fresh.get_job("live-queue") is None  # idle: nothing waits

    # New work arrives: one more run, no polling in between.
    LIVE_TABLE.due = now()
    await wakeups.wake("live-queue")
    for _ in range(50):
        if len(RUNS) == 2 and fresh.get_job("live-queue") is None:
            break
        await asyncio.sleep(0.1)
    assert len(RUNS) == 2
    assert fresh.get_job("live-queue") is None


def test_a_wake_up_stored_in_postgres_comes_back_traced_and_never_dropped():
    """Production's job store saves a job by its function's import path and
    imports it again for every run. The wake-up must come back as the traced
    function, still with no misfire limit."""
    from uuid import uuid4

    from apscheduler.job import Job
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from sqlalchemy import text

    from api.src.apscheduler_service.tracing import is_traced
    from api.src.database.database import sync_engine
    from api.src.zillow_leads import queue as zillow_queue

    table_name = f"apscheduler_jobs_test_{uuid4().hex[:8]}"
    store = SQLAlchemyJobStore(engine=sync_engine, tablename=table_name)
    scheduler = AsyncIOScheduler(jobstores={"default": store})
    try:
        store.start(scheduler, "default")
        trigger = DateTrigger(run_date=now() + timedelta(minutes=5))
        store.add_job(
            Job(
                scheduler,
                id="zillow-email-queue",
                func=zillow_queue.process_zillow_email_queue,
                trigger=trigger,
                executor="default",
                args=(),
                kwargs={},
                name="Zillow lead emails: process the queue",
                misfire_grace_time=None,
                coalesce=True,
                max_instances=1,
                next_run_time=trigger.run_date,
            )
        )
        loaded = store.lookup_job("zillow-email-queue")
        assert is_traced(loaded.func)
        assert loaded.func is zillow_queue.process_zillow_email_queue
        assert loaded.misfire_grace_time is None
        assert loaded.func_ref == "api.src.zillow_leads.queue:process_zillow_email_queue"
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))


@pytest.mark.asyncio
async def test_the_scheduler_keeps_running_when_another_server_took_a_job_first():
    """During a deploy two servers share the job store and both start the same
    wake-up; the second to delete it gets JobLookupError. Plain APScheduler then
    sets no next timer and this server runs no job again."""
    import asyncio

    ticks: list[datetime] = []
    woken: list[datetime] = []

    async def tick() -> None:
        ticks.append(now())

    async def wake_up() -> None:
        woken.append(now())

    scheduler = service.ResilientAsyncIOScheduler(
        jobstores={"default": MemoryJobStore()}, jobstore_retry_interval=0.2
    )
    scheduler.start()
    try:
        scheduler.add_job(tick, "interval", seconds=0.3, id="tick")
        store = scheduler._jobstores["default"]
        executor = scheduler._executors["default"]
        submit = executor.submit_job

        def submit_then_lose_the_race(job, run_times):
            submit(job, run_times)
            if job.id == "wake-up":
                store.remove_job("wake-up")  # the other server deleted it first

        executor.submit_job = submit_then_lose_the_race
        scheduler.add_job(
            wake_up,
            "date",
            run_date=now() + timedelta(milliseconds=200),
            id="wake-up",
            misfire_grace_time=None,
        )
        await asyncio.sleep(1.5)
        assert woken, "the wake-up should have run"
        assert [t for t in ticks if t > woken[0] + timedelta(milliseconds=300)], (
            "no job ran after the failed pass: the scheduler stalled"
        )
    finally:
        scheduler.shutdown(wait=False)


SKIP_RUNS: list[datetime] = []
SKIP_TABLE = Table()


@scheduled_job
async def work_slowly_finishing_queue() -> None:
    """A run whose next wake-up comes due before it has returned (the gap
    swallowed by a slow write, say). APScheduler skips that wake-up and deletes
    it; the skip listener must set it again."""
    import asyncio

    SKIP_RUNS.append(now())
    if len(SKIP_RUNS) == 1:
        wakeups._put(wakeups._scheduler(), wakeups.get_queue("skip-queue"), now())
        await asyncio.sleep(0.5)  # still running when it comes due
        SKIP_TABLE.due = now()  # and work is waiting
    else:
        SKIP_TABLE.due = None


@pytest.mark.asyncio
async def test_a_wake_up_skipped_because_the_last_run_was_finishing_is_set_again(
    fresh, monkeypatch
):
    import asyncio

    SKIP_RUNS.clear()
    monkeypatch.setattr(wakeups, "RERUN_GAP", timedelta(seconds=0.2))
    skipping = wakeups.WakeQueue(
        job_id="skip-queue",
        info=INFO,
        run=work_slowly_finishing_queue,
        next_due_at=SKIP_TABLE.next_due_at,
    )
    wakeups.register_queue(skipping)
    fresh.start()
    fresh.remove_job("skip-queue")
    SKIP_TABLE.due = now()
    await wakeups.wake("skip-queue")
    for _ in range(40):
        if len(SKIP_RUNS) >= 2:
            break
        await asyncio.sleep(0.1)
    assert len(SKIP_RUNS) == 2, "the skipped wake-up was lost"
