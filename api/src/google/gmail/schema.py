"""
Pydantic schemas for Gmail-related operations.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EmailMessageBase(BaseModel):
    """Base Pydantic model for email messages"""

    message_id: str
    thread_id: str
    subject: str
    from_address: str
    to_address: str
    received_date: datetime
    body_text: str | None = None
    body_html: str | None = None
    raw_payload: dict[str, Any]


class EmailMessageCreate(EmailMessageBase):
    """Pydantic model for creating email messages"""

    pass


class EmailMessageResponse(EmailMessageBase):
    """Pydantic model for email message responses"""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)  # Replaces the old Config class
