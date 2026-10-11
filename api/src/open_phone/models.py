from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


class OpenPhoneEvent(Base):
    __tablename__ = "open_phone_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String)  # e.g. message.received, call.completed, etc.
    event_id: Mapped[str] = mapped_column(String, unique=True)  # OpenPhone's event ID
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,  # Allow null for existing records
    )
    event_data: Mapped[dict] = mapped_column(JSON)  # Full event data for reference

    # Extracted fields for easier querying
    message_text: Mapped[str | None] = mapped_column(String, nullable=True)
    from_number: Mapped[str | None] = mapped_column(String, nullable=True)
    to_number: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String, nullable=True)


class EscalationRequest(Base):
    """One durable source event/tool call. Historical webhooks are never backfilled."""

    __tablename__ = "escalation_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_key: Mapped[str] = mapped_column(String, unique=True)
    event_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message_text: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


class EscalationDelivery(Base):
    """Frozen recipient and fenced delivery state, independent of other recipients."""

    __tablename__ = "escalation_deliveries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("escalation_requests.id"), index=True)
    recipient_key: Mapped[str] = mapped_column(String)
    recipient: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_sid: Mapped[str | None] = mapped_column(String, nullable=True)
    conflict_sid: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


class EscalationAttempt(Base):
    """Persisted before a provider POST; completed only by its lease owner."""

    __tablename__ = "escalation_attempts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("escalation_deliveries.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String, default="sending")
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    provider_code: Mapped[int | None] = mapped_column(nullable=True)
    execution_sid: Mapped[str | None] = mapped_column(String, nullable=True)
