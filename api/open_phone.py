from fastapi import APIRouter, Request, Body
from pydantic import BaseModel, Field
import json
import logging
from pprint import pprint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)
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

@router.post("/message_received")
async def message_received(
    request: Request
    # payload: OpenPhoneWebhookPayload, # Using this caused lots of 422 errors!
):
    # Debug logging
    body = await request.body()

    # Try parsing the raw body to see what we're getting
    try:
        request_body_json = json.loads(body.decode())
        logger.info("Request body JSON: %s", json.dumps(request_body_json, indent=2))
        logger.info("Request headers: %s", json.dumps(dict(request.headers), indent=2))
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


