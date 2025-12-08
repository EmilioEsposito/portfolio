"""
Database models for Email Approval Demo.
"""
from datetime import datetime
from sqlalchemy import String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from api.src.database.database import Base


class PendingApproval(Base):
    """Stores pending email approvals waiting for human decision."""

    __tablename__ = "pending_email_approvals"

    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(String(255))
    message_history: Mapped[str] = mapped_column(Text)  # JSON string
    email_to: Mapped[str] = mapped_column(String(255))
    email_subject: Mapped[str] = mapped_column(String(500))
    email_body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
