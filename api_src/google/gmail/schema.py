"""
Pydantic schemas for Gmail-related operations.
"""

from pydantic import BaseModel
from typing import Union

class OptionalPassword(BaseModel):
    """Request model for endpoints that optionally require a password."""
    password: Union[str, None] = None

class GenerateResponseRequest(BaseModel):
    """Request model for generating AI responses."""
    email_content: str
    system_instruction: str

class ZillowEmailResponse(BaseModel):
    """Response model for Zillow email queries."""
    id: str
    subject: str
    sender: str
    received_at: str
    body_html: str 