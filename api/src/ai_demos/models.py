import asyncio
from datetime import datetime
from typing import Any
from sqlalchemy import String, Integer, DateTime, func, JSON, select, desc, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic_core import to_jsonable_python
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
import json
from pydantic_ai.agent import AgentRunResult
import logfire

from api.src.database.database import Base, AsyncSessionFactory, provide_session

class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        # Composite index for efficient listing by user + agent with ordering
        Index("ix_agent_conv_user_agent_updated", "clerk_user_id", "agent_name", "updated_at"),
    )

    # conversation_id is used as the primary key
    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    agent_name: Mapped[str] = mapped_column(String, index=True)
    # clerk_user_id is the Clerk user ID (e.g., "user_2abc123...") - used for ownership
    # Matches convention in User model (user/models.py)
    clerk_user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # user_email for display/debugging convenience
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Sernia agent additions
    modality: Mapped[str | None] = mapped_column(String, index=True, nullable=True, server_default="web_chat")
    contact_identifier: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

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
    clerk_user_id: str,
    retries: int = 3,
    retry_delay: float = 0.5,
) -> AgentConversation | None:
    """
    Retrieve an agent conversation by ID for a specific user.

    The clerk_user_id filter is applied in the SQL query - returns None if
    conversation doesn't exist OR doesn't belong to user.

    Retries help handle race conditions where the conversation may not be
    persisted yet when approval is clicked quickly after streaming completes.
    """
    for attempt in range(retries):
        stmt = select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.clerk_user_id == clerk_user_id,
        )
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation is not None:
            return conversation

        # Only retry if we didn't find it and have attempts left
        if attempt < retries - 1:
            logfire.info(f"Conversation {conversation_id} not found for user {clerk_user_id}, retrying ({attempt + 1}/{retries})...")
            await asyncio.sleep(retry_delay)

    return None

async def get_conversation_messages(
    conversation_id: str,
    clerk_user_id: str,
    session: AsyncSession | None = None,
    retries: int = 3,
    retry_delay: float = 0.5,
) -> list[ModelMessage]:
    """
    Retrieve conversation messages for a specific user.

    Returns empty list if conversation doesn't exist or doesn't belong to user.
    """
    async with provide_session(session) as s:
        conversation = await get_agent_conversation(
            s, conversation_id, clerk_user_id, retries=retries, retry_delay=retry_delay
        )
        if conversation and conversation.messages:
            return ModelMessagesTypeAdapter.validate_python(conversation.messages)
        return []

async def _get_user_email_from_clerk(user_id: str) -> str | None:
    """Look up user email from Clerk given a user ID."""
    try:
        from api.src.utils.clerk import clerk_client
        user = clerk_client.users.get(user_id=user_id)
        if user and user.email_addresses:
            # Prefer verified email
            for email in user.email_addresses:
                if email.verification and email.verification.status == "verified":
                    return email.email_address
            # Fallback to first email
            return user.email_addresses[0].email_address
    except Exception as e:
        logfire.warning(f"Failed to get email for user {user_id}: {e}")
    return None


async def save_agent_conversation(
    session: AsyncSession,
    conversation_id: str,
    agent_name: str,
    messages: list[ModelMessage] | list[Any],
    clerk_user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    estimated_tokens: int | None = None,
) -> AgentConversation:
    """
    Save or update an agent conversation.
    Uses SQLAlchemy's merge for idiomatic upsert behavior.

    Args:
        clerk_user_id: Clerk user ID (e.g., "user_2abc123...") - used for ownership
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
        logfire.exception("Failed to convert messages to JSON, using fallback")
        # Fallback: simple to_jsonable_python which might leave bytes as is,
        # but we iterate to stringify bytes to avoid DB errors
        data = to_jsonable_python(messages)
        messages_json = _sanitize_json(data)

    # Look up user email from Clerk for convenience/debugging
    user_email = None
    if clerk_user_id:
        user_email = await _get_user_email_from_clerk(clerk_user_id)

    # Use session.merge which performs an upsert (SELECT + INSERT/UPDATE)
    # This is idiomatic SQLAlchemy ORM.
    conversation = AgentConversation(
        id=conversation_id,
        agent_name=agent_name,
        clerk_user_id=clerk_user_id,
        user_email=user_email,
        messages=messages_json,
        metadata_=metadata,
    )
    if estimated_tokens is not None:
        conversation.estimated_tokens = estimated_tokens

    # merge returns the persistent instance attached to the session
    conversation = await session.merge(conversation)
    await session.commit()

    return conversation

async def persist_agent_run_result(
    result: AgentRunResult,
    conversation_id: str,
    agent_name: str,
    clerk_user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Convenience function to persist an agent run result to the database.
    Handles session creation and error logging.

    Uses result.all_messages() which includes the full conversation.
    This replaces the existing conversation in DB (upsert behavior).

    IMPORTANT: This function uses asyncio.shield to protect against cancellation.
    When called from a streaming on_complete callback, client disconnection can
    cancel the parent task. Shield ensures persistence completes regardless.

    Args:
        result: The agent run result to persist
        conversation_id: Unique ID for the conversation
        agent_name: Name of the agent
        clerk_user_id: Clerk user ID (e.g., "user_2abc123...") - used for ownership
        metadata: Optional metadata to store
        session: Optional existing session (uses provide_session if not provided)
    """
    logfire.info(f"persist_agent_run_result called: conversation_id={conversation_id}, agent_name={agent_name}")
    if not conversation_id:
        logfire.warning("No conversation_id provided for persistence")
        return

    async def _do_persist():
        """Inner function that does the actual persistence."""
        try:
            async with provide_session(session) as s:
                all_messages = result.all_messages()
                logfire.debug(f"Persisting {len(all_messages)} messages for conversation {conversation_id}")

                # Extract total token usage for diagnostics
                total_tokens: int | None = None
                try:
                    usage = result.usage()
                    total_tokens = usage.total_tokens if usage.total_tokens else None
                except Exception:
                    pass

                await save_agent_conversation(
                    session=s,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    messages=all_messages,
                    clerk_user_id=clerk_user_id,
                    metadata=metadata,
                    estimated_tokens=total_tokens,
                )
            logfire.info(f"Conversation {conversation_id} saved to database for agent {agent_name} and clerk_user_id {clerk_user_id}")
        except Exception as e:
            logfire.error(f"Failed to save conversation {conversation_id}: {e}")

    # Spawn as a background task and shield from cancellation
    task = asyncio.create_task(_do_persist())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Parent was cancelled (e.g., client disconnected), but task continues
        logfire.info(f"Persistence shielded from cancellation for {conversation_id}")

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
    clerk_user_id: str,
    agent_name: str,
    limit: int = 20,
    pending_only: bool = False,
    session: AsyncSession | None = None,
) -> list[dict]:
    """
    List conversations for a specific user and agent.

    Args:
        clerk_user_id: Clerk user ID
        agent_name: Name of the agent
        limit: Maximum number of conversations to return
        pending_only: If True, only return conversations with pending approvals
        session: Optional existing session

    Returns conversations sorted by updated_at desc with summary info.
    """
    from sqlalchemy import text

    async with provide_session(session) as s:
        # Use raw SQL to extract preview at DB level - avoids loading full messages JSON
        # Preview is extracted from: messages[0].parts[0].content (first user message)
        query = text("""
            SELECT
                id,
                agent_name,
                clerk_user_id,
                LEFT(messages -> 0 -> 'parts' -> 0 ->> 'content', 100) as preview,
                created_at,
                updated_at
            FROM agent_conversations
            WHERE agent_name = :agent_name
              AND clerk_user_id = :clerk_user_id
            ORDER BY updated_at DESC
            LIMIT :limit
        """)

        result = await s.execute(query, {
            "agent_name": agent_name,
            "clerk_user_id": clerk_user_id,
            "limit": limit,
        })
        rows = result.fetchall()

        conv_list = []
        for row in rows:
            # For pending_only filter, we need to check pending status
            # This requires loading the full messages - only do it when filtering
            has_pending = False
            pending: list[dict] = []

            if pending_only:
                # Load full conversation to check pending status
                conv = await get_agent_conversation(s, row.id, clerk_user_id)
                if conv and conv.messages:
                    messages = ModelMessagesTypeAdapter.validate_python(conv.messages)
                    pending = extract_pending_approval_from_messages(messages)
                    has_pending = len(pending) > 0
                    if not has_pending:
                        continue  # Skip non-pending conversations

            conv_list.append({
                "conversation_id": row.id,
                "agent_name": row.agent_name,
                "clerk_user_id": row.clerk_user_id,
                "preview": row.preview or "",
                "pending": pending,
                "has_pending": has_pending,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            })

        return conv_list


async def delete_conversation(
    conversation_id: str,
    clerk_user_id: str,
    session: AsyncSession | None = None,
) -> bool:
    """
    Delete a conversation if it belongs to the specified user.

    Uses a single query with both conversation_id and clerk_user_id filters.

    Args:
        conversation_id: The conversation ID to delete
        clerk_user_id: The Clerk user ID that should own the conversation
        session: Optional existing session

    Returns:
        True if deleted successfully

    Raises:
        ValueError: If conversation not found or user doesn't own it
    """
    from sqlalchemy import delete

    async with provide_session(session) as s:
        # Single delete with both filters - only deletes if user owns it
        stmt = delete(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.clerk_user_id == clerk_user_id,
        )
        result = await s.execute(stmt)
        await s.commit()

        # Check if any row was deleted
        if result.rowcount == 0:
            raise ValueError(f"Conversation not found: {conversation_id}")

        logfire.info(f"Deleted conversation {conversation_id} for user {clerk_user_id}")
        return True


# =============================================================================
# Pending Approval Utilities
# =============================================================================

def extract_pending_approval_from_messages(messages: list[ModelMessage]) -> list[dict]:
    """
    Extract all pending approval info from stored messages.

    A tool call is pending if:
    1. There's a ToolCallPart in a ModelResponse
    2. There's NO corresponding ToolReturnPart in a subsequent ModelRequest

    This works because:
    - First run: [ModelRequest(user), ModelResponse(ToolCallPart)] - pending
    - After approval: [ModelRequest(user), ModelResponse(ToolCallPart), ModelRequest(ToolReturnPart), ModelResponse(text)] - not pending

    Returns a list of pending approvals (empty if none).
    """
    # Collect all tool_call_ids that have been returned (executed)
    returned_tool_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    returned_tool_ids.add(part.tool_call_id)

    # Find all tool calls that haven't been returned yet (pending approval)
    pending: list[dict] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    if part.tool_call_id not in returned_tool_ids:
                        pending.append({
                            "tool_call_id": part.tool_call_id,
                            "tool_name": part.tool_name,
                            "args": json.loads(part.args) if isinstance(part.args, str) else part.args,
                        })
    return pending


async def get_conversation_with_pending(
    conversation_id: str,
    clerk_user_id: str,
    session: AsyncSession | None = None,
) -> dict | None:
    """
    Get a conversation and check if it has a pending approval.

    Returns None if conversation doesn't exist or doesn't belong to user.

    Returns:
        {
            "conversation_id": str,
            "messages": list[ModelMessage],
            "pending": dict | None,  # approval info if pending
            "agent_name": str,
            "clerk_user_id": str,
            "created_at": datetime,
            "updated_at": datetime,
        }
    """
    async with provide_session(session) as s:
        conversation = await get_agent_conversation(s, conversation_id, clerk_user_id)
        if not conversation:
            return None

        messages = await get_conversation_messages(conversation_id, clerk_user_id, session=s)
        pending = extract_pending_approval_from_messages(messages)

        return {
            "conversation_id": conversation_id,
            "messages": messages,
            "pending": pending,
            "agent_name": conversation.agent_name,
            "clerk_user_id": conversation.clerk_user_id,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }


async def list_pending_conversations(
    agent_name: str,
    clerk_user_id: str,
    limit: int = 50,
    session: AsyncSession | None = None,
) -> list[dict]:
    """
    List conversations that have pending approvals for a specific user.

    This is a convenience wrapper around list_user_conversations with pending_only=True.
    """
    return await list_user_conversations(
        clerk_user_id=clerk_user_id,
        agent_name=agent_name,
        limit=limit,
        pending_only=True,
        session=session,
    )
