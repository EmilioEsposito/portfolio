from fastapi import APIRouter, Request, Body, Depends, HTTPException
from pydantic import BaseModel, Field
import json
import logging
from pprint import pprint
import os
import base64
import hmac
import hashlib
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/open_phone",  # All endpoints here will be under /open_phone
    tags=["open_phone"],  # Optional: groups endpoints in the docs
)


class OpenPhoneWebhookPayload(BaseModel):
    class ObjectDict(BaseModel):
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

    object: ObjectDict


#  'openphone-signature': 'hmac;1;1639710054089;fake_signature'
# The format is:

# <scheme>;<version>;<timestamp>;<signature>
#  scheme	The signature scheme. Currently, this is always "hmac".
#  version	The signature version. Currently this is always "1".
#  timestamp	The timestamp when the signature was generated.
#  signature	The base64 encoded digital signature.


def verify_openphone_signature_factory(env_var_name: str):
    """
    Returns a dependency function that checks signature using an HMAC
    defined by the env_var_name environment variable.
    """
    async def verify_openphone_signature(request: Request):
        signing_key = os.getenv(env_var_name)
        data = await request.body()
        # Parse the fields from the openphone-signature header.
        signature = request.headers['openphone-signature']
        fields = signature.split(';')
        timestamp = fields[2]
        provided_digest = fields[3]

        # Compute the data covered by the signature as bytes.
        signed_data_bytes = b''.join([timestamp.encode(), b'.', data])

        # Convert the base64-encoded signing key to bytes.
        signing_key_bytes = base64.b64decode(signing_key)

        # Compute the SHA256 HMAC digest.
        # Obtain the digest in base64-encoded form for easy comparison with
        # the digest provided in the openphone-signature header.
        hmac_object = hmac.new(signing_key_bytes, signed_data_bytes, 'sha256')
        computed_digest = base64.b64encode(hmac_object.digest()).decode()

        # Make sure the computed digest matches the digest in the openphone header.
        if provided_digest == computed_digest:
            print('signature verification succeeded')
            return True
        else:
            print('signature verification failed')
            raise HTTPException(403, "Signature verification failed")

    return verify_openphone_signature


@router.post(
    "/message_received",
    dependencies=[Depends(verify_openphone_signature_factory("OPEN_PHONE_MESSAGE_RECEIVED_WEBHOOK_SECRET"))],
)
async def message_received(
    request: Request,
    # payload: OpenPhoneWebhookPayload, # Using this caused lots of 422 errors!
):
    # If we are here, the signature was valid

    body = await request.body()

    # Try parsing the raw body to see what we're getting
    try:
        request_body_json = json.loads(body.decode())
        logger.info("Raw request body: %s", body.decode())
        logger.info("Raw request header: %s", request.headers)
        # logger.info("Request body JSON: %s", json.dumps(request_body_json, indent=2))
        # logger.info("Request headers: %s", json.dumps(dict(request.headers), indent=2))
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", str(e))
        logger.info("Request headers: %s", dict(request.headers))
        return {
            "message": "Failed to parse JSON",
            "body": body.decode(),
            "headers": dict(request.headers),
        }

    # TODO: secure it by checking the signature secret
    return {
        "message": "Hello from open_phone!",
        # "payload": payload,
        "request_body_json": request_body_json,
        "headers": request.headers,
    }

