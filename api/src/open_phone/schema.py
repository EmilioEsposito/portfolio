from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from datetime import datetime


class BaseOpenPhoneObject(BaseModel):
    id: str
    object: str
    createdAt: datetime
    userId: str
    phoneNumberId: Optional[str] = None
    conversationId: Optional[str] = None

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

class CallSummaryObject(BaseModel):
    """
    Represents a summary of a call, including its status, key points, and next steps.
    
    Attributes:
        object (str): The type of object, typically "call_summary".
        callId (str): The unique identifier for the call.
        status (str): The current status of the call (e.g., "completed", "in_progress").
        summary (List[str]): A list of key points or highlights from the call.
        nextSteps (List[str]): A list of recommended next steps following the call.
    """
    object: str
    callId: str
    status: str
    summary: List[str]
    nextSteps: List[str]

class DialogueEntry(BaseModel):
    end: float
    start: float
    content: str
    identifier: str
    userId: Optional[str] = None

class CallTranscriptObject(BaseModel):
    object: str
    callId: str
    createdAt: datetime
    dialogue: List[DialogueEntry]
    duration: float
    status: str

class OpenPhoneEventData(BaseModel):
    object: Union[MessageObject, CallObject, ContactObject, CallSummaryObject, CallTranscriptObject]

class OpenPhoneWebhookPayload(BaseModel):
    id: str
    object: str
    createdAt: datetime
    apiVersion: str
    type: str
    data: OpenPhoneEventData

    