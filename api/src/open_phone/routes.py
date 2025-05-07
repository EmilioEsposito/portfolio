from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
import logging
import json
import os
import base64
import hmac
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Union
from pydantic import BaseModel
from api.src.database.database import get_session
from api.src.open_phone.models import OpenPhoneEvent
from api.src.open_phone.schema import OpenPhoneWebhookPayload
from api.src.open_phone.service import send_message, get_contacts_by_external_ids, get_contacts_sheet_as_json
from api.src.open_phone.escalate import analyze_for_twilio_escalation
from api.src.utils.password import verify_admin_auth
import asyncio
from datetime import datetime, date
from sqlalchemy.exc import IntegrityError
from pprint import pprint
import time
import requests
from api.src.utils.dependencies import verify_serniacapital_user, verify_admin_or_serniacapital

router = APIRouter(
    prefix="/open_phone",
    tags=["open_phone"],
)

async def verify_open_phone_signature(request: Request):
    # signing_key = os.getenv(env_var_name)
    signing_key = os.getenv("OPEN_PHONE_WEBHOOK_SECRET")
    if not signing_key:
        raise HTTPException(403, "OPEN_PHONE_WEBHOOK_SECRET not configured")
    data = await request.body()
    # Parse the fields from the openphone-signature header.
    signature = request.headers["openphone-signature"]
    fields = signature.split(";")
    timestamp = fields[2]
    provided_digest = fields[3]

    # Compute the data covered by the signature as bytes.
    signed_data_bytes = b"".join([timestamp.encode(), b".", data])

    # Convert the base64-encoded signing key to bytes.
    signing_key_bytes = base64.b64decode(signing_key)

    # Compute the SHA256 HMAC digest.
    # Obtain the digest in base64-encoded form for easy comparison with
    # the digest provided in the openphone-signature header.
    hmac_object = hmac.new(signing_key_bytes, signed_data_bytes, "sha256")
    computed_digest = base64.b64encode(hmac_object.digest()).decode()

    # Make sure the computed digest matches the digest in the openphone header.
    if provided_digest == computed_digest:
        logging.info("signature verification succeeded")
        return True
    else:
        logging.error("signature verification failed")
        raise HTTPException(403, "Signature verification failed")

def extract_event_data(payload: OpenPhoneWebhookPayload) -> dict:
    """Extract relevant fields from the event data based on event type"""
    # Convert the payload to dict first
    payload_dict = payload.dict()
    
    # Convert any datetime objects to ISO format strings in the payload_dict
    def convert_datetime_to_str(obj):
        if isinstance(obj, dict):
            return {k: convert_datetime_to_str(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_datetime_to_str(item) for item in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj
    
    # Apply the conversion to the entire payload dict
    payload_dict = convert_datetime_to_str(payload_dict)
    
    event_data = {
        "event_type": payload.type,
        "event_id": payload.id,
        "event_data": payload_dict,  # Use the converted dict
        "event_timestamp": payload.createdAt,  # Changed from created_at to event_timestamp
    }
    
    # Extract common fields
    data_object = payload.data.object
    event_data.update({
        "conversation_id": data_object.conversationId,
        "user_id": data_object.userId,
        "phone_number_id": data_object.phoneNumberId,
    })
    
    # Extract type-specific fields
    if hasattr(data_object, "from_"):
        event_data["from_number"] = data_object.from_
    if hasattr(data_object, "to"):
        event_data["to_number"] = data_object.to
    if hasattr(data_object, "body"):
        event_data["message_text"] = data_object.body
        
    return event_data

@router.post(
    "/webhook",
    dependencies=[Depends(verify_open_phone_signature)],
)
async def webhook(
    request: Request,
    payload: OpenPhoneWebhookPayload,
    session: AsyncSession = Depends(get_session)
):
    try:
        # Extract event data
        event_data = extract_event_data(payload)
        
        # Analyze messages for potential Twilio escalation before saving to DB
        if payload.type=="message.received":
            await analyze_for_twilio_escalation(event_data)
        
        # check if event_id is already in the database
        existing_event = await session.execute(
            select(OpenPhoneEvent).where(OpenPhoneEvent.event_id == event_data["event_id"])
        )
        if existing_event:
            logging.info(f"Event {event_data['event_id']} already processed, skipping")
            return {"message": "Event already processed"}
        else:
            # Create database record
            open_phone_event = OpenPhoneEvent(**event_data)
            session.add(open_phone_event)
            await session.commit()
            await session.refresh(open_phone_event)
            logging.info(f"Successfully recorded OpenPhone event: {payload.type}")
            return {"message": "Event recorded successfully"}
        
    except IntegrityError as e:
        # If the event already exists, that's fine - just return success
        if "uq_open_phone_events_event_id" in str(e):
            logging.info(f"Event {payload.id} already processed, skipping")
            return {"message": "Event already processed"}
        raise HTTPException(500, f"Database error: {str(e)}")
    except Exception as e:
        logging.error(f"Error processing OpenPhone webhook: {str(e)}", exc_info=True)
        await session.rollback()
        raise HTTPException(500, f"Error processing webhook: {str(e)}")

@router.post("/send_message", dependencies=[Depends(verify_admin_or_serniacapital)])
async def send_message_endpoint(request: Request):
    """
    Simple endpoint wrapper around send_message.
    Only available in development environment.
    """
    data = await request.json()
    message = data["message"]

    if "from_phone_number" not in data:
        raise HTTPException(400, "from_phone_number is required")
    if "to_phone_number" not in data:
        raise HTTPException(400, "to_phone_number is required")

    from_phone_number = data["from_phone_number"]
    to_phone_number = data["to_phone_number"]

    response = await send_message(message, to_phone_number, from_phone_number)
    return {"message": "Message sent", "open_phone_response": response.json()}


@router.get("/contacts", dependencies=[Depends(verify_admin_auth)])
async def route_get_contacts_by_external_ids(
    external_ids: List[str] = Query(...),
    sources: Union[List[str], None] = Query(default=None),
    page_token: Union[str, None] = None,
):
    return await get_contacts_by_external_ids(external_ids, sources, page_token)

class TenantMassMessageRequest(BaseModel):
    property_names: List[str]
    message: str

@router.post("/tenant_mass_message", dependencies=[Depends(verify_admin_auth)])
async def send_tenant_mass_message(
    body: TenantMassMessageRequest,
):
    """Send a message to all tenants in the specified properties."""
    logging.info(
        f"Starting tenant mass message request for properties: {body.property_names}"
    )

    try:
        # Get contacts from Google Sheet
        try:
            logging.info("Fetching contacts from Google Sheet")
            all_unfiltered_contacts = get_contacts_sheet_as_json()
            logging.info(
                f"Retrieved {len(all_unfiltered_contacts)} total contacts from sheet"
            )
        except Exception as e:
            logging.error(
                f"Failed to fetch contacts from Google Sheet: {str(e)}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch contacts: {str(e)}"
            )
        
        property_names = body.property_names

        # Filter contacts for all specified properties
        contacts = [
            contact
            for contact in all_unfiltered_contacts
            if contact["Property"] in property_names
        ]

        # Filter out contacts where Lease End Date is in the past
        for contact in contacts:
            if "Lease End Date" in contact:
                if contact["Lease End Date"] < datetime.now().strftime("%Y-%m-%d"):
                    contacts.remove(contact)
                    logging.info(f"Removed contact {contact['First Name']} because Lease End Date is in the past")
            else:
                contacts.remove(contact)
                logging.warning(f"Contact {contact['First Name']} has no Lease End Date. Filtering out.")

        logging.info(
            f"Found {len(contacts)} total contacts for properties {body.property_names}"
        )

        if not contacts:
            logging.warning(f"No contacts found for properties: {body.property_names}")
            raise HTTPException(
                status_code=404,
                detail=f"No contacts found for properties: {body.property_names}",
            )

        failures = 0
        successes = 0
        failed_contacts = []
        error_messages = set()

        # Create a list of coroutines for concurrent message sending
        message_tasks = []
        for contact in contacts:
            try:
                phone_number = "+1" + contact["Phone Number"].replace("-", "").replace(
                    " ", ""
                ).replace("(", "").replace(")", "")
                logging.info(
                    f"Preparing message to {contact['First Name']} at {phone_number}"
                )

                # Create a coroutine for each message
                message_tasks.append(
                    send_message(
                        message=body.message,
                        to_phone_number=phone_number,
                    )
                )

            except Exception as e:
                failures += 1
                error_message = str(e)
                error_messages.add(error_message)
                failed_contacts.append(
                    {
                        "name": contact["First Name"],
                        "phone": contact["Phone Number"],
                        "property": contact["Property"],
                        "error": error_message,
                    }
                )
                logging.error(
                    f"Failed to prepare message for {contact['First Name']}: {error_message}"
                )

        # Send all messages concurrently
        responses = await asyncio.gather(*message_tasks, return_exceptions=True)

        # Process responses
        for contact, response in zip(contacts, responses):
            try:
                # Skip if this was an exception
                if isinstance(response, Exception):
                    raise response

                # Check for both 200 (OK) and 202 (Accepted) as success statuses
                if response.status_code not in [200, 202]:
                    error_text = response.text
                    try:
                        error_json = response.json()
                        error_text = json.dumps(error_json)
                    except:
                        pass
                    raise Exception(
                        f"OpenPhone API returned status {response.status_code}: {error_text}"
                    )

                # If we get here, the message was sent successfully
                response_data = response.json()
                if response_data.get("data", {}).get("status") == "sent":
                    successes += 1
                    logging.info(f"Successfully sent message to {contact['First Name']}")
                else:
                    raise Exception(
                        f"Message not confirmed as sent: {json.dumps(response_data)}"
                    )

            except Exception as e:
                failures += 1
                error_message = str(e)
                error_messages.add(error_message)
                failed_contacts.append(
                    {
                        "name": contact["First Name"],
                        "phone": contact["Phone Number"],
                        "property": contact["Property"],
                        "error": error_message,
                    }
                )
                logging.error(
                    f"Failed to send message to {contact['First Name']}: {error_message}"
                )

        # Prepare response
        if failures > 0:
            failed_details = [
                f"{c['name']} ({c['phone']}) in {c['property']}: {c['error']}"
                for c in failed_contacts
            ]
            message = (
                f"{'Partial success' if successes > 0 else 'Failed'}: "
                f"Sent to {successes} contacts, failed for {failures} contacts.\n\n"
                f"Failed contacts:\n" + "\n".join(failed_details)
            )
            logging.warning(message)

            return JSONResponse(
                status_code=207 if successes > 0 else 500,
                content={
                    "message": message,
                    "success": successes > 0,
                    "failures": failures,
                    "successes": successes,
                    "failed_contacts": failed_contacts,
                },
            )
        property_names_str = ", ".join(body.property_names)
        success_message = f"Successfully sent message to all {successes} contacts in {property_names_str}!"
        logging.info(success_message)
        return JSONResponse(
            status_code=200,
            content={
                "message": success_message,
                "success": True,
                "successes": successes,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(
            f"Unexpected error in send_tenant_mass_message: {str(e)}", exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content={
                "message": f"Unexpected error: {str(e)}",
                "success": False,
                "error": str(e),
            },
        )


# def test_get_ghost_ids():
#     headers = {
#         "Authorization": f"{os.getenv('OPEN_PHONE_API_KEY')}",
#         "Content-Type": "application/json",
#     }
#     ghost_ids = [
#         "67a3f0e374352083a596852c",
#         "67a3ea7913bc7ac81079abce",
#         "67a3f29874352083a5968570",
#     ]
#     response_codes = []
#     for id in ghost_ids:
#         url = f"https://api.openphone.com/v1/contacts/{id}"
#         response = requests.get(url, headers=headers)
#         print(response.json())
#         response_codes.append(response.status_code)
#     pprint(response_codes)

#     assert set(response_codes) == set([200])


# Working!
@router.post("/create_contacts_in_openphone", dependencies=[Depends(verify_admin_auth)])
async def create_contacts_in_openphone(overwrite=False, source_name=None):

    headers = {
        "Authorization": os.getenv("OPEN_PHONE_API_KEY"),
        "Content-Type": "application/json",
    }

    url = "https://api.openphone.com/v1/contact-custom-fields"
    headers = {
        "Authorization": f"{os.getenv('OPEN_PHONE_API_KEY')}",
        "Content-Type": "application/json",
    }
    custom_fields_raw = requests.get(url, headers=headers).json()["data"]

    custom_field_key_to_name = {
        field["key"]: field["name"] for field in custom_fields_raw
    }

    contacts = get_contacts_sheet_as_json()
    contact = contacts[35]

    response_codes = []
    responses = []

    # The source name needs a timestamp, otherwise API will return 500 error on re-creation
    if not source_name:
        source_name = f"API-Emilio-{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    for contact in contacts:
        print(contact["external_id"])

        contact["Lease Start Date"] = (
            contact["Lease Start Date"][:10] + "T00:00:00.000Z"
        )
        contact["Lease End Date"] = contact["Lease End Date"][:10] + "T00:00:00.000Z"

        data = {
            "defaultFields": {
                "company": contact["Company"],
                "emails": [{"name": " Email", "value": contact["Email"]}],
                "firstName": contact["First Name"],
                "lastName": contact["Last Name"],
                "phoneNumbers": [{"name": "Phone", "value": contact["Phone Number"]}],
                "role": contact["Role"],
            },
            "createdByUserId": "USXAiFJxgv",  # Emilio
            "source": source_name,
            "externalId": contact[
                "external_id"
            ],  # "e" + contact["Phone Number"],   # contact["external_id"]
            "customFields": [
                {"key": key, "value": contact[field_name]}
                for key, field_name in custom_field_key_to_name.items()
            ],
        }
        # pprint(data)

        # get contact by external id
        existing_contacts = await get_contacts_by_external_ids(
            external_ids=[contact["external_id"]]
        )
        skip = False
        if len(existing_contacts["data"]) > 0:

            if overwrite:
                print("Contact already exists, deleting...")
                # delete contact(s)
                for existing_contact in existing_contacts["data"]:
                    url = f"https://api.openphone.com/v1/contacts/{existing_contact['id']}"
                    response = requests.delete(url, headers=headers)
                    pprint(response)
            else:
                print("Contact already exists, skipping...")
                skip = True

        if not skip:

            time.sleep(1)
            response = requests.post(
                "https://api.openphone.com/v1/contacts", headers=headers, json=data
            )
            response_codes.append(response.status_code)
            pprint(response.json())
            pprint(response.status_code)
            responses.append(response.json())

    assert set(response_codes) == set([201]) or response_codes == []

@router.get("/tenants", dependencies=[Depends(verify_serniacapital_user)])
async def get_tenant_data():
    """
    Fetch tenant contact data from the Google Sheet.
    Adds an 'Active Lease' boolean field based on Lease Start/End Dates.
    Requires the user to be authenticated with a @serniacapital.com email.
    """
    try:
        logging.info("Fetching tenant contacts from Google Sheet for SerniaCapital user")
        tenant_data = get_contacts_sheet_as_json()
        logging.info(f"Retrieved {len(tenant_data)} total tenant contacts from sheet")
        
        # Add 'Active Lease' field
        today = date.today()
        processed_data = []
        for tenant in tenant_data:
            try:
                start_date_str = tenant.get('Lease Start Date')
                end_date_str = tenant.get('Lease End Date')
                
                is_active = False
                if start_date_str and end_date_str:
                    # Attempt to parse YYYY-MM-DD format (or first 10 chars)
                    start_date = date.fromisoformat(start_date_str[:10])
                    end_date = date.fromisoformat(end_date_str[:10])
                    if start_date <= today <= end_date:
                        is_active = True
                
                tenant['Active Lease'] = is_active
                processed_data.append(tenant)
            except (ValueError, TypeError) as date_err:
                logging.warning(f"Could not parse dates for tenant {tenant.get('external_id', '<no_id>')}: {date_err}. Setting Active Lease to False.")
                tenant['Active Lease'] = False # Default to False if dates are invalid
                processed_data.append(tenant)

        return processed_data
    except Exception as e:
        logging.error(
            f"Failed to fetch or process tenant contacts: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch or process tenant contacts: {str(e)}"
        )

