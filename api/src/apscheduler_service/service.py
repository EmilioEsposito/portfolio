from dotenv import find_dotenv, load_dotenv

from api.src.google.common.service_account_auth import get_delegated_credentials

load_dotenv(find_dotenv(".env"), override=True)
import asyncio
import functools
import os
import threading
from datetime import datetime, timedelta
from typing import Literal

import logfire
import pytz
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.job import Job
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from opentelemetry import context as otel_context

from api.src.contact.service import get_contact_by_slug

# Import the synchronous engine from database.py
from api.src.database.database import sync_engine
from api.src.google.gmail.service import send_email
from api.src.open_phone.service import send_message
from api.src.push.service import send_push_to_user

# --- Monkey-patch APScheduler Job.__str__ to include job_id --- START
# Store the original __str__ method in case it's ever needed for reversion or comparison
_original_apscheduler_job_str = Job.__str__


def custom_apscheduler_job_str(self):
    # self is an apscheduler.job.Job instance
    return f"{self.name} (job_id: {self.id})"


Job.__str__ = custom_apscheduler_job_str
logfire.info("APScheduler Job.__str__ has been monkey-patched to include job_id.")
# --- Monkey-patch APScheduler Job.__str__ to include job_id --- END

_scheduler_lock = threading.Lock()
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """
    Lazily construct and return the global APScheduler instance.

    This keeps module import fast (no scheduler/jobstore construction at import time),
    while preserving a single shared scheduler instance process-wide.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler

        # APScheduler v3's SQLAlchemyJobStore is synchronous — every DB operation
        # (add_job, get_due_jobs, update_job) blocks the asyncio event loop.
        # On Neon each operation takes ~0.15-0.5s, and startup does ~20 of them
        # sequentially, freezing the event loop for seconds. This delays HTTP
        # requests including health checks.
        #
        # Local dev uses MemoryJobStore to avoid this entirely (0.01s startup).
        # Railway uses SQLAlchemyJobStore for persistence across deploys — the
        # one-time startup cost is acceptable and the 120s health check timeout
        # absorbs it. The sync engine's QueuePool (see database.py) mitigates
        # the per-operation cost by reusing connections.
        #
        # Long-term fix: migrate to APScheduler v4 which has native async
        # support via AsyncScheduler + SQLAlchemyDataStore with async engines.
        is_hosted = len(os.getenv("RAILWAY_ENVIRONMENT_NAME", "")) > 0

        if is_hosted:
            if not sync_engine:
                raise Exception(
                    "Synchronous engine not available. Scheduler cannot be initialized."
                )
            logfire.info("Creating APScheduler (AsyncIOScheduler) with SQLAlchemyJobStore.")
            jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine)}
        else:
            logfire.info("Creating APScheduler (AsyncIOScheduler) with MemoryJobStore (local dev).")
            jobstores = {"default": MemoryJobStore()}

        scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults={
                "misfire_grace_time": 60  # Grace time in seconds for missed job execution
            },
        )

        # Register the error handler (once)
        scheduler.add_listener(sync_error_listener_wrapper, EVENT_JOB_ERROR)  # Use the wrapper
        logfire.info("Registered central job error handler wrapper.")

        _scheduler = scheduler
        return _scheduler


def _new_trace(func):
    """Wrap an async job function so it runs in a fresh OpenTelemetry trace.

    Without this, APScheduler jobs inherit the lifespan trace and all spans
    pile up under a single 'LIFESPAN: FastAPI index.py' trace that runs for
    hours, making individual job runs impossible to find in Logfire.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Detach from the current (lifespan) trace context so logfire.span
        # below starts a brand-new root trace.
        token = otel_context.attach(otel_context.Context())
        try:
            with logfire.span("apscheduler job: {job_name}", job_name=func.__qualname__):
                return await func(*args, **kwargs)
        finally:
            otel_context.detach(token)

    return wrapper


def upsert_job(scheduler: AsyncIOScheduler, **kwargs) -> None:
    """Add a job, cleanly replacing any existing job with the same ID.

    Automatically wraps async job functions with _new_trace so each execution
    gets its own trace in Logfire instead of inheriting the lifespan trace.

    When the scheduler is already running, we remove-then-add to avoid a
    spurious INSERT→conflict→UPDATE cycle that creates error-level DB spans
    in Logfire on every call.

    When the scheduler is NOT running (i.e. during startup), add_job() stores
    the job in an in-memory pending list — NOT in the DB — so remove+add is
    broken (the remove hits the DB, but the add doesn't). In that case we use
    replace_existing=True, which is handled correctly when start() flushes
    pending jobs.
    """
    kwargs.pop("replace_existing", None)
    # Wrap async functions so each run gets its own trace
    func = kwargs.get("func")
    if func and asyncio.iscoroutinefunction(func):
        kwargs["func"] = _new_trace(func)

    job_id = kwargs.get("id")
    if scheduler.running and job_id:
        # Scheduler is live: remove first to avoid noisy DB conflict spans
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass
        scheduler.add_job(**kwargs)
    else:
        # Scheduler not started: add_job goes to _pending_jobs.
        # replace_existing=True ensures start() handles duplicates gracefully.
        scheduler.add_job(**kwargs, replace_existing=True)


# Note: APScheduler's internal logging (e.g., misfire warnings) is captured by
# logging.getLogger().addHandler(...) configured in api.src.utils.logfire_config


# --- Centralized Job Error Handling --- START
async def handle_job_error(event):
    logfire.info(f"--- handle_job_error START for job {event.job_id} ---")
    job_id = event.job_id
    exception = event.exception
    traceback_str = event.traceback

    logfire.exception(
        f"Job {job_id} raised an exception",
        job_id=job_id,
        _exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
    )

    credentials = None
    logfire.info(f"Attempting to get delegated credentials for job {job_id} error email.")
    try:
        # Assuming get_delegated_credentials might be synchronous and I/O bound.
        # If it's already async, this to_thread call is okay but not strictly necessary.
        credentials = await asyncio.to_thread(
            get_delegated_credentials,
            user_email="emilio@serniacapital.com",  # TODO: Move to env var?
            scopes=["https://mail.google.com"],
        )
        logfire.info(f"Successfully got credentials for job {job_id} error email.")
    except Exception:
        logfire.exception(f"Failed to get delegated credentials for job {job_id} error email")
        logfire.info(f"--- handle_job_error END (credential failure) for job {job_id} ---")
        return  # Stop if we can't get credentials

    message_text = f"APScheduler Job Error: {job_id} raised an exception: {exception}\nTraceback: {traceback_str}"

    logfire.info(f"Attempting to send error email for job {job_id}.")
    try:
        # Call the now asynchronous send_email function directly
        await send_email(
            to="espo412@gmail.com",  # TODO: Move to env var?
            subject=f"ALERT: APScheduler Job Error on {os.getenv('RAILWAY_ENVIRONMENT_NAME', 'unknown environment')}",
            message_text=message_text,
            credentials=credentials,
        )
        logfire.info(f"Successfully sent error notification email for job {job_id}.")

        # Add a small delay here to allow underlying I/O of send_email to complete before the test process potentially exits
        logfire.info(
            f"Adding a short delay (3s) in handle_job_error for email to finalise sending for job {job_id}."
        )
        await asyncio.sleep(3)
        logfire.info(f"Short delay completed in handle_job_error for job {job_id}.")

    except Exception:
        logfire.exception(f"Failed to send error notification email for job {job_id}")
    logfire.info(f"--- handle_job_error END for job {job_id} ---")


# Synchronous wrapper for the async error handler
def sync_error_listener_wrapper(event):
    logfire.info(
        f"--- sync_error_listener_wrapper received event for job {event.job_id}, creating task for handle_job_error ---"
    )
    asyncio.create_task(handle_job_error(event))


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
    scheduler = get_scheduler()
    slug_map = {
        "EMILIO": "emilio",
        "JACKIE": "jackie",
        "PEPPINO": "peppino",
        "ANNA": "anna",
        "SERNIA": "sernia",
    }
    contact = await get_contact_by_slug(slug_map[recipient])
    if not contact or not contact.phone_number:
        raise ValueError(f"Contact '{slug_map[recipient]}' not found or has no phone number")
    to_phone_number = contact.phone_number

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


async def schedule_email(
    subject: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    run_date: datetime,
):
    scheduler = get_scheduler()

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


def register_hello_apscheduler_jobs():
    """Register hello world test job.

    Only runs in local development. In hosted environments (Railway), the
    SQLAlchemyJobStore has a race condition where APScheduler tries to remove
    the one-time date-triggered job after execution, but the job is already
    gone - causing a JobLookupError that triggers Logfire alerts.

    This job is just for testing/demo purposes and doesn't need to run in production.
    """
    is_hosted = len(os.getenv("RAILWAY_ENVIRONMENT_NAME", "")) > 0
    if is_hosted:
        logfire.info("Skipping hello_world_apscheduler_job registration (hosted environment)")
        return

    scheduler = get_scheduler()
    upsert_job(
        scheduler,
        func=run_hello_world,
        trigger="date",
        kwargs={"name": "Emilio"},
        id="hello_world_apscheduler_job",
        run_date=datetime.now() + timedelta(seconds=30),
    )


async def schedule_push(
    title: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    run_date: datetime,
):
    scheduler = get_scheduler()
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


@logfire.instrument()
async def run_hello_world(name: str):
    logfire.info(f"Hello {name} from apscheduler run_hello_world executed at {datetime.now()}")
