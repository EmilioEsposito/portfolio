from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BaseOpenPhoneObject(BaseModel):
    id: str
    object: str
    createdAt: datetime
    userId: str
    phoneNumberId: str | None = None
    conversationId: str | None = None

class MessageObject(BaseOpenPhoneObject):
    from_: str = Field(..., alias="from")
    to: str
    body: str
    media: list[Any] = []
    status: str
    createdBy: str | None = None
    direction: str

class CallObject(BaseOpenPhoneObject):
    from_: str = Field(..., alias="from")
    to: str
    direction: str
    media: list[Any] = []
    voicemail: Any | None = None
    status: str
    answeredAt: datetime | None = None
    answeredBy: str | None = None
    completedAt: datetime | None = None

class ContactObject(BaseOpenPhoneObject):
    firstName: str | None = ""
    lastName: str | None = ""
    company: str | None = ""
    role: str | None = ""
    pictureUrl: str | None = ""
    fields: dict[str, Any] | None = []
    notes: list[Any] = []
    sharedWith: list[str]
    clientId: str | None = ""
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
    summary: list[str]
    nextSteps: list[str]

class DialogueEntry(BaseModel):
    end: float
    start: float
    content: str
    identifier: str
    userId: str | None = None

class CallTranscriptObject(BaseModel):
    object: str
    callId: str
    createdAt: datetime
    dialogue: list[DialogueEntry]
    duration: float
    status: str

class OpenPhoneEventData(BaseModel):
    object: MessageObject | CallObject | ContactObject | CallSummaryObject | CallTranscriptObject

class OpenPhoneWebhookPayload(BaseModel):
    id: str
    object: str
    createdAt: datetime
    apiVersion: str
    type: str
    data: OpenPhoneEventData

    