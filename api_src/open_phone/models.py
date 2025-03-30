from datetime import datetime
from sqlalchemy import String, DateTime, func, JSON
from sqlalchemy.orm import Mapped, mapped_column
from api_src.database.database import Base

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