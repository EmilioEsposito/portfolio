"""Gmail search, read, and send core functions. Google Workspace delegation."""
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import (
    extract_email_body,
    get_email_content,
    get_gmail_service,
    send_email as _service_send_email,
)
from api.src.tool_core.errors import ExternalServiceError
from api.src.tool_core.types import EmailSendResult

# Read + search + send. "https://mail.google.com/" is required to send
# per the check inside google.gmail.service.send_email().
GMAIL_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


async def search_emails_core(
    query: str,
    *,
    user_email: str,
    max_results: int = 10,
) -> str:
    """Search Gmail using full Gmail search syntax.

    Args:
        query: Gmail query string (e.g. "from:john subject:rent", "in:inbox is:unread").
        user_email: Google Workspace user whose mailbox to impersonate.
        max_results: Max messages to return.
    """
    credentials = get_delegated_credentials(user_email=user_email, scopes=GMAIL_SCOPES)
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

    lines: list[str] = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg_ref["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            )
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


async def read_email_core(message_id: str, *, user_email: str) -> str:
    """Read a full email by message ID.

    Args:
        message_id: Gmail message ID (from search_emails_core output).
        user_email: Google Workspace user whose mailbox to impersonate.
    """
    credentials = get_delegated_credentials(user_email=user_email, scopes=GMAIL_SCOPES)
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
    thread_id = message.get("threadId", "?")

    return (
        f"From: {headers.get('from', '?')}\n"
        f"To: {headers.get('to', '?')}\n"
        f"Date: {headers.get('date', '?')}\n"
        f"Subject: {headers.get('subject', '(no subject)')}\n"
        f"Message ID: {message_id}\n"
        f"Thread ID: {thread_id}\n\n"
        f"{content}"
    )


async def send_email_core(
    to: list[str],
    subject: str,
    body: str,
    *,
    user_email: str,
    sender_override: str | None = None,
) -> EmailSendResult:
    """Send an email via Gmail (domain-wide delegation).

    Args:
        to: Recipient email addresses. Joined with commas for the RFC header.
        subject: Email subject.
        body: Plain-text body.
        user_email: Google Workspace user whose mailbox to impersonate.
        sender_override: Optional From-address (e.g. shared "all@serniacapital.com"
            mailbox). Must be accessible to ``user_email``'s delegated credentials.

    Caller is responsible for any approval gating (e.g. rejecting external
    recipients). This function just sends.
    """
    credentials = get_delegated_credentials(
        user_email=sender_override or user_email,
        scopes=GMAIL_SCOPES,
    )
    to_header = ", ".join(to)
    try:
        sent = await _service_send_email(
            to=to_header,
            subject=subject,
            message_text=body,
            sender=sender_override,
            credentials=credentials,
        )
    except Exception as exc:  # service raises HTTPException on Gmail errors
        raise ExternalServiceError(f"Gmail send failed: {exc}") from exc

    return EmailSendResult(
        to=list(to),
        subject=subject,
        from_address=sender_override or user_email,
        message_id=sent.get("id") if isinstance(sent, dict) else None,
    )
