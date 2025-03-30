from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from datetime import datetime

class BaseOpenPhoneObject(BaseModel):
    id: str
    object: str
    createdAt: datetime
    userId: str
    phoneNumberId: str
    conversationId: str

class MessageObject(BaseOpenPhoneObject):
    from_: str = Field(..., alias="from")
    to: str
    body: str
    media: List[Any] = []
    status: str
    createdBy: Optional[str] = None
    direction: str

class CallObject(BaseOpenPhoneObject):
    from_: str = Field(..., alias="from")
    to: str
    direction: str
    media: List[Any] = []
    voicemail: Optional[Any] = None
    status: str
    answeredAt: Optional[datetime] = None
    answeredBy: Optional[str] = None
    completedAt: Optional[datetime] = None

class ContactObject(BaseOpenPhoneObject):
    firstName: str
    lastName: str
    company: str
    role: str
    pictureUrl: Optional[str] = ""
    fields: Dict[str, Any]
    notes: List[Any] = []
    sharedWith: List[str]
    clientId: str
    updatedAt: datetime
    phoneNumberId: Optional[str] = None
    conversationId: Optional[str] = None

class OpenPhoneEventData(BaseModel):
    object: Union[MessageObject, CallObject, ContactObject]

class OpenPhoneWebhookPayload(BaseModel):
    id: str
    object: str
    createdAt: datetime
    apiVersion: str
    type: str
    data: OpenPhoneEventData

    