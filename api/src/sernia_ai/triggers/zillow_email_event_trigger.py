"""
Zillow email event trigger for the Sernia AI agent.

Fires when new Zillow emails arrive via Gmail Pub/Sub, but **debounced**:
the first email starts a debounce window (default 5 min); any additional
Zillow emails during that window are accumulated.  The agent fires once at
the end of the window so it can assess all accumulated emails in a single
run.

Configuration: debounce length and the HITL approval gate are both
overridable via the DB-backed ``zillow_email_config`` AppSetting (JSONB):

    {
        "debounce_seconds": 300,
        "require_approval": true
    }

Defaults come from ``DEFAULT_ZILLOW_DEBOUNCE_SECONDS`` and
``DEFAULT_ZILLOW_REQUIRE_APPROVAL`` in ``config.py``. ``require_approval=False``
opts the agent's outbound Zillow replies out of the standard external email
HITL approval card (the agent calls ``send_email`` directly for these runs).

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
from sqlalchemy import select

from api.src.sernia_ai.config import (
    DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
    DEFAULT_ZILLOW_REQUIRE_APPROVAL,
    FRONTEND_BASE_URL,
)
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

# TTL for the recently-fired message_id cache. Must comfortably exceed the
# debounce window so a redelivery landing after a batch fires still dedupes.
# The runtime debounce is configurable up to 3600s, so a 2-hour TTL covers
# even the longest valid window.
RECENTLY_FIRED_TTL_SECONDS = 7200  # 2 hours

# Module-level state for the debounce window.
# _pending_emails accumulates email info dicts; _pending_task is the asyncio
# task that sleeps for the configured debounce and then fires the trigger.
_pending_emails: list[dict] = []
_pending_task: asyncio.Task | None = None

# {message_id: monotonic_epoch_when_fired} — TTL cache of message_ids that
# were already included in a fired batch. Used to reject pubsub redeliveries
# that arrive shortly after a window closes.
_recently_fired_message_ids: dict[str, float] = {}


async def get_zillow_email_config() -> dict:
    """Read ``zillow_email_config`` from the DB, returning defaults if not set.

    Defaults come from ``config.DEFAULT_ZILLOW_*`` so the constants stay the
    single source of truth. Failures fall back to defaults — never raises.
    """
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.models import AppSetting

    defaults = {
        "debounce_seconds": DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
        "require_approval": DEFAULT_ZILLOW_REQUIRE_APPROVAL,
    }
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "zillow_email_config")
            )
            row = result.scalar_one_or_none()
            if isinstance(row, dict):
                return {
                    "debounce_seconds": int(row.get("debounce_seconds", defaults["debounce_seconds"])),
                    "require_approval": bool(row.get("require_approval", defaults["require_approval"])),
                }
    except Exception:
        logfire.warn("Failed to read zillow_email_config from DB, using defaults")
    return defaults


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

    The first email in a quiet period starts the debounce timer (length
    configurable via ``zillow_email_config.debounce_seconds`` — see
    ``get_zillow_email_config``). Subsequent emails within that window are
    accumulated.  When the timer fires, the agent runs once and sees every
    email in the batch.

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

    config = await get_zillow_email_config()
    debounce_seconds = config["debounce_seconds"]
    logfire.info(
        "zillow_email_event: starting debounce window",
        debounce_seconds=debounce_seconds,
        subject=subject,
    )
    _pending_task = asyncio.create_task(_debounced_fire(debounce_seconds))


async def _debounced_fire(debounce_seconds: int) -> None:
    """Sleep for the debounce window, then fire the trigger with all accumulated emails."""
    await asyncio.sleep(debounce_seconds)

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

    config = await get_zillow_email_config()
    require_approval = config["require_approval"]

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

    conversation_id = await run_agent_for_trigger(
        trigger_source="zillow_email_event",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        rate_limit_key="zillow_batch",
        conversation_id=conv_id,
        bypass_external_email_approval=not require_approval,
    )

    logfire.info(
        "zillow_email_event: batched trigger completed",
        email_count=len(emails),
        conversation_id=conversation_id,
        draft_created=conversation_id is not None,
        require_approval=require_approval,
    )

    return conversation_id
