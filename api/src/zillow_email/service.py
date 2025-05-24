import logging
import os
from pprint import pprint
from api.src.open_phone.service import send_message, upsert_openphone_contact
from api.src.database.database import AsyncSessionFactory
from api.src.scheduler.service import scheduler
from api.src.contact.service import ContactCreate
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
import pytest
from api.src.contact.service import get_contact_by_slug
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime, timedelta
import openai
import pytz
from api.src.google.calendar.service import create_calendar_event, get_calendar_service
from bs4 import BeautifulSoup

# Create a logger specific to this module
logger = logging.getLogger(__name__)

logger.info("Zillow unreplied email alerts service loaded")

async def check_unreplied_emails(sql: str, target_phone_numbers: list[str]=None, target_slugs: list[str]=None, mock=False):
    """
    Check for unreplied Zillow emails and send a summary via OpenPhone.

    Args:
        sql: str - The path to the SQL file to use for the query (ends with .sql) OR the SQL query itself.
        target_phone_numbers: list[str] - The phone numbers to send the message to.
        target_slugs: list[str] - The slugs of the contacts to send the message to.
        mock: bool - Whether to mock the sending of the message.
    """
    logger.info(f"check_unreplied_emails invoked. Received sql='{sql}'")
    assert target_phone_numbers or target_slugs, "Either target_phone_numbers or target_slugs must be provided"
    logger.info(f"target_phone_numbers='{target_phone_numbers}'")
    logger.info(f"target_slugs='{target_slugs}'")
    logger.info(f"mock='{mock}'")

    if target_slugs:
        target_phone_numbers = []
        for target_slug in target_slugs:
            target_contact = await get_contact_by_slug(target_slug)
            if not target_contact:
                logger.error(f"Contact with slug '{target_slug}' not found.")
                raise Exception(f"Contact with slug '{target_slug}' not found.")
            else:
                target_phone_numbers.append(target_contact.phone_number)

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
        sql=sql_query, target_slugs=["emilio"]
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
        sql=sql_query, target_slugs=["emilio"]
    )
    assert sent_message_count == 0

# Define Pydantic models for better structure
class EmailMessageDetail(BaseModel):
    subject: str
    email_timestamp_et: datetime # Assuming it's a datetime object after DB conversion
    email_day_of_week_et_str: str
    body_html: str
    body_text: str = None
    from_address: EmailStr # Or str if not strictly email
    # Assuming to_address from your SQL might be a single string or needs parsing to a list.
    # For simplicity, keeping as string for now. Adjust if it's an array/list in DB.
    to_address: Optional[str] = None 
    # Add other relevant fields from your email_data if needed

class ShouldReply(BaseModel):
    should_reply: bool
    reason: str
    appointment_scheduled: bool

def get_clean_zillow_thread_str(messages: List[EmailMessageDetail]):

    # Cleanup
    for message in messages:
        # remove redundant replies in each message
        message.body_text = message.body_html.split(">On")[0]+">"
        # remove the redundant text after "New messageHurrah!" in zillow emails
        message.body_text = message.body_text.split("New messageHurrah!")[0]
        # remove html tags from body_html
        message.body_text = BeautifulSoup(message.body_text, "html.parser").get_text()

    thread_str = ""
    for message in messages:
        thread_str += f"FROM: {message.from_address}\n"
        thread_str += f"TO: {message.to_address}\n"
        thread_str += f"RECEIVED DATE (ET): {message.email_timestamp_et.strftime('%Y-%m-%d %H:%M:%S')}\n"
        thread_str += f"DAY OF WEEK: {message.email_day_of_week_et_str}\n"
        thread_str += f"SUBJECT: {message.subject}\n"
        thread_str += f"BODY: {message.body_text}\n"
        thread_str += "----------------------------------------\n"

    return thread_str

async def ai_assess_thread(thread_id: str, messages: List[EmailMessageDetail]):

    ai_instructions = """
    # Context
    You are a helpful assistant that works for Sernia Capital Property Management (all@serniacapital.com). 
    
    Note: Any email from all@serniacapital.com is considered a response from "Sernia". all@serniacapital.com 
     is a shared email account. Jackie responds on behalf of Sernia the most, but other team members may respond as well.
     The first email in the thread is always from the lead.

    # Task
    You are tasked with assessing whether Sernia should reply or follow up to 
     a given Zillow email thread. These are threads where potential tenants (leads) are interested in renting 
     one of our properties. The goal of each email thread is to vet if the lead is "qualified", 
     and if they are qualified, to schedule an appointment to view the property (and collect their 
     phone number).

    # Guidelines:
    * No need to reply or follow up further if the ball is fully in the lead's court and they are not responding.
    * No need to reply if an appointment is acknowledged by both parties and phone number is at least requested.
    * No need to reply if it is confirmed that the lead is not qualified for any reason 
    * Credit: If Zillow profile says credit score is below 600, never a need to reply, lead is definitely not qualified. 
       Credit over 670 is qualified. Scores in between are case by case. No score is fine. 
    * Pets: We do not allow dogs. We allow cats. Other pets are case-by-case. If their Zillow profile says
       they have pets, we should reply asking for clarification to see if they are qualified.
    * Availability: If there is material potential mismatch on move-in date vs availability, it is 
       sometimes worth clarifying if they have flexibility.
    * If Sernia already replied, and the applicant never responds, Sernia does not need to reply again.
    * However, even if Sernia was the last one to reply, if the thread implied there was a follow-up required from Sernia, 
      Sernia should reply again (e.g. if last message from Sernia was ""we'll get back to you on that question..."")
    * No need to reply if it seems like the lead is simply using the thread to let Sernia know they are physically at the 
       property for their appointment (at that point Sernia usuually just calls them to find them). 
    * When giving your reasoning, speak in the "we" voice, since you work for Sernia as well. 

    # Response Format. Return your response in JSON. 
    # Examples:
    {
        "should_reply": false,
        "reason": "Reasoning for your response",
        "appointment_scheduled": true
    }
    {
        "should_reply": true,
        "reason": "Reasoning for your response",
        "appointment_scheduled": false
    }
    """

    logger.info(f"ai_assess_thread: Assessing Zillow email thread: {thread_id}")

    thread_str = get_clean_zillow_thread_str(messages)

    client = openai.OpenAI()
    response = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": ai_instructions},
            {
                "role": "user",
                "content": f"EMAIL THREAD: {thread_str}",
            },
        ],
        text_format=ShouldReply,
    )

    logger.info(f"ai_assess_thread: Parsed AI response: {response.output_parsed}")

    should_sernia_reply = response.output_parsed.should_reply
    reason = response.output_parsed.reason
    appointment_scheduled = response.output_parsed.appointment_scheduled
    return should_sernia_reply, reason, appointment_scheduled


class CollectedThreadInfo(BaseModel):
    building_number: Optional[int] = None
    unit_number: Optional[int] = None
    lead_first_name: Optional[str] = None
    lead_last_name: Optional[str] = None
    lead_phone_number: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None


async def ai_collect_thread_info(thread_id: str, messages: List[EmailMessageDetail]):

    ai_instructions = f"""# Context
    You are a helpful assistant that works for Sernia Capital Property Management (all@serniacapital.com). 
    
    Note: Any email from all@serniacapital.com is considered a response from "Sernia". all@serniacapital.com 
     is a shared email account. Jackie responds on behalf of Sernia the most, but other team members may respond as well. 
     The first email in the thread is always from the lead.

    # Task
    You are tasked with collecting the lead's information, property information, and final confirmed appointment date and time 
     from a Zillow email thread.

    # Guidelines:
    * The first email in the thread is always from the lead.
    * The property information is always in the subject line. 
        * The building_number is just the 3 or 4 digit number preceding the street name.
        * The unit_number is the number after the street name and before the city. 
            * It might be diplayed in these sorts of varying formats: #1, Apt 1, Unit 01, etc. Return just the number, in this case "1".
    * The lead's first and last name are in the body of the first email, and possibly also in the "from" field of the first email.
    * Do not confuse appointment time *options* with the final confirmed appointment date and time. 
    * Apointment Date: In cases where the confirmed appointment is given as a day of week, you will need to figure out the implied calendar date vs the date of the email message.
    * Appointment Time: Assume everything is in ET timezone, and do not do any timezone conversion. Return the time in ET timezone.

    # Response Format. Return your response in JSON. 
    # Examples:
    {{
        "building_number": 332,
        "unit_number": 1,
        "lead_first_name": "John",
        "lead_last_name": "Doe",
        "lead_phone_number": "+14125551212",
        "appointment_date": "2024-01-01",
        "appointment_time": "10:00 AM"
    }}
    """

    logger.info(f"ai_collect_thread_info: Collecting lead info from Zillow email thread: {thread_id}")



    
    thread_str = get_clean_zillow_thread_str(messages)

    client = openai.OpenAI()
    response = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": ai_instructions},
            {
                "role": "user",
                "content": f"EMAIL THREAD:\n{thread_str}",
            },
        ],
        text_format=CollectedThreadInfo,
    )

    logger.info(f"ai_collect_thread_info: Parsed AI response: {response.output_parsed}")

    thread_info = response.output_parsed

    return thread_info, thread_str

async def check_email_threads(overwrite_calendar_events=False):

    async with AsyncSessionFactory() as session:

        if os.getenv("RAILWAY_ENVIRONMENT_NAME") == "production":
            target_contact = await get_contact_by_slug("sernia")
            non_prod_env = None
        else:
            target_contact = await get_contact_by_slug("emilio")
            non_prod_env = os.getenv("RAILWAY_ENVIRONMENT_NAME", "local")


        target_phone_number = target_contact.phone_number

        should_sernia_reply = False
        with open("api/src/zillow_email/zillow_email_threads.sql", "r") as f:
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

                    email_day_of_week_et_str = email['email_timestamp_et'].strftime('%A')

                    message_detail = {
                        "subject": email['subject'],
                        "email_timestamp_et": email['email_timestamp_et'],
                        "email_day_of_week_et_str": email_day_of_week_et_str,
                        "body_html": email['body_html'],
                        "from_address": email['from_address'],
                        "to_address": email['to_address']
                    }

                    if thread_id not in email_threads:
                        email_threads[thread_id] = []

                    message_detail = EmailMessageDetail(**message_detail)

                    email_threads[thread_id].append(message_detail)

            else:
                logger.info("No email threads found.")

            # for each thread, check if it has a reply
            for thread_id, messages in email_threads.items():
                should_sernia_reply, reason, appointment_scheduled = await ai_assess_thread(thread_id, messages)

                if should_sernia_reply:
                    alert_message = f'📬 Sernia-AI detected unreplied Zillow email 📬'
                    alert_message += f'\n\nSubject: {messages[0].subject}'
                    alert_message += f'\n\nReason: {reason}'

                    if non_prod_env:
                        alert_message = f'ENV: {non_prod_env}\n\n{alert_message}'

                    logger.info(alert_message)
                    await send_message(
                        message=alert_message,
                        to_phone_number=target_phone_number,
                        from_phone_number="+14129101500"
                    )

                if appointment_scheduled:
                    logger.info(f"Appointment scheduled: {appointment_scheduled}")

                    thread_info, thread_str = await ai_collect_thread_info(thread_id, messages)

                    # pad unit_number with leading zeros
                    unit_number_padded = str(thread_info.unit_number).zfill(2)

                    first_name_aux = 'Lead ' + str(thread_info.building_number) + "-" + unit_number_padded + " " + thread_info.lead_first_name

                    try:
                        if thread_info.lead_phone_number:
                            contact_create = ContactCreate(
                                phone_number=thread_info.lead_phone_number,
                                first_name=first_name_aux,
                                last_name=thread_info.lead_last_name,
                                company=str(thread_info.building_number),
                                role="Lead",
                            )

                            # now create a new contact in OpenPhone
                            openphone_contact_response = await upsert_openphone_contact(contact_create)
                            openphone_contact = openphone_contact_response.json()
                            logger.info(f"Created/updated OpenPhone contact: {openphone_contact}")
                        else:
                            logger.error(f"No phone number found for lead: {first_name_aux}")
                    except Exception as e:
                        logger.error(f"Error creating OpenPhone contact: {e}")
                        logger.error(f"Contact create: {contact_create}")

                    try:
                        if thread_info.appointment_date and thread_info.appointment_time:
                            # Combine date and time strings and parse them
                            appointment_datetime_str = f"{thread_info.appointment_date} {thread_info.appointment_time}"
                            # Assuming appointment_time is like "10:00 AM" or "2:00 PM"
                            # Convert to 24-hour format for parsing if necessary, or ensure consistent format
                            # For simplicity, assuming it's parsable directly or already in a good format from AI

                            # Define the timezone
                            eastern_tz = pytz.timezone("US/Eastern")

                            # Parse the combined string. This might need adjustment based on the exact format of appointment_time
                            # Example: if time is "10:00 AM", datetime.strptime can handle it with "%Y-%m-%d %I:%M %p"
                            # If AI guarantees "YYYY-MM-DD" for date and "HH:MM" (24hr) for time, it's simpler.
                            # Let's assume AI provides date as "YYYY-MM-DD" and time as "HH:MM AM/PM"

                            parsed_datetime = datetime.strptime(appointment_datetime_str, "%Y-%m-%d %I:%M %p")

                            # Localize the naive datetime to Eastern Time
                            start_datetime_aware = eastern_tz.localize(parsed_datetime)
                            end_datetime_aware = start_datetime_aware + timedelta(minutes=30) # Assuming 30-minute appointments

                            start_time_iso = start_datetime_aware.isoformat()
                            end_time_iso = end_datetime_aware.isoformat()

                            event_summary = f"{thread_info.building_number}-{unit_number_padded} Apt Viewing for Lead: {thread_info.lead_first_name} {thread_info.lead_last_name or ''}"
                            if non_prod_env:
                                event_summary = f"{non_prod_env} - {event_summary}"
                            event_description = f"Building: {thread_info.building_number}"
                            event_description += f"\nUnit: {unit_number_padded}"
                            event_description += f"\nName: {thread_info.lead_first_name} {thread_info.lead_last_name or ''}"
                            event_description += f"\nPhone: {thread_info.lead_phone_number or 'N/A'}"
                            event_description += f"\nSource: Zillow Email."
                            event_description += f"\n\nEMAIL THREAD:\n{thread_str}"

                            calendar_service = await get_calendar_service(user_email=target_contact.email) # TODO: make user_email dynamic or from config

                            event_body = {
                                "summary": event_summary,
                                "description": event_description,
                                "start": {"dateTime": start_time_iso, "timeZone": "America/New_York"},
                                "end": {"dateTime": end_time_iso, "timeZone": "America/New_York"},
                                "attendees": [
                                    {"email": "emilio+listings@serniacapital.com"} 
                                    # Potentially add lead's email if available and desired?
                                ],
                                "reminders": {
                                    "useDefault": False,
                                    "overrides": [
                                        {"method": "email", "minutes": 24 * 60}, # 1 day before
                                        {"method": "popup", "minutes": 120}, # 2 hours before
                                    ],
                                }
                            }

                            if os.getenv("RAILWAY_ENVIRONMENT_NAME", "local") in ["production", "local"]:
                                created_event = await create_calendar_event(calendar_service, event_body, overwrite=overwrite_calendar_events)
                                logger.info(f"Successfully created Google Calendar event: {created_event.get('id')}")
                            else:
                                logger.info(f"Skipping Google Calendar event creation in hosted non-production environment.")
                        else:
                            logger.warning(f"Cannot create calendar event for thread {thread_id} due to missing appointment date/time. Thread Info: {thread_info}")
                    except Exception as e:
                        logger.error(f"Error creating Google Calendar event for thread {thread_id}: {e}")
                        logger.error(f"Thread Info for calendar event creation: {thread_info}")


@pytest.mark.asyncio
async def test_check_email_threads():
    await check_email_threads(overwrite_calendar_events=True)


async def start_service():
    # test job
    scheduler.add_job(
        id="zillow_test_job",
        func=check_unreplied_emails,
        kwargs={
            "sql": "api/src/zillow_email/test.sql",
            "target_slugs": ["emilio"],
        },
        trigger=CronTrigger(hour="10", minute="1", day="1", month="1", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )

    # Schedule the job to run
    scheduler.add_job(
        id="zillow_email_new_unreplied_job",
        func=check_unreplied_emails,
        kwargs={
            "sql": "api/src/zillow_email/zillow_email_new_unreplied.sql",
            "target_slugs": ["sernia"],
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
        trigger=CronTrigger(hour="8,17", minute="0", timezone="US/Eastern"),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300
    )
