from fastapi import APIRouter, Request, Body
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/open_phone",  # All endpoints here will be under /open_phone
    tags=["open_phone"],  # Optional: groups endpoints in the docs
)

class BaseModelWithConfig(BaseModel):
    class Config:
        extra = "ignore"

class OpenPhoneWebhookPayload(BaseModelWithConfig):
    class Object(BaseModelWithConfig):
        class Data(BaseModelWithConfig):
            class Object(BaseModelWithConfig):
                from_: str = Field(..., alias="from")
                to: str
                body: str
                media: list = []
                createdAt: str
                userId: str
                conversationId: str
            
            object: Object
        
        data: Data
    
    object: Object

@router.post("/message_received")
async def message_received(
    request: Request,
    payload: OpenPhoneWebhookPayload = Body(...),
):
    headers = dict(request.headers)
    # TODO: secure it by checking the signature secret
    return {
        "message": "Hello from open_phone!",
        "payload": payload.model_dump(),
        "headers": headers,
    }


