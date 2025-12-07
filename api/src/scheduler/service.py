from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(".env"), override=True)

import asyncio
import logfire
import pytest
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.job import Job
from apscheduler.events import EVENT_JOB_ERROR
from datetime import datetime, timedelta
from typing import Literal

from api.src.contact.service import get_contact_by_slug
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import send_email
from api.src.open_phone.service import send_message
from api.src.push.service import send_push_to_user

# Import the synchronous engine from database.py
from api.src.database.database import sync_engine

# --- Monkey-patch APScheduler Job.__str__ to include job_id --- START
# Store the original __str__ method in case it's ever needed for reversion or comparison
_original_apscheduler_job_str = Job.__str__

def custom_apscheduler_job_str(self):
    # self is an apscheduler.job.Job instance
    return f"{self.name} (job_id: {self.id})"

Job.__str__ = custom_apscheduler_job_str
logfire.info("APScheduler Job.__str__ has been monkey-patched to include job_id.")
# --- Monkey-patch APScheduler Job.__str__ to include job_id --- END

# Configure the job store
if sync_engine:
    logfire.info(
        "Scheduler using SQLAlchemyJobStore with pre-configured synchronous engine."
    )
    jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine)}
else:
    raise Exception(
        "Synchronous engine not available. Scheduler cannot be initialized."
    )

# index.py will handle the start/shutdown of this scheduler instance
scheduler = AsyncIOScheduler(
    jobstores=jobstores,
    job_defaults={
        'grace_time': 60  # Set default grace_time to 60 seconds
    }
)

# Note: APScheduler's internal logging is captured by logfire through OpenTelemetry


# --- Centralized Job Error Handling --- START
async def handle_job_error(event):
    """
    Handles APScheduler job errors by logging them to Logfire.
    Logfire is configured to send alerts for exceptions.
    """
    job_id = event.job_id
    exception = event.exception
    traceback_str = event.traceback

    # Use logfire.exception() to properly capture the exception for Logfire alerting
    # This ensures Logfire categorizes it as an exception and can trigger alerts
    logfire.exception(
        f"APScheduler job '{job_id}' failed",
        job_id=job_id,
        exception_type=type(exception).__name__,
        exception_message=str(exception),
        traceback=traceback_str,
    )

# Synchronous wrapper for the async error handler
def sync_error_listener_wrapper(event):
    logfire.info(f"--- sync_error_listener_wrapper received event for job {event.job_id}, creating task for handle_job_error ---")
    asyncio.create_task(handle_job_error(event))

# Register the error handler
scheduler.add_listener(sync_error_listener_wrapper, EVENT_JOB_ERROR) # Use the wrapper
logfire.info("Registered central job error handler wrapper.")
# --- Centralized Job Error Handling --- END


# functions_available_to_scheduler = {
#     "send_message": send_message,
#     "send_email": send_email,
#     "send_push_to_user": send_push_to_user
# }


async def schedule_sms(
    message: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    run_date: datetime,
):
    sernia_contact = await get_contact_by_slug("sernia")
    phone_numbers = {
        "EMILIO": "+14123703550",
        "JACKIE": "+14123703505",
        "PEPPINO": "+14126800593",
        "ANNA": "+14124172322",
        "SERNIA": sernia_contact.phone_number,
    }

    to_phone_number = phone_numbers[recipient]

    is_scheduled = False
    try:
        scheduler.add_job(
            func=send_message,
            kwargs={
                "message": message,
                "to_phone_number": to_phone_number,
                "from_phone_number": "+14129101500",
            },
            trigger="date",
            run_date=run_date,
            timezone=pytz.timezone("America/New_York"),
        )
        is_scheduled = True
    except Exception as e:
        logfire.error(f"Error scheduling SMS: {e}")
        is_scheduled = False

    return is_scheduled


@pytest.mark.asyncio
async def test_schedule_sms():
    scheduler.start()
    is_scheduled = await schedule_sms(
        message="Hello, this is a test message",
        recipient="EMILIO",
        run_date=datetime.now() + timedelta(seconds=5),
    )
    await asyncio.sleep(10)
    scheduler.shutdown()
    assert is_scheduled


async def schedule_email(
    subject: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    run_date: datetime,
):

    emails = {
        "EMILIO": "emilio@serniacapital.com",
        "JACKIE": "jackie@serniacapital.com",
        "PEPPINO": "peppino@serniacapital.com",
        "ANNA": "anna@serniacapital.com",
        "SERNIA": "all@serniacapital.com",
    }

    # await send_email(
    #     to="espo412@gmail.com",
    #     subject="Test email",
    #     message_text="This is a test email",
    #     credentials=get_delegated_credentials(
    #         user_email="emilio@serniacapital.com", scopes=["https://mail.google.com"]
    #     ),
    # )

    to_email = emails[recipient]
    is_scheduled = False
    try:
        scheduler.add_job(
            func=send_email,
            kwargs={
                "subject": subject,
                "message_text": body,
                "to": to_email,
                "credentials": get_delegated_credentials(
                    user_email="emilio@serniacapital.com",
                    scopes=["https://mail.google.com"],
                ),
            },
            trigger="date",
            run_date=run_date,
            timezone=pytz.timezone("America/New_York"),
        )
        is_scheduled = True
    except Exception as e:
        logfire.error(f"Error scheduling email: {e}")
        is_scheduled = False

    return is_scheduled


@pytest.mark.asyncio
async def test_schedule_email():
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


async def schedule_push(
    title: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    run_date: datetime,
):
    emails = {
        "EMILIO": "emilio@serniacapital.com",
        "JACKIE": "jackie@serniacapital.com",
        "PEPPINO": "peppino@serniacapital.com",
        "ANNA": "anna@serniacapital.com",
        "SERNIA": "all@serniacapital.com",
    }

    to_email = emails[recipient]
    is_scheduled = False

    # await send_push_to_user(
    #         email=test_email,
    #         title="Pytest Hello World!",
    #         body="This is a test notification from pytest.",
    #         data={"test": True},
    #     )
    
    try:
        scheduler.add_job(
            func=send_push_to_user,
            kwargs={
                "email": to_email,
                "title": title,
                "body": body,
                "data": {"test": True},
            },
            trigger="date",
            run_date=run_date,
            timezone=pytz.timezone("America/New_York"),
        )
        is_scheduled = True
    except Exception as e:
        logfire.error(f"Error scheduling push: {e}")
        is_scheduled = False

    return is_scheduled


@pytest.mark.asyncio
async def test_schedule_push():
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

# EXAMPLES

# scheduler.add_job(
#     id="send_push_to_user_job",
#     func=send_push_to_user,
#     kwargs={
#         "email": "emilio@serniacapital.com",
#         "title": "APScheduler Test Notification",
#         "body": "This is a test notification from the APScheduler.",
#         "data": {"test": True}
#     },
#     trigger="interval",
#     seconds=300,
#     replace_existing=True
# )


# scheduler.add_job(
#     id="one_time_job",
#     func=send_push_to_user,
#     kwargs={
#         "email": "emilio@serniacapital.com",
#         "title": "One Time APScheduler Test Notification",
#         "body": "This is a test notification from the APScheduler.",
#         "data": {"test": True}
#     },
#     trigger="date",
#     run_date=datetime.now() + timedelta(seconds=30),
#     replace_existing=True
# )


# TESTING


async def run_hello_world(name: str):
    print(f"print: Hello {name} from test_job executed at {datetime.now()}")
    logfire.info(f"logger: Hello {name} from test_job executed at {datetime.now()}")


def test_run_hello_world():
    # This function demonstrates adding a job and running the scheduler directly.
    # In the main app, scheduler.start() and scheduler.shutdown() are called by lifespan events.
    print("test_job")

    async def main_test_logic():  # Make it async to use await for scheduler methods
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


@pytest.mark.asyncio
async def test_job_that_will_fail():
    logfire.info("--- test_job_that_will_fail START ---")
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
