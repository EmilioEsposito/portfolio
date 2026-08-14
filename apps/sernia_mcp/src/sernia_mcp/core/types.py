"""Pydantic result models for ``core`` functions."""

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
    from_phone_id: str
    line_name: str


class GroupSmsRouting(BaseModel):
    """Resolved routing for a group SMS (one shared thread, 2+ recipients).

    All-internal groups send from the AI line; any group containing an
    external contact sends from the shared team number. ``is_mixed`` flags
    internal+external groups (internal numbers become visible to external
    recipients — the approval card is the safeguard).
    """

    phones: list[str]  # deduped, original order preserved
    recipient_names: list[str]
    all_internal: bool
    is_mixed: bool
    from_phone_id: str
    line_name: str


class SmsResult(BaseModel):
    """Result of a send_sms_core / send_group_sms_core call.

    For group sends, ``to_phone`` and ``contact_name`` are comma-joined
    lists of the recipients.
    """

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
