import os
import re  # Added for normalization function
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(".env.development.local"), override=True)
import requests
from typing import List, Optional, Union
from fastapi import HTTPException
import logging
from api.src.google.sheets import get_sheet_as_json
import json

# from twilio.rest import Client # removed to reduce bundle size
import pytest
from datetime import datetime
import pytz
import random
from openai import OpenAI
from pydantic import BaseModel
from pprint import pprint

logger = logging.getLogger(__name__)


# --- Twilio Configuration ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FLOW_ID = "FW708fd372ad2ccc709cdaf1565f087bfa"

if not TWILIO_ACCOUNT_SID:
    raise HTTPException(status_code=500, detail="TWILIO_ACCOUNT_SID is missing")
if not TWILIO_AUTH_TOKEN:
    raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is missing")

# twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) # removed to reduce bundle size


# Explicit keywords Escalation
explicit_keywords = [
    "urgent",
    "emergency",
    "911",
    "fire",
    "smoke",
    "explosion",
    "explode",
    "exploding",
    "flood",
    # "water",
    # "leak",
    "violent",
    "burglar",
    "robbery",
    "gun",
    "police",
    "officer",
    "ambulance",
]

negation_prefixes = [
    "notan",
    "nota",
    "not",
    "non",
]

# sort keywords by length, longest first
explicit_keywords.sort(key=len, reverse=True)
negation_phases = [
    negation_prefix + explicit_keyword
    for negation_prefix in negation_prefixes
    for explicit_keyword in explicit_keywords
]


never_escalate_from_numbers = [
    "+16266125747",
]

# --- Normalization function ---
def normalize_text_for_keyword_search(text: str) -> str:
    if not text:
        return ""
    # Lowercase and remove all non-alphanumeric characters
    text = re.sub(r"[^a-z0-9]", "", text.lower())
    # remove negation phrases
    for negation_phrase in negation_phases:
        text = text.replace(negation_phrase, "")
    return text


ai_instructions = f"""
You work for a residential property management company. 

Your job is to read incoming SMS messages from tenants, and decide if there is an URGENT issue that should be escalated to relevant parties.



Things we DO want to escalate:
* Water leaking onto floor, from ceilings, gushing out of pipes, etc. Water actively going into walls is an emergency.
* Active burglars
* Fires
* Explosions
* Exploding
* Explosion
* Active ongoing property damage
* Degenerates loitering or harassing tenants
* Active drug use or drug dealing
* Here are more example words/ideas that should be escalated: {explicit_keywords}

Here are examples of things to NOT escalate:
* A dripping faucet into the sink
* Talking about a prior incident that has obviously already been mostly mitigated already
* "I lost my keys and can't get in! Can someone bring me a spare ASAP??"
* "My power is out, can you send someone to fix it right away?"
* Low priority property damage that doesn't pose an immediate threat and won't worsen if neglected for a day or two

Please respond with a JSON object with the following fields:
* should_escalate: bool
* reason: str

"""


async def ai_assess_for_escalation(open_phone_event: dict):
    client = OpenAI()

    class ShouldEscalate(BaseModel):
        should_escalate: bool
        reason: str

    timestamp = open_phone_event.get("event_timestamp")

    # add timezone to timestamp only if it doesn't have one
    if timestamp.tzinfo is None:
        timestamp_et = pytz.timezone("US/Eastern").localize(timestamp)
    else:
        timestamp_et = timestamp

    response = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": ai_instructions},
            {
                "role": "user",
                "content": f"MESSAGE: {open_phone_event.get('message_text')}\nTIMESTAMP (ET): {timestamp_et}",
            },
        ],
        text_format=ShouldEscalate,
    )

    logger.info(
        f'AI assessment for message text "{open_phone_event.get("message_text")}": {response.output_parsed}'
    )

    should_escalate = response.output_parsed.should_escalate
    return should_escalate


async def analyze_for_twilio_escalation(
    open_phone_event: dict, escalate_to_numbers: list[str] = [], mock: bool = False
):
    """
    Analyzes an OpenPhone event and potentially triggers a Twilio Studio Flow execution.
    """

    should_escalate = False  # default to false
    successful_escalations = 0

    incident_id = random.randint(100, 999)
    event_from_number = open_phone_event.get("from_number")
    event_message_text = open_phone_event.get("message_text")
    event_id = open_phone_event.get("event_id", "")
    logger.info(f"Analyzing for Twilio escalation. OpenPhone event_id: {event_id}")

    result_message = "No result message"

    # Default flow numbers, adjust as needed or make dynamic
    escalate_from_number = ""

    if len(escalate_to_numbers) == 0:
        escalate_to_numbers = [
            "+14123703550",
            "+14126800593",
            # "+14124172322",
            # "+14123703505",
        ]

    # # 320-09 Escalation between 8pm and 7am
    # unit32009_numbers = ["+14124786168", "+14122280772"]
    # if event_from_number in unit32009_numbers and (now_et.hour >= 20 or now_et.hour <= 7):
    #     should_escalate = True
    #     escalate_to_numbers = ["+14126800593"] # Specific target for 320-09
    #     escalate_from_number = "+14129001989" # Specific sender for 320-09

    # Allow an AI Agent to assess if this should be escalated
    try:
        should_escalate = await ai_assess_for_escalation(open_phone_event)
    except Exception as e:
        logger.error(f"AI Error assessing for escalation: {e}")

    # Check for explicit keywords in the message text, just in case the AI doesn't catch it
    if not should_escalate and any(
        keyword in normalize_text_for_keyword_search(event_message_text)
        for keyword in explicit_keywords
    ):
        should_escalate = True
        logger.info(f"Explicit keyword escalation triggered. \nevent_id={event_id}\nshould_escalate={should_escalate}\nmessage_text={event_message_text}")

    escalate_from_number = "+14129001989"  # Specific sender for 320-09
    event_message_text = (
        f"URGENT! {event_from_number} said: {event_message_text}"  # Prepend identifier
    )

    # Add incident ID to the message text
    if event_message_text:
        event_message_text += f"\nIncident ID: {incident_id}"
    else:
        event_message_text = f"Escalation Triggered\nIncident ID: {incident_id}"

    # override should_escalate if the from number is in the never_escalate_from_numbers list
    if should_escalate and event_from_number in never_escalate_from_numbers:
        should_escalate = False
        logger.info(f"Event from number {event_from_number} is in the never_escalate_from_numbers list, so not escalating")

    if should_escalate:
        logger.info(
            f"Escalation triggered. INCIDENT_ID: {incident_id} for EVENT_ID {open_phone_event.get('event_id')} to numbers {escalate_to_numbers}"
        )
        try:
            # Construct the API URL
            studio_api_url = (
                f"https://studio.twilio.com/v2/Flows/{TWILIO_FLOW_ID}/Executions"
            )

            for escalate_to_number in escalate_to_numbers:

                # Prepare the payload
                payload = {
                    "To": escalate_to_number,
                    "From": escalate_from_number,
                    "Parameters": json.dumps(
                        {"message_text": event_message_text}
                    ),  # Parameters must be a JSON string
                }

                if mock:
                    result_message = f"Mocking Twilio escalation for event {open_phone_event.get('event_id')} to {escalate_to_number} with message: {event_message_text}"
                    logger.info(result_message)
                    successful_escalations += 1
                else:
                    # Make the request using Basic Auth
                    response = requests.post(
                        studio_api_url,
                        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                        data=payload,
                    )
                    response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

                    execution_data = response.json()
                    execution_sid = execution_data.get("sid")
                    result_message = f"Successfully created Twilio execution: {execution_sid} for INCIDENT_ID {incident_id} and EVENT_ID {open_phone_event.get('event_id')}"
                    logger.info(result_message)
                    successful_escalations += 1
        except requests.exceptions.RequestException as e:
            # Log the error, including the response text if available
            error_message = f"Failed to create Twilio execution for event {open_phone_event.get('event_id')}: {str(e)}"
            if e.response is not None:
                error_message += f"\nResponse status: {e.response.status_code}"
                error_message += f"\nResponse text: {e.response.text}"
            logger.error(error_message, exc_info=True)  # exc_info=True adds traceback
        except Exception as e:
            # Catch any other unexpected errors during the process
            logger.error(
                f"An unexpected error occurred during Twilio escalation for event {open_phone_event.get('event_id')}: {str(e)}",
                exc_info=True,
            )

    else:
        logger.debug(
            f"Event {open_phone_event.get('event_id')} (type: {open_phone_event.get('event_type')}) did not meet Twilio escalation criteria."
        )

    return successful_escalations


@pytest.mark.asyncio
async def test_explicit_keyword_escalation():
    """
    Test function to verify Twilio escalation functionality.
    """
    # Test data
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "fire in the building",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    successful_escalations = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(successful_escalations)
    assert successful_escalations == 1


@pytest.mark.asyncio
async def test_ai_escalation_positive():
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "There is a crazy person screaming about hurting people in the building!",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    should_escalate = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(should_escalate)

    assert should_escalate == 1


@pytest.mark.asyncio
async def test_ai_escalation_negative():
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "I lost my keys and can't get in! Can someone bring me a spare ASAP??",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    should_escalate = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(should_escalate)

    assert should_escalate == 0
