"""Gmail search / read / send via Google domain-wide delegation."""
from __future__ import annotations

from sernia_mcp.clients.gmail import (
    GMAIL_SCOPES,
    extract_body,
    get_gmail_service,
    get_message,
    send_email_via_service,
)
from sernia_mcp.clients.google_auth import get_delegated_credentials
from sernia_mcp.core.errors import ExternalServiceError
from sernia_mcp.core.types import EmailSendResult


async def search_emails_core(
    query: str,
    *,
    user_email: str,
    max_results: int = 10,
) -> str:
    """Search Gmail using full Gmail search syntax.

    Args:
        query: Gmail query (e.g. ``from:john subject:rent``, ``in:inbox is:unread``).
        user_email: Workspace user to impersonate.
        max_results: Max messages to return.
    """
    creds = get_delegated_credentials(user_email=user_email, scopes=GMAIL_SCOPES)
    service = get_gmail_service(creds)

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
    """Read a full email by ID."""
    creds = get_delegated_credentials(user_email=user_email, scopes=GMAIL_SCOPES)
    service = get_gmail_service(creds)
    message = get_message(service, message_id)
    if not message:
        return f"Email {message_id} not found (may have been deleted)."

    headers = {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    body = extract_body(message)
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

    Caller is responsible for any approval gating.
    """
    creds = get_delegated_credentials(
        user_email=sender_override or user_email,
        scopes=GMAIL_SCOPES,
    )
    service = get_gmail_service(creds)
    try:
        sent = await send_email_via_service(
            service,
            to=", ".join(to),
            subject=subject,
            message_text=body,
            sender=sender_override or user_email,
        )
    except Exception as exc:
        raise ExternalServiceError(f"Gmail send failed: {exc}") from exc

    return EmailSendResult(
        to=list(to),
        subject=subject,
        from_address=sender_override or user_email,
        message_id=sent.get("id") if isinstance(sent, dict) else None,
    )
