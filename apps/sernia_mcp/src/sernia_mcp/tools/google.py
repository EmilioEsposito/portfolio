"""MCP wrappers for Gmail read tools (domain-wide delegation).

Send tools live in ``approvals.py``.
"""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.errors import CoreError
from sernia_mcp.core.google.calendar import list_calendar_events_core
from sernia_mcp.core.google.drive import (
    read_drive_pdf_core,
    read_google_doc_core,
    read_google_sheet_core,
    search_drive_core,
)
from sernia_mcp.core.google.gmail import (
    read_email_core,
    read_email_thread_core,
    search_emails_core,
)
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


@mcp.tool
async def google_search_drive(query: str, max_results: int = 20) -> str:
    """Search Google Drive for files and folders.

    The query matches both file names and full-text content. Returns
    formatted results with the file ID needed by ``google_read_sheet``,
    ``google_read_doc``, ``google_read_pdf``, etc.

    Args:
        query: Search text. Matches file names AND content.
        max_results: Maximum results to return (default 20).
    """
    try:
        return await search_drive_core(
            query,
            user_email=resolve_user_email_for_request(),
            max_results=max_results,
        )
    except CoreError as e:
        raise ToolError(f"google_search_drive failed: {e}") from e


@mcp.tool
async def google_read_sheet(
    file_id: str,
    sheet_name: str | None = None,
    range: str | None = None,
) -> str:
    """Read values from a Google Sheet.

    Output is a pipe-delimited table capped at 100 data rows / 8000 chars.
    For larger sheets, pass ``range`` to slice (e.g. ``"A1:D200"``). On an
    invalid sheet name or range, the tool falls back to listing the
    available sheet names so you can retry without guessing.

    Args:
        file_id: Drive file ID (from ``google_search_drive``).
        sheet_name: Optional tab name. Defaults to the first sheet.
        range: Optional A1 range (e.g. ``"A1:D20"``). Reads entire sheet
            if omitted (subject to row/char caps above).
    """
    try:
        return await read_google_sheet_core(
            file_id,
            user_email=resolve_user_email_for_request(),
            sheet_name=sheet_name,
            range=range,
        )
    except CoreError as e:
        raise ToolError(f"google_read_sheet failed: {e}") from e


@mcp.tool
async def google_read_doc(file_id: str) -> str:
    """Read the text content of a Google Doc.

    Args:
        file_id: The Google Drive file ID (from ``google_search_drive``).
    """
    try:
        return await read_google_doc_core(
            file_id,
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        raise ToolError(f"google_read_doc failed: {e}") from e


@mcp.tool
async def google_read_pdf(file_id: str) -> str:
    """Read text content from a PDF stored in Google Drive.

    If the file ID actually points at a Google Doc (not a PDF), the call
    transparently falls back to ``google_read_doc``. Image-based PDFs
    return a "no extractable text" notice — pypdf does not OCR.

    Args:
        file_id: The Google Drive file ID (from ``google_search_drive``).
    """
    try:
        return await read_drive_pdf_core(
            file_id,
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        raise ToolError(f"google_read_pdf failed: {e}") from e


@mcp.tool
async def google_read_email_thread(thread_id: str) -> str:
    """Read all messages in a Gmail thread, in chronological order.

    Each message is rendered with From / To / Date / Subject and its
    Message ID — pass that ID into a future ``google_send_email`` call's
    ``reply_to_message_id`` to reply within the thread.

    Args:
        thread_id: Gmail thread ID (from ``google_search_emails`` or
            ``google_read_email`` output).
    """
    try:
        return await read_email_thread_core(
            thread_id,
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        raise ToolError(f"google_read_email_thread failed: {e}") from e


@mcp.tool
async def google_list_calendar_events(
    days_ahead: int = 7,
    days_behind: int = 0,
) -> str:
    """List the user's primary-calendar events around now.

    Always includes today (window starts at midnight ET of
    ``today - days_behind``, ends at ``now + days_ahead``).

    Args:
        days_ahead: Days forward from now (default 7).
        days_behind: Days backward from today's midnight (default 0 — today only).
    """
    try:
        return await list_calendar_events_core(
            user_email=resolve_user_email_for_request(),
            days_ahead=days_ahead,
            days_behind=days_behind,
        )
    except CoreError as e:
        raise ToolError(f"google_list_calendar_events failed: {e}") from e
