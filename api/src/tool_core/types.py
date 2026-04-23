"""Shared Pydantic result models returned by tool_core functions."""
from pydantic import BaseModel


class WorkspaceFile(BaseModel):
    """Result of a workspace_read_core call."""

    path: str
    content: str
    size_bytes: int


class WorkspaceWriteResult(BaseModel):
    """Result of a workspace_write_core call."""

    path: str
    size_bytes: int
    created: bool  # True if the file did not exist before this write


class SmsRouting(BaseModel):
    """Resolved SMS routing for a given recipient phone."""

    contact_id: str | None
    contact_name: str | None
    is_internal: bool
    from_phone_id: str  # Quo phone ID the message will send from
    line_name: str  # Human-readable line label


class SmsResult(BaseModel):
    """Result of a send_sms_core call."""

    to_phone: str
    contact_name: str | None
    line_name: str
    parts_sent: int
    message_chars: int


class EmailSendResult(BaseModel):
    """Result of a send_email_core call."""

    to: list[str]
    subject: str
    from_address: str
    message_id: str | None
