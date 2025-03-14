from datetime import datetime
from sqlalchemy import String, DateTime, func, JSON, Text, ARRAY, Integer
from sqlalchemy.orm import Mapped, mapped_column
from api_src.database.database import Base

class EmailMessage(Base):
    """SQLAlchemy model for storing Gmail messages"""
    __tablename__ = "email_messages"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String, unique=True, index=True)  # Gmail's message ID
    thread_id: Mapped[str] = mapped_column(String, index=True)  # Gmail's thread ID
    subject: Mapped[str] = mapped_column(String)
    from_address: Mapped[str] = mapped_column(String)  # Using from_address since 'from' is a reserved word
    to_address: Mapped[str] = mapped_column(String, nullable=True)
    received_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body_text: Mapped[str] = mapped_column(Text, nullable=True)  # Plain text body
    body_html: Mapped[str] = mapped_column(Text, nullable=True)  # HTML body
    raw_payload: Mapped[dict] = mapped_column(JSON)  # Store the full message payload for future processing
    # New columns
    first_history_id: Mapped[int] = mapped_column(Integer, nullable=True)  # First history ID when message was seen
    history_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=True)  # Array of history IDs where this message appeared
    label_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)  # Array of Gmail label IDs
    
    # Metadata timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
    ) 