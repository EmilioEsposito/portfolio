from datetime import datetime
from typing import Any
from sqlalchemy import String, DateTime, func, JSON
from sqlalchemy.orm import Mapped, mapped_column
from api.src.database.database import Base

class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    # conversation_id is used as the primary key
    id: Mapped[str] = mapped_column(String, primary_key=True, index=True) 
    agent_name: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
    )
