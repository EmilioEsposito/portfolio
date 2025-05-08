import logging
import os
from api.src.open_phone.service import send_message
from api.src.database.database import AsyncSessionFactory
from api.src.scheduler.service import scheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
import pytest

# Create a logger specific to this module
logger = logging.getLogger(__name__)

logger.info("Zillow unreplied email alerts service loaded")

async def check_unreplied_emails(sql: str, target_phone_numbers: list[str], mock=False):
    """
    Check for unreplied Zillow emails and send a summary via OpenPhone.

    Args:
        sql: str - The path to the SQL file to use for the query (ends with .sql) OR the SQL query itself.
        mock: bool - Whether to mock the sending of the message.
        target_phone_number: str - The phone number to send the message to.
    """
    logger.info(f"check_unreplied_emails invoked. Received sql='{sql}', target_phone_numbers='{target_phone_numbers}', mock='{mock}'")

    # Configuration for phone numbers
    from_phone_number = "+14129101500"  # Alert Robot

    logger.info(
        f"Running Zillow unreplied email check. Alerts to: {target_phone_numbers}"
    )
    unreplied_count = 0  # Default value
    sent_message_count = 0

    try:
        async with AsyncSessionFactory() as session:
            # Read SQL query
            # Assuming the SQL file path is relative to the workspace root or this script's location
            # The original service.py implies it's relative to where the script is run from (api/)

            if sql.endswith(".sql"):
                logger.info(f"sql parameter '{sql}' ends with .sql, attempting to read file.")
                try:
                    with open(sql, "r") as f:
                        sql_query = f.read()
                    logger.info(f"Successfully read SQL query from file: {sql}")
                except FileNotFoundError:
                    logger.error(
                        f"SQL file not found at {sql}. Please check the path."
                    )
                    return (
                        -1
                    )  # Return -1 if SQL file is not found, test expects a count.
            else:
                sql_query = sql
                logger.info(f"sql parameter '{sql}' does not end with .sql. Using as direct query.")

            result = await session.execute(text(sql_query))
            unreplied_emails = result.fetchall()
            unreplied_count = len(unreplied_emails)

            if unreplied_emails:
                logger.info(f"Found {unreplied_count} unreplied Zillow emails.")

                formatted_results = []
                for email in unreplied_emails:
                    received_date = email[
                        0
                    ]  # Assuming structure based on cron.py logic
                    subject = email[1]
                    formatted_results.append(f"• {received_date}: {subject}")

                message_body = f"📬 Unreplied Zillow Emails 📬\n\nYou have {unreplied_count} unreplied Zillow emails:\n\n"
                message_body += "\n".join(formatted_results)
                message_body += (
                    "\n\nPlease check your email and reply to these messages."
                )

                current_env = os.getenv("RAILWAY_ENVIRONMENT_NAME", "local")
                if current_env not in ["production", "local"]:
                    logger.info(
                        f"Skipping OpenPhone message in '{current_env}' environment."
                    )
                else:
                    logger.info(
                        f"Attempting to send OpenPhone message in '{current_env}' environment."
                    )

                    for target_phone_number in target_phone_numbers:
                        if mock:
                            sent_message_count += 1
                            logger.info(f"Mock sending message to {target_phone_number}.")
                        else:
                            response = await send_message(
                                message=message_body,
                                to_phone_number=target_phone_number,
                                from_phone_number=from_phone_number,
                            )
                            if response.status_code in [200, 202]:
                                logger.info(
                                    f"Successfully sent summary of {unreplied_count} unreplied emails to {target_phone_number}"
                                )
                                sent_message_count += 1
                            else:
                                logger.error(
                                    f"Failed to send OpenPhone message to {target_phone_number}: Status {response.status_code} - {getattr(response, 'text', 'No text attribute')}"
                                )

            else:
                logger.info("No unreplied Zillow emails found.")

            return sent_message_count

    except Exception as e:
        logger.error(f"Error in check_unreplied_emails job: {str(e)}", exc_info=True)
        # Return the determined count before exception, or 0 if it occurred early
        return sent_message_count


@pytest.mark.asyncio
async def test_has_unreplied_emails():
    sql_query = """SELECT
    'testing' AS subject,
    TO_CHAR(
        CURRENT_TIMESTAMP at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str;"""
    sent_message_count = await check_unreplied_emails(
        sql=sql_query, target_phone_numbers=["+14123703550"]
    )
    assert sent_message_count == 1


@pytest.mark.asyncio
async def test_has_no_unreplied_emails():
    sql_query = """SELECT
    'testing' AS subject,
    TO_CHAR(
        CURRENT_TIMESTAMP at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str
    WHERE 1=0;"""
    sent_message_count = await check_unreplied_emails(
        sql=sql_query, target_phone_numbers=["+14123703550"]
    )
    assert sent_message_count == 0


def start_service():
    # Schedule the job to run
    scheduler.add_job(
        id="zillow_email_new_unreplied_job",
        func=check_unreplied_emails,
        kwargs={
            "sql": "api/src/zillow_unreplied_email_alerts/zillow_email_new_unreplied.sql",
            "target_phone_numbers": ["+14129101989"],
        },
        trigger=CronTrigger(hour="8,12,17", minute="0", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )

    # temp test job
    scheduler.add_job(
        id="zillow_test_job",
        func=check_unreplied_emails,
        kwargs={
            "sql": "api/src/zillow_unreplied_email_alerts/test.sql",
            "target_phone_numbers": ["+14123703550"],
        },
        trigger=CronTrigger(hour="10", minute="1", day="1", month="1", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )
