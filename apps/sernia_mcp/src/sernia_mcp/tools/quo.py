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
    get_call_details_core,
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
async def quo_get_call_details(call_id: str, transcript_max_chars: int = 4000) -> str:
    """Fetch a Quo call's summary AND transcript in one shot, rendered as
    markdown (summary on top with next steps, transcript below). Use this with
    the Call ID surfaced by ``quo_list_active_sms_threads`` or
    ``quo_get_thread_messages``.

    Args:
        call_id: The Quo call ID (``AC...``).
        transcript_max_chars: Max characters of transcript dialogue to include
            (default 4000). Pass a larger value when you need the full
            transcript of a long call.
    """
    try:
        return await get_call_details_core(
            call_id, transcript_max_chars=transcript_max_chars,
        )
    except CoreError as e:
        raise ToolError(f"quo_get_call_details failed: {e}") from e


@mcp.tool
async def quo_get_thread_messages(
    phone_number: str | list[str],
    max_results: int = 20,
) -> str:
    """Get the recent thread (SMS + calls) with a phone number on the shared
    team line, OR a group thread by passing a list of phones.

    Returns SMS messages and calls interleaved chronologically, enriched with
    contact names. Call entries include the Call ID — pass it to
    ``quo_get_call_details`` to read the call's summary + transcript.

    **Group threads** (multiple phones, partial data): OpenPhone's public
    API does not list group-thread history by participant filter, so this
    tool can only return the *most recent* group activity plus each
    participant's 1:1 history. Older group messages exist but are not
    retrievable through this MCP server — view them in the OpenPhone app.
    The output includes a caveat block making this explicit. To find the
    participant set for a group conversation, use
    ``quo_list_active_sms_threads`` (it shows all participants per thread).

    Args:
        phone_number: A single phone in E.164 (1:1 thread) OR a list of
            phones (group thread).
        max_results: Max items per type to return per participant
            (default 20 messages + 20 calls).
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
    """List active conversation threads on the shared team line.

    Mirrors the Quo active inbox: returns all non-'done' threads (Quo marks
    'done' by snoozing 100+ years out), enriched with contact names, sorted
    by most recent activity. Each thread's snippet shows whichever activity
    is most recent — SMS or call. Call snippets include the Call ID so you
    can pass it to ``quo_get_call_details`` for the summary + transcript.

    Use this for "what threads need attention right now" — for a specific
    conversation's full thread history, use ``quo_get_thread_messages``.

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
