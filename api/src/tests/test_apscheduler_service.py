"""
Tests for the APScheduler service and routes.

Moved verbatim from ``api/src/apscheduler_service/service.py`` and
``api/src/apscheduler_service/routes.py``. Marked ``live`` so they only
run when explicitly requested:

    pytest -m live api/src/tests/test_apscheduler_service.py -v -s
"""

import asyncio
from datetime import datetime, timedelta
from pprint import pprint

import logfire
import pytest
import pytz
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.apscheduler_service.routes import get_jobs, run_job_now
from api.src.apscheduler_service.service import (
    get_scheduler,
    run_hello_world,
    schedule_email,
    schedule_push,
    schedule_sms,
)


@pytest.mark.live
@pytest.mark.asyncio
async def test_schedule_sms():
    scheduler = get_scheduler()
    scheduler.start()
    is_scheduled = await schedule_sms(
        message="Hello, this is a test message",
        recipient="EMILIO",
        run_date=datetime.now() + timedelta(seconds=5),
    )
    await asyncio.sleep(10)
    scheduler.shutdown()
    assert is_scheduled


@pytest.mark.live
@pytest.mark.asyncio
async def test_schedule_email():
    scheduler = get_scheduler()
    scheduler.start()
    is_scheduled = await schedule_email(
        subject="Test Email",
        body="This is a test email",
        recipient="EMILIO",
        run_date=datetime.now() + timedelta(seconds=5),
    )
    await asyncio.sleep(10)
    scheduler.shutdown()
    assert is_scheduled


@pytest.mark.live
@pytest.mark.asyncio
async def test_schedule_push():
    scheduler = get_scheduler()
    scheduler.start()
    is_scheduled = await schedule_push(
        title="Scheduled Test Push",
        body="This is a Scheduled Test Push",
        recipient="EMILIO",
        run_date=datetime.now() + timedelta(seconds=5),
    )
    await asyncio.sleep(10)
    scheduler.shutdown()
    assert is_scheduled


@pytest.mark.live
def test_run_hello_world():
    # This function demonstrates adding a job and running the scheduler directly.
    # In the main app, scheduler.start() and scheduler.shutdown() are called by lifespan events.
    print("test_job")

    async def main_test_logic():  # Make it async to use await for scheduler methods
        scheduler = get_scheduler()
        # Start the scheduler if it's not already running (e.g. when running this script directly)
        if not scheduler.running:
            logfire.info("Starting scheduler for test_job...")
            scheduler.start()
        else:
            logfire.info("Scheduler already running for test_job.")

        job_id = "hello_world_test_job"
        run_date = datetime.now() + timedelta(seconds=5)  # Shortened for faster test
        logfire.info(f"Adding job '{job_id}' to run at {run_date}")

        scheduler.add_job(
            func=run_hello_world,
            trigger="date",
            kwargs={"name": "Emilio"},
            id=job_id,  # Use id instead of job_id for add_job method
            run_date=run_date,  # Pass run_date directly for date trigger
            replace_existing=True,
        )

        job = scheduler.get_job(job_id=job_id)
        logfire.info(f"Job added: {job}")

        jobs = scheduler.get_jobs()
        logfire.info(f"Jobs: {jobs}")

        # Wait for the job to run
        # Giving a bit more time than the scheduled time
        await asyncio.sleep(10)

        # get job again (should be None now)
        job = scheduler.get_job(job_id=job_id)  # Manually check if the job was removed

        # Manually remove the job if for some reason it still exists (it should self deleteif it was trigger="date")
        if job:
            scheduler.remove_job(job_id=job_id)
            raise Exception("Job was not removed automatically")

        # Shutdown the scheduler if it was started by this test logic
        # In a real app, lifespan events handle this.
        # For a standalone test, it depends on whether you want to test shutdown too.
        logfire.info("Shutting down scheduler after test_job...")
        scheduler.shutdown(wait=True)

    import asyncio

    asyncio.run(main_test_logic())


async def job_that_will_fail():
    x=5
    logfire.info(f"job_that_will_fail: Executing, x = {x}")
    print(f"job_that_will_fail: print x = {x}") # For quick visual check in console
    logfire.info("job_that_will_fail: About to raise ValueError for testing error handler.")
    raise ValueError("This job is designed to fail for testing the error handler.")


@pytest.mark.live
@pytest.mark.asyncio
async def test_job_that_will_fail():
    logfire.info("--- test_job_that_will_fail START ---")
    scheduler = get_scheduler()
    # Ensure scheduler is started for this test
    if not scheduler.running:
        logfire.info("Starting scheduler for test_job_that_will_fail...")
        scheduler.start()
    else:
        logfire.warn("Scheduler was already running at the start of test_job_that_will_fail.")

    failing_job_id = "failing_test_job_for_handler"

    # Use job's target timezone for creating run_date and schedule a bit further out
    ny_tz = pytz.timezone("America/New_York")
    run_date_ny = datetime.now(ny_tz) + timedelta(seconds=4) # Increased to 4 seconds

    logfire.info(f"Adding failing job '{failing_job_id}' to run at {run_date_ny.isoformat()} (TZ: America/New_York) for error handler test.")

    scheduler.add_job(
        func=job_that_will_fail,
        trigger="date",
        id=failing_job_id,
        run_date=run_date_ny, # Use the NY-aware datetime
        replace_existing=True,
        timezone=ny_tz, # Explicitly set, matches run_date's tz
    )

    failing_job = scheduler.get_job(job_id=failing_job_id)
    assert failing_job is not None, f"Failing job {failing_job_id} was not added successfully."
    logfire.info(f"Failing job added: {failing_job} (Next run: {failing_job.next_run_time.isoformat() if failing_job.next_run_time else 'N/A'})")

    # Wait long enough for the job to execute and the error handler (including email) to fire
    # Increased sleep duration to give more time for all async operations.
    logfire.info("Waiting for job to run and error handler to complete (approx 12s)...")
    await asyncio.sleep(12) # Increased from 10 to 12

    # The job should have run, failed, and been caught by the error handler.
    # Date-triggered jobs are typically removed after execution (or attempted execution).
    failing_job_after_run = scheduler.get_job(job_id=failing_job_id)
    assert failing_job_after_run is None, f"Failing job {failing_job_id} should have been removed after attempting to run."
    logfire.info(f"Failing job {failing_job_id} was correctly removed after execution attempt (expected for date trigger).")

    # Ensure scheduler is shutdown after this test
    if scheduler.running:
        logfire.info("Shutting down scheduler after test_job_that_will_fail...")
        scheduler.shutdown(wait=True)
    logfire.info("--- test_job_that_will_fail END ---")


@pytest.mark.live
@pytest.mark.asyncio
async def test_get_jobs():

    scheduler = get_scheduler()
    if not scheduler.running:
        logfire.info("Starting scheduler for test_job...")
        scheduler.start()
    else:
        logfire.info("Scheduler already running for test_job.")
    jobs = await get_jobs()
    pprint(jobs)
    scheduler.shutdown()
    assert len(jobs) > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_run_job_now():
    scheduler = get_scheduler()
    if not scheduler.running:
        logfire.info("Starting scheduler for test_job...")
        scheduler.start()
    else:
        logfire.info("Scheduler already running for test_job.")
    job_id = "zillow_test_job"
    await run_job_now(job_id)
    await asyncio.sleep(10)
    scheduler.shutdown()
