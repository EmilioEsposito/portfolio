"""Google Drive search + Google Sheets read.

Lift-and-shift from ``api/src/sernia_ai/tools/google_tools.py`` minus the
conversation-scoped CSV export (sernia_ai writes large sheets to a per-conv
DuckDB-friendly CSV; the MCP server has no equivalent conversation context,
so we just format and cap the inline output).
"""
from __future__ import annotations

import io

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from sernia_mcp.clients.google_auth import get_delegated_credentials

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive",
]

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Cap inline content returned to the model. Matches sernia_ai's _DOC_CONTENT_CAP.
_CONTENT_CAP = 8_000


def _get_drive_service(user_email: str):
    creds = get_delegated_credentials(user_email=user_email, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def _get_sheets_service(user_email: str):
    creds = get_delegated_credentials(user_email=user_email, scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=creds)


def _get_docs_service(user_email: str):
    """Docs API uses the broader Drive scope so the same delegation works."""
    creds = get_delegated_credentials(
        user_email=user_email,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("docs", "v1", credentials=creds)


async def search_drive_core(
    query: str,
    *,
    user_email: str,
    max_results: int = 20,
) -> str:
    """Search Google Drive by name + full-text content.

    Returns a formatted list with file IDs (the IDs feed into ``read_google_sheet``,
    ``read_google_doc``, etc.).
    """
    service = _get_drive_service(user_email)
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

    type_label_map = {
        "application/vnd.google-apps.document": "Google Doc",
        "application/vnd.google-apps.spreadsheet": "Google Sheet",
        "application/vnd.google-apps.presentation": "Google Slides",
        "application/vnd.google-apps.folder": "Folder",
        "application/pdf": "PDF",
    }

    lines: list[str] = []
    for f in files:
        mime = f.get("mimeType", "")
        type_label = type_label_map.get(
            mime, mime.split("/")[-1] if "/" in mime else mime
        )
        lines.append(
            f"- {f['name']} ({type_label})\n"
            f"  Modified: {f.get('modifiedTime', '?')}\n"
            f"  ID: {f['id']}\n"
            f"  Link: {f.get('webViewLink', 'N/A')}"
        )
    return "\n".join(lines)


async def read_google_sheet_core(
    file_id: str,
    *,
    user_email: str,
    sheet_name: str | None = None,
    range: str | None = None,
) -> str:
    """Read a Google Sheet's values.

    Args:
        file_id: Drive file ID (use ``search_drive`` to find).
        user_email: Workspace user to impersonate via domain delegation.
        sheet_name: Optional tab name (defaults to first sheet).
        range: Optional A1 range (e.g. ``"A1:D20"``). Reads entire sheet if omitted.

    On invalid sheet/range (Google returns 400/404), falls back to listing the
    available sheet names so the agent can retry without guessing.
    """
    service = _get_sheets_service(user_email)

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
            meta = service.spreadsheets().get(spreadsheetId=file_id).execute()
            first_sheet = meta["sheets"][0]["properties"]["title"]
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=first_sheet)
                .execute()
            )
    except HttpError as e:
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
        raise

    rows = result.get("values", [])
    if not rows:
        return "Sheet is empty."

    headers = rows[0]
    lines = [
        " | ".join(str(h) for h in headers),
        "-" * len(" | ".join(str(h) for h in headers)),
    ]
    for row in rows[1:101]:
        padded = list(row) + [""] * (len(headers) - len(row))
        lines.append(" | ".join(str(v) for v in padded))
    if len(rows) > 101:
        lines.append(f"...(showing 100 of {len(rows) - 1} data rows)")

    text = "\n".join(lines)
    if len(text) > _CONTENT_CAP:
        text = text[:_CONTENT_CAP] + "\n...(truncated)"
    return text


async def read_google_doc_core(file_id: str, *, user_email: str) -> str:
    """Read the text content of a Google Doc.

    Output is capped at ``_CONTENT_CAP`` chars. For PDFs use
    ``read_drive_pdf_core``; for sheets use ``read_google_sheet_core``.
    """
    service = _get_docs_service(user_email)
    doc = service.documents().get(documentId=file_id).execute()
    title = doc.get("title", "(untitled)")

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
    if len(text) > _CONTENT_CAP:
        text = text[:_CONTENT_CAP] + "\n...(truncated)"

    return f"Title: {title}\n\n{text}" if text else f"Title: {title}\n\n(empty document)"


async def read_drive_pdf_core(file_id: str, *, user_email: str) -> str:
    """Download a PDF from Drive and extract its text.

    If ``file_id`` points at a Google Doc (not a PDF), delegates to
    ``read_google_doc_core`` so callers don't have to branch upfront.
    Image-based PDFs return a "no extractable text" notice; ``pypdf`` does
    not OCR.
    """
    service = _get_drive_service(user_email)
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    name = meta.get("name", "unknown")
    mime = meta.get("mimeType", "")

    if mime == "application/vnd.google-apps.document":
        return await read_google_doc_core(file_id, user_email=user_email)

    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    pdf_bytes = buffer.getvalue()

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_parts: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text)
    text = "\n\n".join(text_parts)

    if not text.strip():
        return (
            f"PDF '{name}' appears to be image-based "
            f"(no extractable text). {len(pdf_bytes)} bytes."
        )

    if len(text) > _CONTENT_CAP:
        text = text[:_CONTENT_CAP] + "\n...(truncated)"

    return f"PDF: {name}\n\n{text}"
