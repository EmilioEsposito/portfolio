import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(".env.development.local"), override=True)
import requests
from typing import List, Optional, Union
from fastapi import HTTPException
import logging
from api_src.google.sheets import get_sheet_as_json
import json
from twilio.rest import Client
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

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

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
    event_to_number = open_phone_event.get("to_number")
    event_message_text = open_phone_event.get("message_text")

    now_et = datetime.now(pytz.timezone('US/Eastern'))

    # Unit Test Escalation
    if event_from_number == "+14123703505":
        should_escalate = True
        flow_to_number = "+14123703550"
        flow_from_number = "+14129001989"

    # 320-09 Escalation between 8pm and 7am
    if event_from_number == "+14124786168" and (now_et.hour >= 20 or now_et.hour <= 7):
        should_escalate = True
        flow_to_number = "+14126800593"
        flow_from_number = "+14129001989"
        event_message_text = "320-09 said:" + event_message_text

    event_message_text += f"\nIncident ID: {incident_id}"

    if should_escalate:

        logging.info(f"Escalating event {open_phone_event.get('event_id')} to Twilio Flow {TWILIO_FLOW_ID} for number {flow_to_number}")
        try:
            # Make sure the client is available (check added due to potential init failure)
            if not twilio_client:
                 logging.error("Twilio client not available for escalation.")
                 raise Exception("Twilio client not available for escalation.")

            execution = twilio_client.studio.v2.flows(TWILIO_FLOW_ID).executions.create(
                to=flow_to_number,
                from_=flow_from_number,
                parameters={"message_text": event_message_text} # Optional: Pass event data to the flow if needed
            )
            logging.info(f"Successfully created Twilio execution: {execution.sid} for event {open_phone_event.get('event_id')}")
        except Exception as e:
            logging.error(f"Failed to create Twilio execution for event {open_phone_event.get('event_id')}: {str(e)}", exc_info=True)
    else:
         logging.debug(f"Event {open_phone_event.get('event_id')} (type: {open_phone_event.get('event_type')}) did not meet Twilio escalation criteria.")

    return should_escalate

@pytest.mark.asyncio
async def test_twilio_escalation():
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