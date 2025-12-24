import asyncio
from datetime import datetime
from typing import Any
from sqlalchemy import String, DateTime, func, JSON, select, desc
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic_core import to_jsonable_python
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, TextPart, UserPromptPart
from pydantic_ai.agent import AgentRunResult
import logfire

from api.src.database.database import Base, AsyncSessionFactory, provide_session

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

async def get_agent_conversation(
    session: AsyncSession,
    conversation_id: str,
    retries: int = 3,
    retry_delay: float = 0.5,
) -> AgentConversation | None:
    """
    Retrieve an agent conversation by ID with retry logic.

    Retries help handle race conditions where the conversation may not be
    persisted yet when approval is clicked quickly after streaming completes.
    """
    for attempt in range(retries):
        stmt = select(AgentConversation).where(AgentConversation.id == conversation_id)
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation is not None:
            return conversation

        # Only retry if we didn't find it and have attempts left
        if attempt < retries - 1:
            logfire.info(f"Conversation {conversation_id} not found, retrying ({attempt + 1}/{retries})...")
            await asyncio.sleep(retry_delay)

    return None

async def get_conversation_messages(conversation_id: str, session: AsyncSession | None = None) -> list[ModelMessage]:
    """Retrieve conversation messages parsed back into PydanticAI ModelMessage objects."""
    
    async with provide_session(session) as s:
        conversation = await get_agent_conversation(s, conversation_id)
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

    Uses result.all_messages() which includes the full conversation.
    This replaces the existing conversation in DB (upsert behavior).
    """
    logfire.info(f"persist_agent_run_result called: conversation_id={conversation_id}, agent_name={agent_name}")
    if not conversation_id:
        logfire.warning("No conversation_id provided for persistence")
        return

    try:
        async with AsyncSessionFactory() as session:
            all_messages = result.all_messages()
            logfire.debug(f"Persisting {len(all_messages)} messages for conversation {conversation_id}")

            await save_agent_conversation(
                session=session,
                conversation_id=conversation_id,
                agent_name=agent_name,
                messages=all_messages,
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


async def list_user_conversations(
    user_id: str,
    agent_name: str,
    limit: int = 20,
) -> list[dict]:
    """
    List conversations for a specific user and agent.

    Returns conversations sorted by updated_at desc with summary info.
    """
    async with AsyncSessionFactory() as session:
        stmt = (
            select(AgentConversation)
            .where(AgentConversation.agent_name == agent_name)
            .where(AgentConversation.user_id == user_id)
            .order_by(desc(AgentConversation.updated_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        conversations = result.scalars().all()

        conv_list = []
        for conv in conversations:
            messages = await get_conversation_messages(conv.id, session=session)

            # Extract first user message as preview
            preview = ""
            for msg in messages:
                if isinstance(msg, ModelRequest):
                    for part in msg.parts:
                        # UserPromptPart contains the user's message text
                        if isinstance(part, UserPromptPart):
                            # UserPromptPart.content can be str or list
                            content = part.content
                            if isinstance(content, str):
                                preview = content[:100]
                            elif isinstance(content, list):
                                # Extract text from list of content parts
                                for item in content:
                                    if isinstance(item, str):
                                        preview = item[:100]
                                        break
                                    elif hasattr(item, 'text'):
                                        preview = item.text[:100]
                                        break
                            break
                    if preview:
                        break

            # Check for pending approval (tool call without return)
            has_pending = _has_pending_tool_call(messages)

            conv_list.append({
                "conversation_id": conv.id,
                "agent_name": conv.agent_name,
                "user_id": conv.user_id,
                "preview": preview,
                "has_pending": has_pending,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
            })

        return conv_list


def _has_pending_tool_call(messages: list[ModelMessage]) -> bool:
    """Check if there's a tool call without a corresponding return."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

    returned_tool_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    returned_tool_ids.add(part.tool_call_id)

    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    if part.tool_call_id not in returned_tool_ids:
                        return True
    return False
