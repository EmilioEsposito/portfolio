"""MCP wrappers for Quo (OpenPhone) read tools.

Send tools live in ``approvals.py`` — they use the FastMCPApp pattern for
deterministic server-side enforcement (tool-visibility split).
"""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.errors import CoreError
from sernia_mcp.core.quo.contact_writes import (
    CustomField,
    Email,
    PhoneNumber,
    create_contact_core,
)
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


@mcp.tool
async def quo_create_contact(
    first_name: str,
    last_name: str,
    company: str | None = None,
    role: str | None = None,
    phone_numbers: list[PhoneNumber] | None = None,
    emails: list[Email] | None = None,
    tags: list[str] | None = None,
    custom_fields: list[CustomField] | None = None,
) -> str:
    """Create a new Quo (OpenPhone) contact.

    Args:
        first_name: Contact's first name.
        last_name: Contact's last name.
        company: Company name (e.g. "Sernia Capital LLC").
        role: Role or type (e.g. "Tenant", "Lead", "Vendor").
        phone_numbers: Phone numbers. Each entry is ``{name, value}`` with
            ``value`` in E.164 (e.g. ``"+14125551234"``). Default name is
            ``"Phone Number"``.
        emails: Email addresses. Each entry is ``{name, value}``.
        tags: Tags to apply (multi-select; e.g. ``["Insurance", "Vendor"]``).
        custom_fields: Additional custom fields as ``{key, value}`` entries.
            ``key`` is the 24-char hex Quo custom-field ID.
    """
    try:
        return await create_contact_core(
            first_name,
            last_name,
            company=company,
            role=role,
            phone_numbers=phone_numbers,
            emails=emails,
            tags=tags,
            custom_fields=custom_fields,
        )
    except CoreError as e:
        raise ToolError(f"quo_create_contact failed: {e}") from e
