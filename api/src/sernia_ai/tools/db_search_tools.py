"""
Database search tools — conversation history search.

Queries the agent_conversations table for past conversation content.
"""

import logfire
from pydantic_ai import FunctionToolset, RunContext
from sqlalchemy import cast, select, String

from api.src.ai_demos.models import AgentConversation
from api.src.sernia_ai.deps import SerniaDeps

db_search_toolset = FunctionToolset()


@db_search_toolset.tool
async def search_conversations(
    ctx: RunContext[SerniaDeps],
    query: str,
    limit: int = 10,
) -> str:
    """Search past agent conversations for a text query.

    Args:
        query: Text to search for in conversation messages (case-insensitive).
        limit: Maximum number of results to return (default 10).
    """
    session = ctx.deps.db_session

    try:
        stmt = (
            select(AgentConversation)
            .where(
                cast(AgentConversation.messages, String).ilike(f"%{query}%"),
            )
            .order_by(AgentConversation.updated_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        conversations = result.scalars().all()

        if not conversations:
            return f"No conversations found matching '{query}'."

        lines = []
        for conv in conversations:
            ts = conv.updated_at.strftime("%Y-%m-%d %I:%M %p") if conv.updated_at else "?"
            # Extract a snippet from messages containing the query
            snippet = _extract_snippet(conv.messages, query)
            lines.append(
                f"[{ts}] agent: {conv.agent_name}, id: {conv.id}\n"
                f"  {snippet}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        logfire.error(f"search_conversations error: {e}")
        return f"Error searching conversations: {e}"


def _extract_snippet(messages: list[dict], query: str, max_len: int = 200) -> str:
    """Find the first message containing the query and return a snippet."""
    query_lower = query.lower()
    for msg in reversed(messages):  # Search newest first
        # Messages are stored as PydanticAI ModelMessage dicts
        for part in msg.get("parts", []):
            content = part.get("content", "")
            if isinstance(content, str) and query_lower in content.lower():
                idx = content.lower().index(query_lower)
                start = max(0, idx - 50)
                end = min(len(content), idx + max_len)
                snippet = content[start:end].replace("\n", " ")
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."
                return snippet
    return "(match in message metadata)"
