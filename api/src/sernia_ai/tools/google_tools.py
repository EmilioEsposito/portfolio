"""
Google tools — Gmail, Calendar, and Drive.

Wraps api/src/google/gmail/service.py, api/src/google/calendar/service.py,
and Google Drive/Docs/Sheets APIs.
All operations delegate as ctx.deps.user_email via service account credentials.
"""

import io
from datetime import datetime, timedelta
from typing import Annotated

import pytz
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext

from api.src.google.calendar.service import (
    CalendarEventInput,
    create_calendar_event as _create_calendar_event,
    delete_calendar_event as _delete_calendar_event,
    get_calendar_service,
)
from api.src.google.common.service_account_auth import get_delegated_credentials
from pydantic import EmailStr
from pydantic.fields import Field

from api.src.sernia_ai.config import SHARED_EXTERNAL_EMAIL, INTERNAL_EMAIL_DOMAIN
from api.src.google.gmail.service import (
    extract_email_body,
    get_email_content,
    get_gmail_service,
    send_email as _send_email,
)
from api.src.sernia_ai.deps import SerniaDeps

# Reusable annotated type for tools that accept an inbox/calendar override
UserInboxEmail = Annotated[
    EmailStr | None,
    Field(
        default=None,
        description=(
            "Email address whose mailbox/calendar to use. "
            "Defaults to the current user. Use 'all@serniacapital.com' for the shared inbox."
        ),
    ),
]


def _html_to_markdown(html: str) -> str:
    """Convert HTML email to clean markdown for LLM consumption.

    Email HTML uses layout tables extensively, so we strip table tags
    (keeping their text) and remove non-content elements before converting.
    """
    import re
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    # Remove tags whose *content* should be discarded (not just the tag)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "img"]):
        tag.decompose()
    cleaned = str(soup)

    # Convert remaining HTML → markdown, stripping layout table tags
    result = md(cleaned, strip=["table", "tr", "td", "th", "tbody", "thead", "tfoot"])
    # Collapse excessive blank lines
    return re.sub(r"\n{3,}", "\n\n", result).strip()


google_toolset = FunctionToolset()

GMAIL_SCOPES = [
    "https://mail.google.com",
    "https://www.googleapis.com/auth/gmail.readonly",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Max text content to return from a single document read
_DOC_CONTENT_CAP = 8_000


def _get_drive_service(user_email: str):
    creds = get_delegated_credentials(user_email=user_email, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def _get_sheets_service(user_email: str):
    creds = get_delegated_credentials(user_email=user_email, scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=creds)


def _get_docs_service(user_email: str):
    creds = get_delegated_credentials(
        user_email=user_email,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("docs", "v1", credentials=creds)


async def _get_threading_headers(message_id: str, user_email: str) -> dict:
    """Fetch threading metadata for replying to a message.

    Returns dict with thread_id, in_reply_to, references, or empty dict on failure.
    Note: thread_id is mailbox-specific — caller must ensure user_email matches
    the mailbox used for sending, otherwise the Gmail API will 404.
    """
    import logfire

    try:
        service = get_gmail_service(
            get_delegated_credentials(user_email, GMAIL_SCOPES)
        )
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Message-ID", "References"],
            )
            .execute()
        )

        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        rfc_message_id = headers.get("message-id", "")

        if not rfc_message_id:
            logfire.warn(
                "No Message-ID header found for reply", message_id=message_id
            )
            return {}

        existing_refs = headers.get("references", "")
        new_refs = f"{existing_refs} {rfc_message_id}".strip()

        return {
            "thread_id": msg.get("threadId"),
            "in_reply_to": rfc_message_id,
            "references": new_refs,
        }
    except Exception as e:
        logfire.exception("Failed to fetch threading headers", message_id=message_id)
        return {}


@google_toolset.tool(requires_approval=True)
async def send_external_email(
    ctx: RunContext[SerniaDeps],
    to: list[EmailStr],
    subject: str,
    body: str,
    reply_to_message_id: str = "",
) -> str:
    """Send an email to external recipients (requires approval).

    May include internal @serniacapital.com addresses (e.g. CC'ing the team),
    but at least one recipient should be external. For internal-only emails,
    use send_internal_email (no approval needed).

    Args:
        to: List of recipient email addresses (e.g. ["tenant@gmail.com"]).
        subject: Email subject line.
        body: Plain text email body.
        reply_to_message_id: Optional Gmail message ID to reply to (threads the email).
    """
    import logfire

    logfire.info("send_external_email called", to=to, subject=subject[:80])

    if not to:
        return "Blocked: no recipients provided."

    thread_kwargs: dict = {}
    if reply_to_message_id:
        # Try shared mailbox first (send mailbox), fall back to user's mailbox
        thread_kwargs = await _get_threading_headers(
            reply_to_message_id, SHARED_EXTERNAL_EMAIL
        )
        if not thread_kwargs and ctx.deps.user_email != SHARED_EXTERNAL_EMAIL:
            logfire.info(
                "reply_to_message_id not found in shared mailbox, trying user inbox",
                message_id=reply_to_message_id,
                user_email=ctx.deps.user_email,
            )
            thread_kwargs = await _get_threading_headers(
                reply_to_message_id, ctx.deps.user_email
            )
            # Drop threadId — it's from user's mailbox, not the send mailbox
            thread_kwargs.pop("thread_id", None)

    to_str = ", ".join(addr.strip() for addr in to)
    credentials = get_delegated_credentials(
        user_email=SHARED_EXTERNAL_EMAIL,
        scopes=GMAIL_SCOPES,
    )
    result = await _send_email(
        to=to_str,
        subject=subject,
        message_text=body,
        sender=ctx.deps.user_email,
        credentials=credentials,
        **thread_kwargs,
    )
    logfire.info("send_external_email success", to=to_str)
    return f"Email sent to {to_str} (message ID: {result.get('id', 'unknown')})."


@google_toolset.tool
async def send_internal_email(
    ctx: RunContext[SerniaDeps],
    to: list[EmailStr],
    subject: str,
    body: str,
    reply_to_message_id: str = "",
) -> str:
    """Send an email to Sernia Capital team members (no approval needed).

    All recipients must be @serniacapital.com addresses. If any recipient is
    external, the tool blocks — use send_external_email instead.

    Args:
        to: List of recipient email addresses
            (e.g. ["emilio@serniacapital.com"] or ["emilio@serniacapital.com", "all@serniacapital.com"]).
        subject: Email subject line.
        body: Plain text email body.
        reply_to_message_id: Optional Gmail message ID to reply to (threads the email).
    """
    import logfire

    logfire.info("send_internal_email called", to=to, subject=subject[:80])

    if not to:
        return "Blocked: no recipients provided."

    # Gate: all recipients must be internal
    for addr in to:
        if not addr.strip().lower().endswith(f"@{INTERNAL_EMAIL_DOMAIN}"):
            logfire.warn("send_internal_email blocked: external recipient", to=addr)
            return (
                f"Blocked: {addr} is not a @{INTERNAL_EMAIL_DOMAIN} address. "
                "Use send_external_email if ANY recipient is external."
            )

    thread_kwargs: dict = {}
    if reply_to_message_id:
        thread_kwargs = await _get_threading_headers(
            reply_to_message_id, ctx.deps.user_email
        )

    to_str = ", ".join(addr.strip() for addr in to)
    credentials = get_delegated_credentials(
        user_email=ctx.deps.user_email,
        scopes=GMAIL_SCOPES,
    )
    result = await _send_email(
        to=to_str,
        subject=subject,
        message_text=body,
        sender=ctx.deps.user_email,
        credentials=credentials,
        **thread_kwargs,
    )
    logfire.info("send_internal_email success", to=to_str)
    return f"Email sent to {to_str} (message ID: {result.get('id', 'unknown')})."


@google_toolset.tool
async def search_emails(
    ctx: RunContext[SerniaDeps],
    query: str,
    user_inbox_email: UserInboxEmail = None,
    max_results: int = 10,
) -> str:
    """Search emails using Gmail search syntax.

    Args:
        query: Gmail search query (e.g. "from:john subject:rent", "is:unread", "newer_than:7d").
        max_results: Maximum number of results to return (default 10).
    """
    if not user_inbox_email:
        user_inbox_email = ctx.deps.user_email
    credentials = get_delegated_credentials(
        user_email=user_inbox_email,
        scopes=GMAIL_SCOPES,
    )
    service = get_gmail_service(credentials)

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    messages = results.get("messages", [])
    if not messages:
        return f"No emails found for query: {query}"

    lines = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="metadata",
                 metadataHeaders=["Subject", "From", "Date"])
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        lines.append(
            f"[{headers.get('date', '?')}] From: {headers.get('from', '?')}\n"
            f"  Subject: {headers.get('subject', '(no subject)')}\n"
            f"  Snippet: {msg.get('snippet', '')}\n"
            f"  ID: {msg_ref['id']}  Thread: {msg_ref.get('threadId', '?')}"
        )
    return "\n\n".join(lines)


async def _read_email(message_id: str, user_email: str, text_only: bool = True) -> str:
    credentials = get_delegated_credentials(
        user_email=user_email,
        scopes=GMAIL_SCOPES,
    )
    service = get_gmail_service(credentials)
    message = await get_email_content(service, message_id)

    if not message:
        return f"Email {message_id} not found (may have been deleted)."

    headers = {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    body = extract_email_body(message)
    content = body.get("text") or body.get("html") or "(no body)"

    if text_only and content == body.get("html"):
        content = _html_to_markdown(content)


    thread_id = message.get("threadId", "?")

    return (
        f"From: {headers.get('from', '?')}\n"
        f"To: {headers.get('to', '?')}\n"
        f"Date: {headers.get('date', '?')}\n"
        f"Subject: {headers.get('subject', '(no subject)')}\n"
        f"Thread ID: {thread_id}\n\n"
        f"{content}"
    )

@google_toolset.tool
async def read_email(
    ctx: RunContext[SerniaDeps],
    message_id: str,
    user_inbox_email: UserInboxEmail = None,
) -> str:
    """Read the full content of an email by its Gmail message ID.

    Args:
        message_id: The Gmail message ID (returned by search_emails).
    """
    result = await _read_email(message_id, user_inbox_email or ctx.deps.user_email)

    # truncate to 5000 characters
    if len(result) > 5000:
        result = result[:5000] + "\n...[TRUNCATED: WARN USER]"

    return result

def _strip_quoted_replies(text: str) -> str:
    """Strip quoted reply blocks from email text.

    In a thread view each message is already shown in full, so re-quoted
    text (3+ consecutive lines starting with '>') is redundant.
    Also strips the 'On ... wrote:' attribution line that precedes the block.
    """
    import re

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(">"):
            # Count consecutive '>' lines
            start = i
            while i < len(lines) and lines[i].strip().startswith(">"):
                i += 1
            if i - start >= 3:
                # Also remove the "On ... wrote:" attribution above
                while result and result[-1].strip() == "":
                    result.pop()
                if result and re.search(r"wrote:\s*$", result[-1]):
                    result.pop()
                    # Attribution can wrap across two lines (name on one, "wrote:" on next)
                    if result and re.search(r"^On .+", result[-1]):
                        result.pop()
                    # Clean trailing blank lines again
                    while result and result[-1].strip() == "":
                        result.pop()
                result.append("[...quoted reply trimmed...]")
            else:
                # Short quote — keep it (could be an inline quote)
                result.extend(lines[start:i])
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


@google_toolset.tool
async def read_email_thread(
    ctx: RunContext[SerniaDeps],
    thread_id: str,
    user_inbox_email: UserInboxEmail = None,
) -> str:
    """Read all messages in an email thread, in chronological order.

    Use this to understand the full back-and-forth of a conversation.
    The thread_id is returned by search_emails and read_email.
    Quoted replies are stripped out, giving a more concise view of the conversation.

    Args:
        thread_id: The Gmail thread ID.
    """
    inbox = user_inbox_email or ctx.deps.user_email
    credentials = get_delegated_credentials(user_email=inbox, scopes=GMAIL_SCOPES)
    service = get_gmail_service(credentials)

    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    messages = thread.get("messages", [])
    if not messages:
        return f"Thread {thread_id} has no messages."

    parts: list[str] = []
    for i, msg in enumerate(messages, 1):
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        body = extract_email_body(msg)
        content = body.get("text") or body.get("html") or "(no body)"
        if content == body.get("html"):
            content = _html_to_markdown(content)

        # Strip redundant quoted replies (each message already shown in full)
        content = _strip_quoted_replies(content)

        # Cap individual message body
        if len(content) > 3000:
            content = content[:3000] + "\n...[truncated]"

        parts.append(
            f"--- Message {i}/{len(messages)} ---\n"
            f"From: {headers.get('from', '?')}\n"
            f"To: {headers.get('to', '?')}\n"
            f"Date: {headers.get('date', '?')}\n"
            f"Subject: {headers.get('subject', '(no subject)')}\n\n"
            f"{content}"
        )

    result = "\n\n".join(parts)
    # Cap total output
    if len(result) > 15000:
        result = result[:15000] + "\n\n...[THREAD TRUNCATED — too many messages]"
    return result


@google_toolset.tool
async def list_calendar_events(
    ctx: RunContext[SerniaDeps],
    days_ahead: int = 7,
    user_inbox_email: UserInboxEmail = None,
) -> str:
    """List upcoming calendar events.

    Args:
        days_ahead: Number of days ahead to look (default 7).
    """
    service = await get_calendar_service(user_email=user_inbox_email or ctx.deps.user_email)
    et_tz = pytz.timezone("US/Eastern")
    now = datetime.now(tz=et_tz)
    time_max = now + timedelta(days=days_ahead)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])

    if not events:
        return f"No calendar events in the next {days_ahead} days."

    lines = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))
        summary = event.get("summary", "(no title)")
        event_id = event.get("id", "?")
        attendees = event.get("attendees", [])
        attendee_str = ", ".join(a.get("email", "?") for a in attendees) if attendees else "none"
        lines.append(
            f"- {summary}\n"
            f"  Start: {start} | End: {end}\n"
            f"  Event ID: {event_id}\n"
            f"  Attendees: {attendee_str}"
        )
    return "\n".join(lines)


@google_toolset.tool
async def create_calendar_event(
    ctx: RunContext[SerniaDeps],
    event: CalendarEventInput,
) -> str:
    """Create a Google Calendar event. Requires approval if any attendee is external.

    Always include all attendees explicitly — no one is auto-added.
    Reminders default to email 1 day before + popup 1 hour before.
    Default timezone is US/Eastern.
    """
    has_external = event.attendees and any(
        not a.email.endswith(f"@{INTERNAL_EMAIL_DOMAIN}") for a in event.attendees
    )
    if has_external and not ctx.tool_call_approved:
        raise ApprovalRequired()

    # Use the shared mailbox as organizer so attendees receive email invites
    service = await get_calendar_service(user_email=SHARED_EXTERNAL_EMAIL)

    result = await _create_calendar_event(
        service, event, organizer_email=SHARED_EXTERNAL_EMAIL, overwrite=True
    )
    event_link = result.get("htmlLink", "")
    return f"Calendar event created: {event.summary}\nLink: {event_link}"


@google_toolset.tool
async def delete_calendar_event_tool(
    ctx: RunContext[SerniaDeps],
    event_id: str,
) -> str:
    """Delete a Google Calendar event (always requires approval).

    Args:
        event_id: The Google Calendar event ID to delete.
    """
    if not ctx.tool_call_approved:
        raise ApprovalRequired()

    service = await get_calendar_service(user_email=SHARED_EXTERNAL_EMAIL)
    await _delete_calendar_event(service, event_id)
    return f"Calendar event {event_id} deleted."


# =============================================================================
# Google Drive
# =============================================================================


@google_toolset.tool
async def search_drive(
    ctx: RunContext[SerniaDeps],
    query: str,
    max_results: int = 20,
) -> str:
    """Search Google Drive for files and folders.

    Args:
        query: Search text. Matches file names and content.
        max_results: Maximum number of results to return (default 10).
    """
    service = _get_drive_service(ctx.deps.user_email)
    # Drive API query: fullText contains 'query' or name contains 'query'
    q = f"(name contains '{query}' or fullText contains '{query}') and trashed = false"
    results = (
        service.files()
        .list(
            q=q,
            pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, webViewLink)",
        )
        .execute()
    )
    files = results.get("files", [])
    if not files:
        return f"No Drive files found for '{query}'."

    lines = []
    for f in files:
        mime = f.get("mimeType", "")
        # Friendly type label
        type_label = {
            "application/vnd.google-apps.document": "Google Doc",
            "application/vnd.google-apps.spreadsheet": "Google Sheet",
            "application/vnd.google-apps.presentation": "Google Slides",
            "application/vnd.google-apps.folder": "Folder",
            "application/pdf": "PDF",
        }.get(mime, mime.split("/")[-1] if "/" in mime else mime)

        lines.append(
            f"- {f['name']} ({type_label})\n"
            f"  Modified: {f.get('modifiedTime', '?')}\n"
            f"  ID: {f['id']}\n"
            f"  Link: {f.get('webViewLink', 'N/A')}"
        )
    return "\n".join(lines)


@google_toolset.tool
async def read_google_doc(
    ctx: RunContext[SerniaDeps],
    file_id: str,
) -> str:
    """Read the text content of a Google Doc.

    Args:
        file_id: The Google Drive file ID (returned by search_drive).
    """
    service = _get_docs_service(ctx.deps.user_email)
    doc = service.documents().get(documentId=file_id).execute()
    title = doc.get("title", "(untitled)")

    # Extract text from the document body
    content_parts: list[str] = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run:
                content_parts.append(text_run.get("content", ""))

    text = "".join(content_parts).strip()
    if len(text) > _DOC_CONTENT_CAP:
        text = text[:_DOC_CONTENT_CAP] + "\n...(truncated)"

    return f"Title: {title}\n\n{text}" if text else f"Title: {title}\n\n(empty document)"


@google_toolset.tool
async def read_google_sheet(
    ctx: RunContext[SerniaDeps],
    file_id: str,
    sheet_name: str | None = None,
    range: str | None = None,
) -> str:
    """Read data from a Google Sheet.

    Args:
        file_id: The Google Drive file ID (returned by search_drive).
        sheet_name: Optional sheet/tab name (defaults to first sheet).
        range: Optional A1 range (e.g. "A1:D20"). Reads entire sheet if omitted.
    """
    service = _get_sheets_service(ctx.deps.user_email)

    # Build range string
    if range and sheet_name:
        range_str = f"'{sheet_name}'!{range}"
    elif sheet_name:
        range_str = sheet_name
    elif range:
        range_str = range
    else:
        range_str = ""

    try:
        if range_str:
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=range_str)
                .execute()
            )
        else:
            # Get metadata to find first sheet name, then read it
            meta = service.spreadsheets().get(spreadsheetId=file_id).execute()
            first_sheet = meta["sheets"][0]["properties"]["title"]
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=first_sheet)
                .execute()
            )
    except HttpError as e:
        # For invalid range/sheet name errors, fetch available sheet names
        # so the agent can retry with the correct name instead of guessing.
        if e.resp.status in (400, 404):
            try:
                meta = service.spreadsheets().get(spreadsheetId=file_id).execute()
                available = [s["properties"]["title"] for s in meta["sheets"]]
                return (
                    f"Error: {e._get_reason()}. "
                    f"Available sheets in this spreadsheet: {', '.join(available)}"
                )
            except Exception:
                pass
        raise  # Re-raise for other HTTP errors (500, auth, etc.)

    rows = result.get("values", [])
    if not rows:
        return "Sheet is empty."

    # Export CSV for DuckDB analysis (best-effort, >5 rows threshold)
    dataset_info = ""
    if len(rows) > 5 and ctx.deps.conversation_id:
        from api.src.sernia_ai.tools.data_export import write_dataset, _sanitize_name
        ds_name = _sanitize_name(sheet_name or "sheet")
        try:
            _, rows_written = write_dataset(
                ctx.deps.conversation_id, ds_name, headers=rows[0], rows=rows[1:]
            )
            # Build a sample row for context
            sample = rows[1] if len(rows) > 1 else []
            sample_padded = sample + [""] * (len(rows[0]) - len(sample))
            sample_pairs = ", ".join(
                f"{h}={v!r}" for h, v in zip(rows[0][:6], sample_padded[:6])
            )
            if len(rows[0]) > 6:
                sample_pairs += ", ..."
            dataset_info = (
                f"[Dataset saved as '{ds_name}' ({rows_written} rows, "
                f"{len(rows[0])} cols). Use load_dataset(\"{ds_name}\") then "
                f"run_sql() for filtering/aggregation.]\n"
                f"[Columns: {', '.join(rows[0])}]\n"
                f"[Sample: {sample_pairs}]\n\n"
            )
        except Exception:
            pass

    # Format as a readable table
    lines = []
    # Header row
    headers = rows[0]
    lines.append(" | ".join(str(h) for h in headers))
    lines.append("-" * len(lines[0]))
    # Data rows (cap at 100)
    for row in rows[1:101]:
        padded = row + [""] * (len(headers) - len(row))
        lines.append(" | ".join(str(v) for v in padded))
    if len(rows) > 101:
        lines.append(f"...(showing 100 of {len(rows) - 1} data rows)")

    text = "\n".join(lines)
    if len(text) > _DOC_CONTENT_CAP:
        text = text[:_DOC_CONTENT_CAP] + "\n...(truncated)"
    return dataset_info + text


@google_toolset.tool
async def read_drive_pdf(
    ctx: RunContext[SerniaDeps],
    file_id: str,
) -> str:
    """Read text content from a PDF file stored in Google Drive.

    Args:
        file_id: The Google Drive file ID (returned by search_drive).
    """
    service = _get_drive_service(ctx.deps.user_email)

    # Get file metadata to check type
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    name = meta.get("name", "unknown")
    mime = meta.get("mimeType", "")

    # For Google Docs, export as plain text instead
    if mime == "application/vnd.google-apps.document":
        return await read_google_doc(ctx, file_id)

    # Download PDF content
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)
    pdf_bytes = buffer.read()

    # Try to extract text with pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        text = "\n\n".join(text_parts)
    except ImportError:
        return f"PDF '{name}' downloaded ({len(pdf_bytes)} bytes) but pypdf is not installed for text extraction."

    if not text.strip():
        return f"PDF '{name}' appears to be image-based (no extractable text). {len(pdf_bytes)} bytes."

    if len(text) > _DOC_CONTENT_CAP:
        text = text[:_DOC_CONTENT_CAP] + "\n...(truncated)"

    return f"PDF: {name}\n\n{text}"
