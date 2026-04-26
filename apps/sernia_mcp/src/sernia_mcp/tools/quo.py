"""MCP wrappers for Quo (OpenPhone) read tools.

Send tools live in ``approvals.py`` — they use the FastMCPApp pattern for
deterministic server-side enforcement (tool-visibility split).
"""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.errors import CoreError
from sernia_mcp.core.quo.contacts import (
    get_thread_messages_core,
    list_active_threads_core,
    search_contacts_core,
)
from sernia_mcp.server import mcp


@mcp.tool
async def quo_search_contacts(query: str) -> str:
    """Fuzzy-search Quo (OpenPhone) contacts by name, phone number, or company.

    Returns the top matching contacts as JSON. Tolerates typos.
    """
    try:
        return await search_contacts_core(query)
    except CoreError as e:
        raise ToolError(f"quo_search_contacts failed: {e}") from e


@mcp.tool
async def quo_get_thread_messages(phone_number: str, max_results: int = 20) -> str:
    """Get recent SMS messages with a phone number on the shared team line.

    Returns chronological messages enriched with contact names.

    Args:
        phone_number: Recipient phone in E.164 format (e.g. "+14155550100").
        max_results: Max messages to return (default 20).
    """
    try:
        return await get_thread_messages_core(phone_number, max_results=max_results)
    except CoreError as e:
        raise ToolError(f"quo_get_thread_messages failed: {e}") from e


@mcp.tool
async def quo_list_active_sms_threads(
    max_results: int = 20,
    updated_after_days: int | None = None,
) -> str:
    """List active SMS conversation threads on the shared team line.

    Mirrors the Quo active inbox: returns all non-'done' threads (Quo marks
    'done' by snoozing 100+ years out), enriched with contact names and a
    one-line snippet of the last message, sorted by most recent activity.

    Use this for "what threads need attention right now" — for a specific
    conversation's full message history, use ``quo_get_thread_messages``.

    Args:
        max_results: Max threads to return (default 20).
        updated_after_days: Optional — only include threads updated within
            this many days. Omit for all active threads (matches Quo inbox).
    """
    try:
        return await list_active_threads_core(
            max_results=max_results,
            updated_after_days=updated_after_days,
        )
    except CoreError as e:
        raise ToolError(f"quo_list_active_sms_threads failed: {e}") from e
