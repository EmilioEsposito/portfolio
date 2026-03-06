"""
Zillow email event trigger for the Sernia AI agent.

Fires in real-time when a new Zillow email arrives via Gmail Pub/Sub.
The agent drafts a response (Phase 1: no email sent) and sends a push
notification to Emilio for review.

Naming convention: all public symbols use the ``zillow_email_event`` root.
"""

import uuid
from textwrap import dedent

import logfire

from api.src.sernia_ai.config import EMILIO_CONTACT_SLUG, FRONTEND_BASE_URL
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

# Module-level cache for Emilio's clerk_user_id (looked up once from DB).
_emilio_clerk_user_id: str | None = None


async def _get_emilio_clerk_user_id() -> str | None:
    """Look up Emilio's clerk_user_id via contact slug, caching at module level."""
    global _emilio_clerk_user_id
    if _emilio_clerk_user_id is not None:
        return _emilio_clerk_user_id

    try:
        from api.src.contact.service import get_clerk_user_id_by_slug

        clerk_id = await get_clerk_user_id_by_slug(EMILIO_CONTACT_SLUG)
        if clerk_id:
            _emilio_clerk_user_id = clerk_id
            logfire.info("zillow_email_event: cached emilio clerk_user_id", clerk_user_id=clerk_id)
            return clerk_id
        logfire.warn("zillow_email_event: no user found for contact slug", slug=EMILIO_CONTACT_SLUG)
    except Exception:
        logfire.exception("zillow_email_event: failed to look up emilio clerk_user_id")
    return None


def is_zillow_email(from_address: str) -> bool:
    """Return True if the sender is a Zillow email address."""
    if not from_address:
        return False
    addr = from_address.lower()
    return addr.endswith("@zillow.com") or "@" in addr and addr.split("@", 1)[1].endswith(".zillow.com")


async def handle_zillow_email_event(
    *,
    thread_id: str,
    subject: str,
    from_address: str,
    body_text: str | None,
) -> str | None:
    """
    Process a newly arrived Zillow email in real-time.

    Called from the Gmail Pub/Sub webhook after the email is saved to the DB.
    The agent reads the full thread via its email tools, drafts a reply,
    and we push-notify Emilio with the result.

    Returns the conversation_id if a draft was created, None otherwise.
    """
    logfire.info(
        "zillow_email_event: handling",
        thread_id=thread_id,
        subject=subject,
        from_address=from_address,
    )

    # Pre-generate conversation ID so we can embed a deeplink in the prompt
    conv_id = str(uuid.uuid4())
    deeplink = f"{FRONTEND_BASE_URL}/sernia-chat?id={conv_id}"

    # Build a snippet of the email body for the trigger prompt
    body_snippet = ""
    if body_text:
        body_snippet = body_text[:500].strip()
        if len(body_text) > 500:
            body_snippet += "..."

    trigger_prompt = dedent(f"""\
        New Zillow email arrived. Load the zillow-auto-reply skill and follow it.

        **Email details:**
        - Thread ID (Gmail): {thread_id}
        - Subject: {subject}
        - From: {from_address}
        - Body preview: {body_snippet}
        - Conversation deeplink: {deeplink}

        Search/read the full email thread for context, then draft or NoAction.""")

    trigger_metadata = {
        "trigger_source": "zillow_email_event",
        "trigger_type": "email_event",
        "thread_id": thread_id,
        "subject": subject,
        "from_address": from_address,
    }

    emilio_clerk_id = await _get_emilio_clerk_user_id()

    conversation_id = await run_agent_for_trigger(
        trigger_source="zillow_email_event",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        notification_title="Zillow Draft Ready",
        notification_body=f"Re: {subject[:80]}",
        rate_limit_key=f"thread:{thread_id}",
        notify_clerk_user_id=emilio_clerk_id,
        conversation_id=conv_id,
    )

    logfire.info(
        "zillow_email_event: completed",
        thread_id=thread_id,
        subject=subject,
        conversation_id=conversation_id,
        draft_created=conversation_id is not None,
        notify_target=emilio_clerk_id or "none (fallback to broadcast)",
    )

    return conversation_id
