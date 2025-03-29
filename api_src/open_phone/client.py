import os
import requests
from typing import List, Optional, Union
from fastapi import HTTPException
import logging
from api_src.google.sheets import get_sheet_as_json

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

def get_contacts_sheet_as_json():
    spreadsheet_id = "1Gi0Wrkwm-gfCnAxycuTzHMjdebkB5cDt8wwimdYOr_M"
    return get_sheet_as_json(spreadsheet_id, sheet_name="OpenPhone") 