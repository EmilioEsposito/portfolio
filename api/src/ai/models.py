from datetime import datetime
from typing import Any
from sqlalchemy import String, DateTime, func, JSON, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic_core import to_jsonable_python
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

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

async def get_agent_conversation(session: AsyncSession, conversation_id: str) -> AgentConversation | None:
    """Retrieve an agent conversation by ID."""
    stmt = select(AgentConversation).where(AgentConversation.id == conversation_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_conversation_messages(session: AsyncSession, conversation_id: str) -> list[ModelMessage]:
    """Retrieve conversation messages parsed back into PydanticAI ModelMessage objects."""
    conversation = await get_agent_conversation(session, conversation_id)
    if conversation and conversation.messages:
        return ModelMessagesTypeAdapter.validate_python(conversation.messages)
    return []

async def save_agent_conversation(
    session: AsyncSession,
    conversation_id: str,
    agent_name: str,
    messages: list[ModelMessage] | list[Any],
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None
) -> AgentConversation:
    """
    Save or update an agent conversation.
    Handles serialization of messages to JSON.
    """
    # Convert messages to JSON-able format
    messages_json = to_jsonable_python(messages)
    
    # Use upsert (insert on conflict update)
    stmt = pg_insert(AgentConversation).values(
        id=conversation_id,
        agent_name=agent_name,
        user_id=user_id,
        messages=messages_json,
        metadata_=metadata
    )
    
    # Define the update set on conflict
    update_dict = {
        'messages': stmt.excluded.messages,
        'updated_at': func.now(),
    }
    if metadata is not None:
        update_dict['metadata_'] = stmt.excluded.metadata_
        
    stmt = stmt.on_conflict_do_update(
        index_elements=['id'],
        set_=update_dict
    )
    
    await session.execute(stmt)
    await session.commit()
    
    # Return the updated record
    return await get_agent_conversation(session, conversation_id)
