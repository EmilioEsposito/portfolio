from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from datetime import datetime

# {
#   "object": {
#     "id": "EV9cf5849eaf5649aca0cf5e530935ab25",
#     "object": "event",
#     "createdAt": "2025-05-15T21:00:11.878Z",
#     "apiVersion": "v3",
#     "type": "contact.updated",
#     "data": {
#       "object": {
#         "id": "CT6823ffc81fc6d26d75e6e008",
#         "object": "contact",
#         "firstName": "Lead 659-02 Shalece",
#         "lastName": "C",
#         "fields": {
#           "Phone": "+14128535356"
#         },
#         "notes": [],
#         "sharedWith": [
#           "OR98t1AGEk"
#         ],
#         "clientId": "3a2c75be-1299-4fdb-b4c8-bccf2c6aafd2",
#         "createdAt": "2025-05-14T02:28:24.277Z",
#         "updatedAt": "2025-05-15T21:00:11.834Z",
#         "userId": "USXAiFJxgv"
#       }
#     }
#   }
# }

class BaseOpenPhoneObject(BaseModel):
    id: str
    object: str
    createdAt: datetime
    userId: str

class MessageObject(BaseOpenPhoneObject):
    from_: str = Field(..., alias="from")
    to: str
    body: str
    media: List[Any] = []
    status: str
    createdBy: Optional[str] = None
    direction: str
    phoneNumberId: Optional[str] = None
    conversationId: Optional[str] = None

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
    firstName: Optional[str] = ""
    lastName: Optional[str] = ""
    company: Optional[str] = ""
    role: Optional[str] = ""
    pictureUrl: Optional[str] = ""
    fields: Optional[Dict[str, Any]] = []
    notes: List[Any] = []
    sharedWith: List[str]
    clientId: Optional[str] = ""
    updatedAt: datetime

class OpenPhoneEventData(BaseModel):
    object: Union[MessageObject, CallObject, ContactObject]

class OpenPhoneWebhookPayload(BaseModel):
    id: str
    object: str
    createdAt: datetime
    apiVersion: str
    type: str
    data: OpenPhoneEventData

    