from fastapi import APIRouter, Request, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, conint
import json
import logging
from pprint import pprint
import os
import base64
import hmac
import requests
from typing import List, Optional, Union
from datetime import datetime
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/open_phone",  # All endpoints here will be under /open_phone
    tags=["open_phone"],  # Optional: groups endpoints in the docs
)


class OpenPhoneWebhookPayload(BaseModel):
    class Data(BaseModel):
        class DataObject(BaseModel):
            object: str
            from_: str = Field(..., alias="from")
            to: str
            body: str
            media: list = []
            createdAt: str
            userId: str
            phoneNumberId: str
            conversationId: str

        object: DataObject

    data: Data
    object: str


# https://support.openphone.com/hc/en-us/articles/4690754298903-How-to-use-webhooks
async def verify_open_phone_signature(request: Request):
    # signing_key = os.getenv(env_var_name)
    signing_key = os.getenv("OPEN_PHONE_MESSAGE_RECEIVED_WEBHOOK_SECRET")
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
        print("signature verification succeeded")
        return True
    else:
        print("signature verification failed")
        raise HTTPException(403, "Signature verification failed")
        # return True


@router.post(
    "/message_received",
    dependencies=[Depends(verify_open_phone_signature)],
)
async def message_received(
    request: Request,
    payload: OpenPhoneWebhookPayload,  # Using this caused lots of 422 errors!
):
    # If we are here, the signature was valid

    body = await request.body()

    # Try parsing the raw body to see what we're getting
    try:
        request_body_json = json.loads(body.decode())

        logger.info("Request body JSON: %s", json.dumps(request_body_json, indent=2))
        logger.info("Request headers: %s", json.dumps(dict(request.headers), indent=2))
        logger.info("Payload: %s", payload)

        # TODO: read the message, process it, and send a response using send_message

    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", str(e))
        logger.info("Request headers: %s", dict(request.headers))
        return {
            "message": "Failed to parse JSON",
            "body": body.decode(),
            "headers": dict(request.headers),
        }

    return {
        "message": "Hello from open_phone!",
        "payload": payload,
        "request_body_json": request_body_json,
        "headers": request.headers,
    }


# curl --request POST \
#   --url https://api.openphone.com/v1/messages \
#   --header 'Content-Type: application/json' \
#   --data '{
#   "content": "<string>",
#   "phoneNumberId": "OP1232abc",
#   "from": "+15555555555",
#   "to": [
#     "+15555555555"
#   ],
#   "userId": "US123abc",
#   "setInboxStatus": "done"
# }'

# Implement authentication

# Include your API key in the Authorization header of each request: Authorization: YOUR_API_KEY
# The OpenPhone API does not use a Bearer token for authentication.


def send_message(
    message: str, 
    to_phone_number: str,
    from_phone_number: str="+14129101989",
):
    """
    Send a message to a phone number using the OpenPhone API.
    """

    # User OpenPhone API to send a message
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


def local_only_route(request: Request):
    """
    Dependency function to restrict routes to local development only.
    Raises 401 if not in development environment.
    """
    if os.getenv("VERCEL_ENV") != "development":
        raise HTTPException(
            status_code=401,
            detail="Unauthorized - this route is only available in development",
        )
    return True


@router.post("/send_message", dependencies=[Depends(local_only_route)])
async def send_message_endpoint(request: Request):
    """
    Simple endpoint wrapper around send_message.
    Only available in development environment.
    """

    data = await request.json()

    message = data["message"]
    from_phone_number = data["from_phone_number"]
    to_phone_number = data["to_phone_number"]

    response = send_message(
        message, to_phone_number, from_phone_number
    )

    return {"message": "Message sent", "open_phone_response": response.json()}


# Create a non-route function for internal use
async def get_contacts_by_external_ids(
    external_ids: List[str],
    sources: Optional[List[str]] = None,
    page_token: Optional[str] = None
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
        # Re-raise the appropriate exception
        raise

# Keep the original route but make it use the internal function
@router.get("/contacts", dependencies=[Depends(local_only_route)])
async def route_get_contacts_by_external_ids(
    external_ids: List[str] = Query(...),
    sources: Union[List[str], None] = Query(default=None),
    page_token: Union[str, None] = None,
):
    return await get_contacts_by_external_ids(external_ids, sources, page_token)


def get_contacts_from_sheetdb():
    url = "https://sheetdb.io/api/v1/vs9ahjsfdc4a1?sheet=OpenPhone"
    headers = {
        "Authorization": f"Bearer {os.getenv('SHEETDB_API_KEY')}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers)
    return response.json()

class BuildingMessageRequest(BaseModel):
    building_name: str
    message: str

@router.post("/send_message_to_building", dependencies=[Depends(local_only_route)])
async def send_message_to_building(
    request: BuildingMessageRequest,
):
    contacts = get_contacts_from_sheetdb()

    # Filter contacts for the specified building
    contacts = [contact for contact in contacts if contact["Building"] == request.building_name]
    
    if not contacts:
        raise HTTPException(
            status_code=404,
            detail=f"No contacts found for building: {request.building_name}"
        )
    
    for contact in contacts:
        send_message(
            request.message,
            to_phone_number="+1"+contact["Phone Number"],
        )
    return contacts

def test_get_ghost_ids():
    headers = {
        "Authorization": f"{os.getenv('OPEN_PHONE_API_KEY')}",
        "Content-Type": "application/json",
    }
    ghost_ids = ["67a3f0e374352083a596852c", "67a3ea7913bc7ac81079abce", "67a3f29874352083a5968570"]
    response_codes = []
    for id in ghost_ids:
        url = f"https://api.openphone.com/v1/contacts/{id}"
        response = requests.get(url, headers=headers)
        print(response.json())
        response_codes.append(response.status_code)
    pprint(response_codes)

    assert set(response_codes)==set([200])
    

# Working! 
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
    custom_fields_raw = requests.get(url, headers=headers).json()['data']

    custom_field_key_to_name = {field["key"]: field["name"] for field in custom_fields_raw}

    contacts = get_contacts_from_sheetdb()
    contact = contacts[35]

    response_codes = []
    responses = []

    # The source name needs a timestamp, otherwise API will return 500 error on re-creation
    if not source_name:
        source_name = f"API-Emilio-{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    for contact in contacts:
        print(contact['external_id'])

        contact["Lease Start Date"] = contact["Lease Start Date"][:10] + "T00:00:00.000Z"
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
            "createdByUserId": "USXAiFJxgv", # Emilio
            "source": source_name,
            "externalId": contact["external_id"], # "e" + contact["Phone Number"],   # contact["external_id"]
            "customFields": [
                {"key": key, "value": contact[field_name]}
                for key, field_name in custom_field_key_to_name.items()
            ],
        }
        # pprint(data)

        
        # get contact by external id
        existing_contacts = await get_contacts_by_external_ids(external_ids=[contact["external_id"]])
        skip = False
        if len(existing_contacts['data'])>0:

            if overwrite:
                print("Contact already exists, deleting...")
                # delete contact(s)
                for existing_contact in existing_contacts['data']:
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

    assert set(response_codes)==set([201]) or response_codes==[]

