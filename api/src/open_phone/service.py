import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(".env.development.local"), override=True)
import requests
from typing import List, Optional, Union
from fastapi import HTTPException
import logging
from api.src.google.sheets import get_sheet_as_json
from api.src.contact.service import get_contact_by_slug, create_contact, ContactCreate
from api.src.database.database import AsyncSessionFactory
from api.src.contact.models import Contact
import pytest
from sqlalchemy import select

logger = logging.getLogger(__name__)

async def send_message(
    message: str,
    to_phone_number: str,
    from_phone_number: Union[str, None] = None
):
    """
    Send a message to a phone number using the OpenPhone API.
    """
    if from_phone_number is None:
        sernia_contact = await get_contact_by_slug("sernia")
        from_phone_number = sernia_contact.phone_number

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

async def create_openphone_contact(contact_create: ContactCreate):
    api_key = os.getenv("OPEN_PHONE_API_KEY")
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    async with AsyncSessionFactory() as db:   
        # first, check if the contact already exists in our database
        # first check via slug if it exists
        if contact_create.slug:
            stmt = select(Contact).where(Contact.slug == contact_create.slug)
            result = await db.execute(stmt)
            contact = result.scalars().first()
        else:
            # check via phone number
            stmt = select(Contact).where(Contact.phone_number == contact_create.phone_number)
            result = await db.execute(stmt)
            contact = result.scalars().first()
        
        if not contact:
            # create a new contact
            contact = await create_contact(db, contact_create)
        else:
            # update the contact
            for key, value in contact_create.model_dump().items():
                setattr(contact, key, value)
            contact = await db.merge(contact)

        data = {
            "defaultFields": {
                "company": contact.company,
                "emails": [{"name": " Email", "value": contact.email}],
                "firstName": contact.first_name,
                "lastName": contact.last_name,
                "phoneNumbers": [{"name": "Phone", "value": contact.phone_number}],
                "role": contact.role,
            },
            "createdByUserId": "USXAiFJxgv",  # Emilio
            "source": "API-Emilio",
            "externalId": "api" + contact_create.phone_number[-10:],  # "e" + contact["Phone Number"],   # contact["external_id"]
        }
        
        response = requests.post(
            "https://api.openphone.com/v1/contacts", headers=headers, json=data
        )

        if response.status_code == 201:
            contact.openphone_contact_id = response.json()['data']['id']
            contact.openphone_json = response.json()['data']
            final_response = response
        else:
            # lookup contact by externalId
            lookup_response = requests.get(
                "https://api.openphone.com/v1/contacts", headers=headers, params={"externalIds": [data["externalId"]]}
            )
            lookup_results = lookup_response.json()['data']
            num_results = len(lookup_results)
            if num_results>1:
                logger.warning(f"Multiple contacts found for the same externalId: {data['externalId']}")
            contact.openphone_contact_id = lookup_results[0]['id']

            # patch the contact
            patch_response = requests.patch(
                f"https://api.openphone.com/v1/contacts/{contact.openphone_contact_id}", headers=headers, json=data
            )
            contact.openphone_json = patch_response.json()['data']
            if patch_response.status_code == 200:
                final_response = patch_response
            else:
                logger.error(f"Failed to patch contact: {patch_response.json()}")
            
        # Use merge instead of upsert
        merged_contact = await db.merge(contact)
        await db.commit()
        await db.refresh(merged_contact)
        
    return final_response

@pytest.mark.asyncio
async def test_create_contact():
    contact_create = ContactCreate(
        slug="test-lead-contact-random",
        phone_number="+19291231234",
        first_name="Test First",
        last_name="Test Last",
        email="test@test.com",
        notes="API-Test",
        company="Test",
        role="Test",
    )

    response = await create_openphone_contact(contact_create)
    print(response.json())
    assert response.status_code == 201 or response.status_code == 200


@pytest.mark.asyncio
async def test_send_message():
    response = await send_message(
        message="Hello, this is a test message",
        to_phone_number="+14123703550",
        from_phone_number="+14129101500",
    )
    print(response.json())
    assert response.status_code == 202

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
        logger.error(f"Error fetching contacts: {str(e)}")
        raise


def get_contacts_sheet_as_json():
    spreadsheet_id = "1Gi0Wrkwm-gfCnAxycuTzHMjdebkB5cDt8wwimdYOr_M"
    return get_sheet_as_json(spreadsheet_id, sheet_name="OpenPhone") 