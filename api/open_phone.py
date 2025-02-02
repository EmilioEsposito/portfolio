from fastapi import APIRouter, Request, Body
from pydantic import BaseModel, Field
import json
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/open_phone",  # All endpoints here will be under /open_phone
    tags=["open_phone"],  # Optional: groups endpoints in the docs
)

class BaseModelWithConfig(BaseModel):
    class Config:
        extra = "ignore"

class OpenPhoneWebhookPayload(BaseModelWithConfig):
    class ObjectDict(BaseModelWithConfig):
        class Data(BaseModelWithConfig):
            class DataObject(BaseModelWithConfig):
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
    request: Request,
    payload: OpenPhoneWebhookPayload = Body(...),
):
    # Debug logging
    body = await request.body()
    logger.info("Raw request body: %s", body.decode())
    logger.info("Content-Type header: %s", request.headers.get("content-type"))
    
    # Try parsing the raw body to see what we're getting
    try:
        raw_json = json.loads(body.decode())
        logger.info("Parsed JSON: %s", json.dumps(raw_json, indent=2))
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", str(e))
        logger.info("Request headers: %s", dict(request.headers))

    headers = dict(request.headers)
    # TODO: secure it by checking the signature secret
    return {
        "message": "Hello from open_phone!",
        "payload": payload,
        "headers": headers,
    }


