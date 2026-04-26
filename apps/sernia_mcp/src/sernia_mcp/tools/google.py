"""MCP wrappers for Gmail read tools (domain-wide delegation).

Send tools live in ``approvals.py``.
"""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.errors import CoreError
from sernia_mcp.core.google.gmail import read_email_core, search_emails_core
from sernia_mcp.identity import resolve_user_email_for_request
from sernia_mcp.server import mcp


@mcp.tool
async def google_search_emails(query: str, max_results: int = 10) -> str:
    """Search Gmail using full Gmail search syntax.

    Examples: ``from:john subject:rent``, ``in:inbox is:unread``, ``newer_than:7d``.
    Returns message IDs, thread IDs, and snippets.
    """
    try:
        return await search_emails_core(
            query,
            user_email=resolve_user_email_for_request(),
            max_results=max_results,
        )
    except CoreError as e:
        raise ToolError(f"google_search_emails failed: {e}") from e


@mcp.tool
async def google_read_email(message_id: str) -> str:
    """Read a full email by its Gmail message ID.

    Args:
        message_id: Gmail message ID (from ``google_search_emails`` output).
    """
    try:
        return await read_email_core(
            message_id,
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        raise ToolError(f"google_read_email failed: {e}") from e
