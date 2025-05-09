import logging
import os
from pprint import pprint
from api.src.open_phone.service import send_message
from api.src.database.database import AsyncSessionFactory
from api.src.scheduler.service import scheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
import pytest
from api.src.contact.service import get_contact_by_slug
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime
import openai

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

# Define Pydantic models for better structure
class EmailMessageDetail(BaseModel):
    subject: str
    email_timestamp_et: datetime # Assuming it's a datetime object after DB conversion
    body_html: str
    from_address: EmailStr # Or str if not strictly email
    # Assuming to_address from your SQL might be a single string or needs parsing to a list.
    # For simplicity, keeping as string for now. Adjust if it's an array/list in DB.
    to_address: Optional[str] = None 
    # Add other relevant fields from your email_data if needed

class ShouldReply(BaseModel):
    should_reply: bool
    reason: str

async def as_assess_thread(thread_id: str, messages: List[EmailMessageDetail]):

    ai_instructions = """
    You are a helpful assistant that works for Sernia Capital Property Management (all@serniacapital.com).
    You are tasked with assessing whether Sernia Capital Property Management should reply or follow up to a given Zillow email thread. These are threads where potential
     tenants are interested in renting one of our properties. The goal of each email thread is to 
     vet that applicants are qualified leads, and if they are qualified, to schedule an appointment
     to view the property (and collect their phone number).

    No need to reply if an appointment is acknowledged by both parties and phone number is collected.
    No need to replay if it is confirmed that the lead is not qualified (e.g. irreconcilable mismatch of move-in date vs availability, etc.)

    If Zillow profile says credit score is below 600, never a need to reply.
    If there is a potential mismatch on move-in date vs availability, it is sometimes worth clarifying if they have flexibility.

    Even if Sernia was the last one to reply, if the thread required a follow-up from Sernia, Sernia should reply.
    Even if Sernia was the last one to reply, if the applicant seemed otherwise qualified, Sernia should reply. Your reasoning should be "Maybe we should reply". 

    When giving your reasoning, speak in the "we" voice, since you work for Sernia as well. 

    Return your response in the following JSON format:
    {
        "should_reply": true,
        "reason": "Reasoning for your response"
    }
    """

    logger.info(f"AI assessing Zillow email thread: {thread_id}")

    client = openai.OpenAI()
    response = client.responses.parse(
        model="gpt-4o-2024-08-06",
        input=[
            {"role": "system", "content": ai_instructions},
            {
                "role": "user",
                "content": f"EMAIL THREAD: {messages}",
            },
        ],
        text_format=ShouldReply,
    )

    pprint(response.output_parsed)

    should_sernia_reply = response.output_parsed.should_reply
    reason = response.output_parsed.reason
    return should_sernia_reply, reason

async def check_email_threads():
    
    async with AsyncSessionFactory() as session:
        sernia_contact = await get_contact_by_slug("sernia")

        should_sernia_reply = False
        with open("api/src/zillow_unreplied_email_alerts/zillow_email_threads.sql", "r") as f:
            sql_query = f.read()

            result = await session.execute(text(sql_query))
            email_rows = result.fetchall()
            email_dicts = [dict(row._mapping) for row in email_rows if email_rows]
            
            # group emails by thread_id
            email_threads = {}

            if email_dicts:
                logger.info(f"Found {len(email_dicts)} email threads.")

                for email in email_dicts:
                    thread_id = email['thread_id']
                    
                    message_detail = {
                        "subject": email['subject'],
                        "email_timestamp_et": email['email_timestamp_et'],
                        "body_html": email['body_html'],
                        "from_address": email['from_address'],
                        "to_address": email['to_address']
                    }

                    if thread_id not in email_threads:
                        email_threads[thread_id] = []

                    email_threads[thread_id].append(message_detail)
                    
            else:
                logger.info("No email threads found.")

            # for each thread, check if it has a reply
            for thread_id, messages in email_threads.items():
                should_sernia_reply, reason = await as_assess_thread(thread_id, messages)

                if should_sernia_reply:
                    alert_message = f'📬 Sernia-AI detected unreplied Zillow email 📬'
                    alert_message += f'\n\nSubject: {messages[0]["subject"]}'
                    alert_message += f'\n\nReason: {reason}'
                    logger.info(alert_message)
                    await send_message(
                        message=alert_message,
                        to_phone_number=sernia_contact.phone_number,
                        from_phone_number="+14129101500"
                    )


async def start_service():
    # test job
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

    # Schedule the job to run
    sernia_contact = await get_contact_by_slug("sernia")
    scheduler.add_job(
        id="zillow_email_new_unreplied_job",
        func=check_unreplied_emails,
        kwargs={
            "sql": "api/src/zillow_unreplied_email_alerts/zillow_email_new_unreplied.sql",
            "target_phone_numbers": [sernia_contact.phone_number],
        },
        trigger=CronTrigger(hour="8,12,17", minute="0", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )

    # Schedule AI job to run
    scheduler.add_job(
        id="zillow_email_threads_ai",
        func=check_email_threads,
        trigger=CronTrigger(hour="8,12,17", minute="0", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )
