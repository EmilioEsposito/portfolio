from dotenv import load_dotenv, find_dotenv

from api.src.google.common.service_account_auth import get_delegated_credentials

load_dotenv(find_dotenv(".env.development.local"), override=True)
import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from datetime import datetime, timedelta, timezone
from api.src.push.service import send_push_to_user
import asyncio
from api.src.open_phone.service import send_message
from api.src.google.gmail.service import send_email
from api.src.push.service import send_push_to_user
from pydantic import BaseModel
from typing import Literal
import pytest
import pytz

# Import the synchronous engine from database.py
from api.src.database.database import sync_engine

logger = logging.getLogger(__name__)

# Configure the job store
if sync_engine:
    logger.info(
        "Scheduler using SQLAlchemyJobStore with pre-configured synchronous engine."
    )
    jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine)}
else:
    raise Exception(
        "Synchronous engine not available. Scheduler cannot be initialized."
    )

# index.py will handle the start/shutdown of this scheduler instance
scheduler = AsyncIOScheduler(jobstores=jobstores)

# Ensure APScheduler's own logging is not too verbose if not desired
aps_logger = logging.getLogger("apscheduler")
aps_logger.setLevel(logging.INFO)  # Or WARNING/ERROR, depending on desired verbosity


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
    phone_numbers = {
        "EMILIO": "+14123703550",
        "JACKIE": "+14123703505",
        "PEPPINO": "+14126800593",
        "ANNA": "+14124172322",
        "SERNIA": "+14129101989",
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
        logger.error(f"Error scheduling SMS: {e}")
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

    # send_email(
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
        logger.error(f"Error scheduling email: {e}")
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
        logger.error(f"Error scheduling push: {e}")
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
    logger.info(f"logger: Hello {name} from test_job executed at {datetime.now()}")


def test_job():
    # This function demonstrates adding a job and running the scheduler directly.
    # In the main app, scheduler.start() and scheduler.shutdown() are called by lifespan events.
    print("test_job")

    async def main_test_logic():  # Make it async to use await for scheduler methods
        # Start the scheduler if it's not already running (e.g. when running this script directly)
        if not scheduler.running:
            logger.info("Starting scheduler for test_job...")
            scheduler.start()
        else:
            logger.info("Scheduler already running for test_job.")

        job_id = "hello_world_test_job"
        run_date = datetime.now() + timedelta(seconds=5)  # Shortened for faster test
        logger.info(f"Adding job '{job_id}' to run at {run_date}")

        scheduler.add_job(
            func=run_hello_world,
            trigger="date",
            kwargs={"name": "Emilio"},
            id=job_id,  # Use id instead of job_id for add_job method
            run_date=run_date,  # Pass run_date directly for date trigger
            replace_existing=True,
        )

        job = scheduler.get_job(job_id=job_id)
        logger.info(f"Job added: {job}")

        jobs = scheduler.get_jobs()
        logger.info(f"Jobs: {jobs}")

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
        logger.info("Shutting down scheduler after test_job...")
        scheduler.shutdown()

    import asyncio

    asyncio.run(main_test_logic())
