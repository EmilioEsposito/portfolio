from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(".env.development.local"), override=True)
import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from datetime import datetime, timedelta
from api.src.push.service import send_notification_to_user
import asyncio

# Import the synchronous engine from database.py
from api.src.database.database import sync_engine

logger = logging.getLogger(__name__)

# Configure the job store
if sync_engine:
    logger.info("Scheduler using SQLAlchemyJobStore with pre-configured synchronous engine.")
    jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine)}
else:
    raise Exception("Synchronous engine not available. Scheduler cannot be initialized.")

# index.py will handle the start/shutdown of this scheduler instance
scheduler = AsyncIOScheduler(jobstores=jobstores)

# Ensure APScheduler's own logging is not too verbose if not desired
aps_logger = logging.getLogger('apscheduler')
aps_logger.setLevel(logging.INFO) # Or WARNING/ERROR, depending on desired verbosity


# EXAMPLES

# scheduler.add_job(
#     id="send_notification_to_user_job",
#     func=send_notification_to_user,
#     kwargs={
#         "email": "emilio@serniacapital.com",
#         "title": "APScheduler Test Notification",
#         "body": "This is a test notification from the APScheduler.",
#         "data": {"test": True}
#     },
#     trigger="interval",
#     seconds=30,
#     replace_existing=True
# )


# scheduler.add_job(
#     id="one_time_job",
#     func=send_notification_to_user,
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

async def run_hello_world():
    print(f"print: Hello World from test_job executed at {datetime.now()}")
    logger.info(f"logger: Hello World from test_job executed at {datetime.now()}")


def test_job():
    # This function demonstrates adding a job and running the scheduler directly.
    # In the main app, scheduler.start() and scheduler.shutdown() are called by lifespan events.
    print("test_job")

    async def main_test_logic(): # Make it async to use await for scheduler methods
        # Start the scheduler if it's not already running (e.g. when running this script directly)
        if not scheduler.running:
            logger.info("Starting scheduler for test_job...")
            scheduler.start()
        else:
            logger.info("Scheduler already running for test_job.")

        job_id = "hello_world_test_job"
        run_date = datetime.now() + timedelta(seconds=5) # Shortened for faster test
        logger.info(f"Adding job '{job_id}' to run at {run_date}")

        scheduler.add_job(
            run_hello_world,
            "date",
            id=job_id, # Use id instead of job_id for add_job method
            run_date=run_date, # Pass run_date directly for date trigger
            replace_existing=True
        )

        # Wait for the job to run
        # Giving a bit more time than the scheduled time
        await asyncio.sleep(10)

        # Shutdown the scheduler if it was started by this test logic
        # In a real app, lifespan events handle this.
        # For a standalone test, it depends on whether you want to test shutdown too.
        logger.info("Shutting down scheduler after test_job...")
        scheduler.shutdown()

    import asyncio
    asyncio.run(main_test_logic())

