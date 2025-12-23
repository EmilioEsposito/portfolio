from datetime import datetime
from typing import Any
from sqlalchemy import String, DateTime, func, JSON, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic_core import to_jsonable_python
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.agent import AgentRunResult
import logfire

from api.src.database.database import Base, AsyncSessionFactory

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
    Uses SQLAlchemy's merge for idiomatic upsert behavior.
    """
    # Convert messages to JSON-safe format using Pydantic's adapter
    # mode='json' ensures bytes are serialized (e.g. to utf-8 string if valid, but care needed for raw binary)
    # If messages contain raw binary that isn't utf-8 (like PDF bytes), dump_python(mode='json') might fail or produce errors.
    # However, ModelMessagesTypeAdapter is usually robust. 
    # If you encounter encoding errors, we might need a custom serializer for bytes to base64.
    
    if not agent_name:
        logfire.error("No agent_name provided for conversation persistence")
    
    try:
        messages_json = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
    except Exception:
        logfire.exception(f"Failed to convert messages to JSON, using fallback")
        # Fallback: simple to_jsonable_python which might leave bytes as is, 
        # but we iterate to stringify bytes to avoid DB errors
        data = to_jsonable_python(messages)
        messages_json = _sanitize_json(data)

    # Use session.merge which performs an upsert (SELECT + INSERT/UPDATE)
    # This is idiomatic SQLAlchemy ORM.
    conversation = AgentConversation(
        id=conversation_id,
        agent_name=agent_name,
        user_id=user_id,
        messages=messages_json,
        metadata_=metadata
    )
    
    # merge returns the persistent instance attached to the session
    conversation = await session.merge(conversation)
    await session.commit()
    
    return conversation

async def persist_agent_run_result(
    result: AgentRunResult,
    conversation_id: str,
    agent_name: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None
) -> None:
    """
    Convenience function to persist an agent run result to the database.
    Handles session creation and error logging.
    """
    if not conversation_id:
        logfire.warning("No conversation_id provided for persistence")
        return

    try:
        async with AsyncSessionFactory() as session:
            await save_agent_conversation(
                session=session,
                conversation_id=conversation_id,
                agent_name=agent_name,
                messages=result.all_messages(),
                user_id=user_id,
                metadata=metadata
            )
        logfire.info(f"Saved conversation {conversation_id} for agent {agent_name}")
    except Exception as e:
        logfire.error(f"Failed to save conversation {conversation_id}: {e}")

def _sanitize_json(obj: Any) -> Any:
    """Helper to ensure all data is JSON compliant, converting bytes to string placeholder."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    elif isinstance(obj, bytes):
        # Convert bytes to string representation or base64 if needed. 
        # For logs/history, we probably don't want huge binary blobs.
        return f"<bytes len={len(obj)}>"
    return obj
