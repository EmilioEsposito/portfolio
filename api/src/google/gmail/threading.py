"""Gmail reply metadata shared by tool-driven and dedicated lead delivery."""

from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import get_gmail_service

GMAIL_SCOPES = [
    "https://mail.google.com",
    "https://www.googleapis.com/auth/gmail.readonly",
]


async def _get_threading_headers(message_id: str, user_email: str) -> dict:
    """Fetch threading metadata for replying to a message.

    Returns dict with thread_id, in_reply_to, references, or empty dict on failure.
    Note: thread_id is mailbox-specific — caller must ensure user_email matches
    the mailbox used for sending, otherwise the Gmail API will 404.
    """
    import logfire

    try:
        service = get_gmail_service(get_delegated_credentials(user_email, GMAIL_SCOPES))
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

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        rfc_message_id = headers.get("message-id", "")

        if not rfc_message_id:
            logfire.warn("No Message-ID header found for reply", message_id=message_id)
            return {}

        existing_refs = headers.get("references", "")
        new_refs = f"{existing_refs} {rfc_message_id}".strip()

        return {
            "thread_id": msg.get("threadId"),
            "in_reply_to": rfc_message_id,
            "references": new_refs,
        }
    except Exception:
        logfire.exception("Failed to fetch threading headers", message_id=message_id)
        return {}
