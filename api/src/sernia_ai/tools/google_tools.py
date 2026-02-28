"""
Google tools — Gmail, Calendar, and Drive.

Wraps api/src/google/gmail/service.py, api/src/google/calendar/service.py,
and Google Drive/Docs/Sheets APIs.
All operations delegate as ctx.deps.user_email via service account credentials.
"""

import io
from datetime import datetime, timedelta

import pytz
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pydantic_ai import FunctionToolset, RunContext

from api.src.google.calendar.service import (
    create_calendar_event as _create_calendar_event,
    get_calendar_service,
)
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import (
    extract_email_body,
    get_email_content,
    get_gmail_service,
    send_email as _send_email,
)
from api.src.sernia_ai.deps import SerniaDeps

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


@google_toolset.tool(requires_approval=True)
async def send_email(
    ctx: RunContext[SerniaDeps],
    to: str,
    subject: str,
    body: str,
) -> str:
    """Send an email via Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
    """
    credentials = get_delegated_credentials(
        user_email=ctx.deps.user_email,
        scopes=GMAIL_SCOPES,
    )
    result = await _send_email(
        to=to,
        subject=subject,
        message_text=body,
        sender=ctx.deps.user_email,
        credentials=credentials,
    )
    return f"Email sent to {to} (message ID: {result.get('id', 'unknown')})."


@google_toolset.tool
async def search_emails(
    ctx: RunContext[SerniaDeps],
    query: str,
    max_results: int = 10,
) -> str:
    """Search emails using Gmail search syntax.

    Args:
        query: Gmail search query (e.g. "from:john subject:rent", "is:unread", "newer_than:7d").
        max_results: Maximum number of results to return (default 10).
    """
    credentials = get_delegated_credentials(
        user_email=ctx.deps.user_email,
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
            f"  ID: {msg_ref['id']}"
        )
    return "\n\n".join(lines)


@google_toolset.tool
async def read_email(
    ctx: RunContext[SerniaDeps],
    message_id: str,
) -> str:
    """Read the full content of an email by its Gmail message ID.

    Args:
        message_id: The Gmail message ID (returned by search_emails).
    """
    credentials = get_delegated_credentials(
        user_email=ctx.deps.user_email,
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

    # Truncate very long emails
    if len(content) > 5000:
        content = content[:5000] + "\n...(truncated)"

    return (
        f"From: {headers.get('from', '?')}\n"
        f"To: {headers.get('to', '?')}\n"
        f"Date: {headers.get('date', '?')}\n"
        f"Subject: {headers.get('subject', '(no subject)')}\n\n"
        f"{content}"
    )


@google_toolset.tool
async def list_calendar_events(
    ctx: RunContext[SerniaDeps],
    days_ahead: int = 7,
) -> str:
    """List upcoming calendar events.

    Args:
        days_ahead: Number of days ahead to look (default 7).
    """
    service = await get_calendar_service(user_email=ctx.deps.user_email)
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
        attendees = event.get("attendees", [])
        attendee_str = ", ".join(a.get("email", "?") for a in attendees) if attendees else "none"
        lines.append(
            f"- {summary}\n"
            f"  Start: {start} | End: {end}\n"
            f"  Attendees: {attendee_str}"
        )
    return "\n".join(lines)


@google_toolset.tool(requires_approval=True)
async def create_calendar_event(
    ctx: RunContext[SerniaDeps],
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str | None = None,
    attendees: list[str] | None = None,
) -> str:
    """Create a Google Calendar event.

    Args:
        summary: Event title/summary.
        start_iso: Start time in ISO 8601 format (e.g. 2025-06-15T10:00:00-04:00).
        end_iso: End time in ISO 8601 format.
        description: Optional event description.
        attendees: Optional list of attendee email addresses.
    """
    service = await get_calendar_service(user_email=ctx.deps.user_email)

    event_body: dict = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    result = await _create_calendar_event(service, event_body)
    event_link = result.get("htmlLink", "")
    return f"Calendar event created: {summary}\nLink: {event_link}"


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

    rows = result.get("values", [])
    if not rows:
        return "Sheet is empty."

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
    return text


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
