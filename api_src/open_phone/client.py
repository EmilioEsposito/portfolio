import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(".env.development.local"), override=True)
import requests
from typing import List, Optional, Union
from fastapi import HTTPException
import logging
from api_src.google.sheets import get_sheet_as_json
import json
# from twilio.rest import Client # removed to reduce bundle size
import pytest
from datetime import datetime
import pytz
import random


# --- Twilio Configuration ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FLOW_ID = 'FW708fd372ad2ccc709cdaf1565f087bfa'

if not TWILIO_ACCOUNT_SID:
    raise HTTPException(status_code=500, detail="TWILIO_ACCOUNT_SID is missing")
if not TWILIO_AUTH_TOKEN:
    raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is missing")

# twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) # removed to reduce bundle size

async def send_message(
    message: str,
    to_phone_number: str,
    from_phone_number: str = "+14129101989",
):
    """
    Send a message to a phone number using the OpenPhone API.
    """
    api_key = os.getenv("OPEN_PHONE_API_KEY")
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    data = {
        "content": message,
        "from": from_phone_number,
        "to": [to_phone_number],
    }
    response = requests.post(
        "https://api.openphone.com/v1/messages", headers=headers, json=data
    )
    return response

async def get_contacts_by_external_ids(
    external_ids: List[str],
    sources: Optional[List[str]] = None,
    page_token: Optional[str] = None,
):
    """Internal function version without Query dependencies"""
    max_results = 49

    # Build query parameters
    params = {"externalIds": external_ids, "maxResults": max_results}

    if sources:
        params["sources"] = sources
    if page_token:
        params["pageToken"] = page_token

    api_key = os.getenv("OPEN_PHONE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenPhone API key not configured")

    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    try:
        response = requests.get(
            "https://api.openphone.com/v1/contacts", headers=headers, params=params
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching contacts: {str(e)}")
        raise

async def analyze_for_twilio_escalation(open_phone_event: dict):
    """
    Analyzes an OpenPhone event and potentially triggers a Twilio Studio Flow execution.
    """

    should_escalate = False # default to false

    incident_id = random.randint(100,999)
    event_from_number = open_phone_event.get("from_number")
    event_message_text = open_phone_event.get("message_text")
    event_id = open_phone_event.get("event_id","")

    now_et = datetime.now(pytz.timezone('US/Eastern'))

    # Default flow numbers, adjust as needed or make dynamic
    escalate_to_numbers = []
    escalate_from_number = ""

    # Unit Test Escalation
    if event_id == "1234567890":
        should_escalate = True
        escalate_to_numbers = [
            "+14123703550",
            # "+14128770257",
        ]
        escalate_from_number = "+14129001989" # Specific sender for test

    # # 320-09 Escalation between 8pm and 7am
    # unit32009_numbers = ["+14124786168", "+14122280772"]
    # if event_from_number in unit32009_numbers and (now_et.hour >= 20 or now_et.hour <= 7):
    #     should_escalate = True
    #     escalate_to_numbers = ["+14126800593"] # Specific target for 320-09
    #     escalate_from_number = "+14129001989" # Specific sender for 320-09

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
        "explosion",
        "water",
        "flood",
        "leak",
        "violent",
        "burglar",
        "robbery",
        "gun",
        "police",
        "officer",
        "ambulance",
    ]

    # Check for explicit keywords in the message text
    if any(keyword in event_message_text.lower() for keyword in explicit_keywords):
        should_escalate = True
        escalate_to_numbers = [
            "+14123703550",
            "+14126800593",
            # "+14124172322",
            # "+14123703505",
        ] 
        escalate_from_number = "+14129001989" # Specific sender for 320-09
        event_message_text = f"URGENT! {event_from_number} said: {event_message_text}" # Prepend identifier

    # Add incident ID to the message text
    if event_message_text:
        event_message_text += f"\nIncident ID: {incident_id}"
    else:
        event_message_text = f"Escalation Triggered\nIncident ID: {incident_id}"

    if should_escalate:
        logging.info(f"Escalating event {open_phone_event.get('event_id')} to Twilio Flow {TWILIO_FLOW_ID} for numbers {escalate_to_numbers}")
        try:
            # Construct the API URL
            studio_api_url = f"https://studio.twilio.com/v2/Flows/{TWILIO_FLOW_ID}/Executions"


            for escalate_to_number in escalate_to_numbers:

                # Prepare the payload
                payload = {
                    'To': escalate_to_number,
                    'From': escalate_from_number,
                    'Parameters': json.dumps({"message_text": event_message_text}) # Parameters must be a JSON string
                }

                # Make the request using Basic Auth
                response = requests.post(
                    studio_api_url,
                    auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                    data=payload
                )
                response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

                execution_data = response.json()
                execution_sid = execution_data.get('sid')
                logging.info(f"Successfully created Twilio execution: {execution_sid} for event {open_phone_event.get('event_id')}")

        except requests.exceptions.RequestException as e:
             # Log the error, including the response text if available
            error_message = f"Failed to create Twilio execution for event {open_phone_event.get('event_id')}: {str(e)}"
            if e.response is not None:
                error_message += f"\nResponse status: {e.response.status_code}"
                error_message += f"\nResponse text: {e.response.text}"
            logging.error(error_message, exc_info=True) # exc_info=True adds traceback
        except Exception as e:
            # Catch any other unexpected errors during the process
             logging.error(f"An unexpected error occurred during Twilio escalation for event {open_phone_event.get('event_id')}: {str(e)}", exc_info=True)

    else:
         logging.debug(f"Event {open_phone_event.get('event_id')} (type: {open_phone_event.get('event_type')}) did not meet Twilio escalation criteria.")

    return should_escalate

@pytest.mark.asyncio
async def test_twilio_escalation_unit_test():
    """
    Test function to verify Twilio escalation functionality.
    """
    # Test data
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "Hello, this is a test message.",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
    }
    await analyze_for_twilio_escalation(open_phone_event)


    

def get_contacts_sheet_as_json():
    spreadsheet_id = "1Gi0Wrkwm-gfCnAxycuTzHMjdebkB5cDt8wwimdYOr_M"
    return get_sheet_as_json(spreadsheet_id, sheet_name="OpenPhone") 