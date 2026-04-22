"""
Zillow email event trigger for the Sernia AI agent.

Fires when new Zillow emails arrive via Gmail Pub/Sub, but **debounced**:
the first email starts a 10-minute window; any additional Zillow emails
during that window are accumulated.  The agent fires once at the end of
the window so it can assess all accumulated emails in a single run.

Deduplication: Gmail Pub/Sub has at-least-once delivery, and a single logical
message can show up in multiple history notifications (e.g. when Gmail label
state changes). We dedupe by Gmail ``message_id`` at two levels:

1. Within the current debounce window — skip if the id is already pending.
2. Across recent windows — a short TTL "recently fired" cache guards against
   a redelivery arriving moments after we fired the previous batch.

Naming convention: all public symbols use the ``zillow_email_event`` root.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from textwrap import dedent

import logfire

from api.src.sernia_ai.config import EMILIO_CONTACT_SLUG, FRONTEND_BASE_URL
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

# ---------------------------------------------------------------------------
# Debounce configuration
# ---------------------------------------------------------------------------
DEBOUNCE_SECONDS = 600  # 10 minutes

# TTL for the recently-fired message_id cache. Must comfortably exceed the
# debounce window so a redelivery landing after a batch fires still dedupes.
RECENTLY_FIRED_TTL_SECONDS = 3600  # 1 hour

# Module-level state for the debounce window.
# _pending_emails accumulates email info dicts; _pending_task is the asyncio
# task that sleeps for DEBOUNCE_SECONDS and then fires the trigger.
_pending_emails: list[dict] = []
_pending_task: asyncio.Task | None = None

# {message_id: monotonic_epoch_when_fired} — TTL cache of message_ids that
# were already included in a fired batch. Used to reject pubsub redeliveries
# that arrive shortly after a window closes.
_recently_fired_message_ids: dict[str, float] = {}


def _prune_recently_fired(now: float | None = None) -> None:
    """Drop entries older than RECENTLY_FIRED_TTL_SECONDS from the TTL cache."""
    if now is None:
        now = time.monotonic()
    expired = [
        mid for mid, ts in _recently_fired_message_ids.items()
        if (now - ts) > RECENTLY_FIRED_TTL_SECONDS
    ]
    for mid in expired:
        _recently_fired_message_ids.pop(mid, None)

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


# ---------------------------------------------------------------------------
# Public API — called from pubsub webhook
# ---------------------------------------------------------------------------

async def queue_zillow_email_event(
    *,
    thread_id: str,
    message_id: str = "",
    subject: str,
    from_address: str,
    body_text: str | None,
) -> None:
    """
    Queue a Zillow email for debounced processing.

    The first email in a quiet period starts a 10-minute timer.  All
    subsequent emails within that window are accumulated.  When the timer
    fires, the agent runs once and sees every email in the batch.

    Duplicate Gmail ``message_id`` values (pubsub redelivery, label-change
    history events for the same physical message) are dropped: once already
    pending or already fired within the TTL, additional calls for the same
    id become no-ops.
    """
    global _pending_task

    # Dedupe by Gmail message_id. Empty message_id falls through (can't dedupe
    # without an identifier) but we should never see that in practice.
    if message_id:
        _prune_recently_fired()
        if message_id in _recently_fired_message_ids:
            logfire.info(
                "zillow_email_event: dropping duplicate (recently fired)",
                message_id=message_id,
                subject=subject,
            )
            return
        if any(e["message_id"] == message_id for e in _pending_emails):
            logfire.info(
                "zillow_email_event: dropping duplicate (already pending)",
                message_id=message_id,
                subject=subject,
            )
            return

    email_info = {
        "thread_id": thread_id,
        "message_id": message_id,
        "subject": subject,
        "from_address": from_address,
        "body_text": body_text,
    }
    _pending_emails.append(email_info)

    if _pending_task is not None and not _pending_task.done():
        logfire.info(
            "zillow_email_event: batched with pending trigger",
            pending_count=len(_pending_emails),
            subject=subject,
        )
        return

    logfire.info(
        "zillow_email_event: starting debounce window",
        debounce_seconds=DEBOUNCE_SECONDS,
        subject=subject,
    )
    _pending_task = asyncio.create_task(_debounced_fire())


async def _debounced_fire() -> None:
    """Sleep for the debounce window, then fire the trigger with all accumulated emails."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Snapshot and clear the pending list atomically
    emails = _pending_emails.copy()
    _pending_emails.clear()

    if not emails:
        return

    # Mark all fired message_ids as recently-fired so redeliveries landing
    # after this window closes still dedupe.
    now = time.monotonic()
    _prune_recently_fired(now)
    for email in emails:
        mid = email.get("message_id")
        if mid:
            _recently_fired_message_ids[mid] = now

    logfire.info(
        "zillow_email_event: debounce window closed, firing trigger",
        email_count=len(emails),
    )

    try:
        await _fire_batched_trigger(emails)
    except Exception:
        logfire.exception("zillow_email_event: batched trigger failed")


# ---------------------------------------------------------------------------
# Trigger execution
# ---------------------------------------------------------------------------

async def _fire_batched_trigger(emails: list[dict]) -> str | None:
    """Run the agent once for a batch of Zillow emails."""
    conv_id = str(uuid.uuid4())
    deeplink = f"{FRONTEND_BASE_URL}/sernia-chat?id={conv_id}"

    # Build a summary of all emails in the batch
    if len(emails) == 1:
        email = emails[0]
        body_snippet = ""
        if email.get("body_text"):
            body_snippet = email["body_text"][:500].strip()
            if len(email["body_text"]) > 500:
                body_snippet += "..."
        email_details = dedent(f"""\
            **Email details:**
            - Gmail Message ID (all@ account): {email['message_id']}
            - Thread ID (Gmail): {email['thread_id']}
            - Subject: {email['subject']}
            - From: {email['from_address']}
            - Body preview: {body_snippet}""")
        reply_hint = f'When replying, pass reply_to_message_id="{email["message_id"]}" to thread correctly.'
    else:
        lines = []
        for i, email in enumerate(emails, 1):
            lines.append(
                f"{i}. Subject: {email['subject']} | From: {email['from_address']} "
                f"| Message ID: {email['message_id']} | Thread ID: {email['thread_id']}"
            )
        email_details = "**Emails received (oldest first):**\n" + "\n".join(lines)
        reply_hint = (
            "For each email that needs a reply, pass the corresponding reply_to_message_id to thread correctly."
        )

    subjects_preview = ", ".join(e["subject"][:50] for e in emails[:3])
    if len(emails) > 3:
        subjects_preview += f" (+{len(emails) - 3} more)"

    trigger_prompt = dedent(f"""\
        {len(emails)} new Zillow email(s) arrived. Load the zillow-auto-reply skill and follow it.

        {email_details}
        - Conversation deeplink: {deeplink}

        Read each email using its Message ID, then draft replies or NoAction per email.
        {reply_hint}
        Always search/read from all@serniacapital.com (use user_email_account="all@serniacapital.com").""")

    trigger_metadata = {
        "trigger_source": "zillow_email_event",
        "trigger_type": "email_event",
        "email_count": len(emails),
        "subjects": [e["subject"] for e in emails],
        "thread_ids": list({e["thread_id"] for e in emails}),
    }

    emilio_clerk_id = await _get_emilio_clerk_user_id()

    conversation_id = await run_agent_for_trigger(
        trigger_source="zillow_email_event",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        notification_title=f"Zillow Draft Ready ({len(emails)} email{'s' if len(emails) != 1 else ''})",
        notification_body=f"Re: {subjects_preview}",
        rate_limit_key="zillow_batch",
        notify_clerk_user_id=emilio_clerk_id,
        conversation_id=conv_id,
    )

    logfire.info(
        "zillow_email_event: batched trigger completed",
        email_count=len(emails),
        conversation_id=conversation_id,
        draft_created=conversation_id is not None,
    )

    return conversation_id
