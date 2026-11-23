"""Every scheduled job has a name, a summary and a description for the Scheduler page.

The registration test runs the real startup registration (``api/index.py``) into a
scheduler that is never started, so a job added there without
``upsert_job(..., info=JobInfo(...))`` fails here, and so does a change to an
existing job's id or schedule.
"""

from datetime import datetime, timedelta

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import api.index as index
from api.src.apscheduler_service import service
from api.src.apscheduler_service.service import upsert_job
from api.src.schedulers import job_info, routes
from api.src.schedulers.job_info import JobInfo
from api.src.sernia_ai.config import DEFAULT_SCHEDULE_DAYS_OF_WEEK, DEFAULT_SCHEDULE_HOURS
from api.src.sernia_ai.triggers import scheduled_triggers

# What production registers, with the schedule each has today. The scheduled
# check's schedule comes from Settings; here it is the default.
PRODUCTION_JOBS = {
    "clickup_peppino_tasks_scheduled": "cron[hour='8,17', minute='0']",
    "escalation-outbox": "interval[0:00:15]",
    "sernia_scheduled_checks": "cron[day_of_week='0,1,2,3,4', hour='8,11,14,17', minute='0']",
    "showing-details-removal": "cron[hour='3', minute='30']",
    "showing-notices": "interval[0:01:00]",
    "zillow-email-queue": "interval[0:00:30]",
}


@pytest.fixture
def scheduler(monkeypatch) -> AsyncIOScheduler:
    """A scheduler that is never started, in place of the app's, and an empty registry."""
    fresh = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    monkeypatch.setattr(fresh, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_scheduler", fresh)
    monkeypatch.setattr(job_info, "_BY_JOB_ID", {})
    return fresh


async def _register_as_production(monkeypatch) -> None:
    """Run the startup registration with the scheduled check on, as in production."""

    async def default_schedule() -> dict:
        return {"days_of_week": DEFAULT_SCHEDULE_DAYS_OF_WEEK, "hours": DEFAULT_SCHEDULE_HOURS}

    monkeypatch.setattr(scheduled_triggers, "_IS_PRODUCTION", True)
    monkeypatch.setattr(scheduled_triggers, "get_schedule_config", default_schedule)
    failures: list[tuple] = []
    # The startup task logs a failure instead of raising; make it visible here.
    monkeypatch.setattr(index.logfire, "exception", lambda *args, **kwargs: failures.append(args))
    await index._apscheduler_startup_async()
    assert failures == []


@pytest.mark.asyncio
async def test_every_job_registered_at_startup_is_described(scheduler, monkeypatch):
    await _register_as_production(monkeypatch)
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(PRODUCTION_JOBS) <= set(jobs)
    for job_id, trigger in PRODUCTION_JOBS.items():
        assert str(jobs[job_id].trigger) == trigger, job_id

    for job in jobs.values():
        info = job_info.info_for(job.id)
        assert info is not None, (
            f"{job.id} has no description: pass info=JobInfo(...) to upsert_job "
            "beside its registration (see api/src/schedulers/README.md)"
        )
        assert job.name == info.name, job.id
        assert info.name.strip() and info.name != job.id, job.id
        # One sentence, shown on the collapsed card.
        assert info.summary.endswith(".") and ". " not in info.summary, job.id
        assert len(info.summary) <= 160, job.id
        assert len(info.description) > len(info.summary), job.id


@pytest.mark.asyncio
async def test_get_jobs_returns_the_description_and_lists_jobs_without_one(scheduler):
    async def noop() -> None:
        return None

    described = JobInfo(
        name="Example: a described job",
        summary="Does one thing.",
        description="Does one thing, every day at 8 am.",
    )
    upsert_job(
        scheduler,
        info=described,
        id="described-job",
        func=noop,
        trigger="cron",
        hour=8,
        minute=0,
        timezone="America/New_York",
    )
    # As the agent schedules a one-off text: a name, no description.
    scheduler.add_job(
        noop,
        trigger="date",
        run_date=datetime.now() + timedelta(days=1),
        id="sms-0000",
        name="SMS to a contact",
    )
    scheduler.add_job(noop, trigger="interval", seconds=30, id="bare-job")

    jobs = {job["id"]: job for job in await routes.get_jobs()}

    described_job = jobs["described-job"]
    assert described_job["name"] == "Example: a described job"
    assert described_job["summary"] == "Does one thing."
    assert described_job["description"] == "Does one thing, every day at 8 am."
    assert described_job["trigger"] == "cron[hour='8', minute='0']"
    assert described_job["timezone"] == "America/New_York"
    assert described_job["service"] == "apscheduler"

    assert jobs["sms-0000"]["name"] == "SMS to a contact"
    assert jobs["sms-0000"]["summary"] is None and jobs["sms-0000"]["description"] is None
    # APScheduler names an unnamed job after its function, never the id alone.
    assert jobs["bare-job"]["name"].endswith("noop")
    assert jobs["bare-job"]["summary"] is None
    # Not started here, so no job has a next run yet; listing must not fail on that.
    assert all(job["pending"] and job["next_run_time"] is None for job in jobs.values())
