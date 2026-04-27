"""MCP wrappers for Gmail read tools (domain-wide delegation).

Send tools live in ``approvals.py``.
"""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.errors import CoreError
from sernia_mcp.core.google.drive import read_google_sheet_core, search_drive_core
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
