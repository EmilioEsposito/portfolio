from fastapi import APIRouter, Request, Body, Depends, HTTPException
from pydantic import BaseModel, Field, conint
import json
import logging
from pprint import pprint
import os
import base64
import hmac
import requests
from typing import List, Optional

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
    message: str, phone_number_id: str, from_phone_number: str, to_phone_number: str
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
        "content": "Hello, world!",
        "phoneNumberId": "OP1232abc",
        "from": "+15555555555",
        "to": ["+15555555555"],
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
            detail="Unauthorized - this route is only available in development"
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
    phone_number_id = data["phone_number_id"]
    from_phone_number = data["from_phone_number"]
    to_phone_number = data["to_phone_number"]

    response = send_message(
        message, phone_number_id, from_phone_number, to_phone_number
    )

    return {"message": "Message sent", "open_phone_response": response.json()}


@router.get("/contacts", dependencies=[Depends(local_only_route)])
async def get_contacts_by_external_ids(
    external_ids: List[str] = ['sdf'],
    sources: Optional[List[str]] = None,
    page_token: Optional[str] = None,
):
    """
    Fetch contacts by their external IDs and optionally filter by sources.

    Only available in development environment.

    Args:
        external_ids: List of external IDs to search for (required)
        sources: Optional list of sources to filter by
        max_results: Maximum number of results per page (1-50, default: 10)
        page_token: Token for pagination

    Returns:
        JSON response containing matched contacts
    """

    max_results = 49

    # Build query parameters
    params = {"externalIds": external_ids, "maxResults": max_results}

    if sources:
        params["sources"] = sources
    if page_token:
        params["pageToken"] = page_token

    # Get API key from environment
    api_key = os.getenv("OPEN_PHONE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenPhone API key not configured")

    # Make request to OpenPhone API
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    try:
        response = requests.get(
            "https://api.openphone.com/v1/contacts", headers=headers, params=params
        )

        # Raise exception for error status codes
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching contacts: {str(e)}")

        # Handle different types of errors
        if isinstance(e, requests.exceptions.HTTPError):
            if e.response.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid OpenPhone API key")
            elif e.response.status_code == 400:
                raise HTTPException(
                    status_code=400,
                    detail=e.response.json().get("message", "Bad request"),
                )

        raise HTTPException(
            status_code=500, detail=f"Error fetching contacts: {str(e)}"
        )




