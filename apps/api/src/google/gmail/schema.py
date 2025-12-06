"""
Pydantic schemas for Gmail-related operations.
"""

from pydantic import BaseModel
from typing import Union
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any

class OptionalPassword(BaseModel):
    """Request model for endpoints that optionally require a password."""
    password: Union[str, None] = None

class GenerateResponseRequest(BaseModel):
    """Request model for generating AI responses."""
    email_content: str
    system_instruction: str

class ZillowEmailResponse(BaseModel):
    """Pydantic model for Zillow email responses"""
    id: int
    subject: str
    sender: str
    received_at: datetime
    body_html: Optional[str] = None 


class EmailMessageBase(BaseModel):
    """Base Pydantic model for email messages"""
    message_id: str
    thread_id: str
    subject: str
    from_address: str
    to_address: str
    received_date: datetime
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    raw_payload: Dict[str, Any]

class EmailMessageCreate(EmailMessageBase):
    """Pydantic model for creating email messages"""
    pass

class EmailMessageResponse(EmailMessageBase):
    """Pydantic model for email message responses"""
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)  # Replaces the old Config class 


