"""
Background agent runner for Sernia AI triggers.

Runs the Sernia agent outside of an HTTP request context (no Clerk user,
no streaming). Used by SMS and email triggers to process events autonomously.

Push notifications are intentionally limited to HITL approval requests
(``notify_pending_approval``). Generic trigger-completion alerts were
removed to reduce notification noise — humans pull conversations from web
chat instead.
"""

import time
import uuid

import logfire

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.config import (
    GOOGLE_DELEGATION_EMAIL,
    TRIGGER_BOT_ID,
    TRIGGER_BOT_NAME,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.models import is_sernia_ai_enabled
from api.src.sernia_ai.runs import RunOutcome, run_and_finalize

# ---------------------------------------------------------------------------
# Rate limiter — prevents the same trigger key from firing more than once
# within RATE_LIMIT_SECONDS.  In-memory only; resets on restart (fine for
# this use-case since the scheduler re-registers on startup anyway).
# ---------------------------------------------------------------------------
RATE_LIMIT_SECONDS = 120  # 2 minutes

# {rate_limit_key: last_run_epoch}
_trigger_cooldowns: dict[str, float] = {}


def _is_rate_limited(key: str) -> bool:
    """Return True if *key* has been triggered within the cooldown window."""
    now = time.monotonic()
    last = _trigger_cooldowns.get(key)
    if last is not None and (now - last) < RATE_LIMIT_SECONDS:
        return True
    _trigger_cooldowns[key] = now
    return False


async def execute_trigger_run(
    *,
    trigger_source: str,
    trigger_prompt: str,
    trigger_metadata: dict,
    conversation_id: str,
    auto_approved_reply_domains: frozenset[str] = frozenset(),
) -> RunOutcome | None:
    """Run the agent as the trigger bot and finish it through the shared pipeline.

    No gates: the kill switch and any throttling are the caller's job.
    ``run_agent_for_trigger`` adds them for fire-and-forget triggers; the
    Zillow queue worker calls this directly because it needs the outcome
    (to retry a failed run) and does its own gating.

    Returns the run's outcome, or None if the run crashed.
    """
    async with AsyncSessionFactory() as session:
        deps = SerniaDeps(
            db_session=session,
            conversation_id=conversation_id,
            user_identifier=TRIGGER_BOT_ID,
            user_name=TRIGGER_BOT_NAME,
            user_email=GOOGLE_DELEGATION_EMAIL,
            modality="web_chat",
            workspace_path=WORKSPACE_PATH,
            auto_approved_reply_domains=auto_approved_reply_domains,
        )
        # The run, the save and the approval push all happen in the shared
        # pipeline (see sernia_ai/runs.py). Generic trigger-completion alerts
        # were removed to cut notification noise — only a pending approval
        # pushes; humans pull everything else from web chat.
        return await run_and_finalize(
            trigger_prompt,
            deps=deps,
            trigger_source=trigger_source,
            clerk_user_id=TRIGGER_BOT_ID,
            metadata=trigger_metadata,
        )


async def run_agent_for_trigger(
    trigger_source: str,
    trigger_prompt: str,
    trigger_metadata: dict,
    rate_limit_key: str | None = None,
    conversation_id: str | None = None,
) -> str | None:
    """
    Run the Sernia agent in background for a trigger event.

    Args:
        trigger_source: Origin of the trigger ("ai_sms", "email", "zillow_email_event", "scheduled_check").
        trigger_prompt: Synthetic user message describing the event. Each caller
            owns their full prompt — include any decision framework, output
            structure, or skill references directly in the prompt (or in the
            workspace skill markdown the prompt points to).
        trigger_metadata: Stored in conversation metadata_ JSON for frontend display.
        rate_limit_key: Cooldown key (e.g. phone number for SMS). When provided,
            the trigger is skipped if the same key fired within the last 2 minutes.
            Falls back to trigger_source if not provided.
        conversation_id: Pre-generated conversation ID. When provided, the runner
            uses this instead of generating a new UUID. Useful when the caller
            needs to embed a deeplink in the trigger prompt.

    Returns:
        The conversation_id if a conversation was created (agent needs human attention),
        or None if the agent processed silently or was rate-limited.

    Push notifications: only HITL approval requests fire a push. Generic
    completion alerts are intentionally suppressed.
    """
    # --- Sernia AI enabled check (universal kill switch) ---
    if not await is_sernia_ai_enabled():
        logfire.info("sernia_ai disabled — skipping trigger", trigger_source=trigger_source)
        return None

    # --- Rate-limit check ---
    cooldown_key = f"{trigger_source}:{rate_limit_key or '_global'}"
    if _is_rate_limited(cooldown_key):
        logfire.info(
            "trigger rate-limited — skipping",
            trigger_source=trigger_source,
            rate_limit_key=cooldown_key,
            cooldown_seconds=RATE_LIMIT_SECONDS,
        )
        return None

    # Classify trigger type for dashboard grouping (tags on logfire spans)
    _trigger_tag = {
        "ai_sms": "trigger:event",
        "email": "trigger:event",
        "zillow_email": "trigger:event",
        "zillow_email_event": "trigger:event",
        "scheduled_check": "trigger:scheduled",
    }.get(trigger_source, "trigger:unknown")

    conv_id = conversation_id or str(uuid.uuid4())

    outcome = await execute_trigger_run(
        trigger_source=trigger_source,
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        conversation_id=conv_id,
    )

    if outcome is None:  # the run crashed; already logged and saved as partial
        return None

    if not outcome.persisted:
        # Without a saved conversation there is nothing for a human to open.
        logfire.error(
            "trigger conversation save failed",
            trigger_source=trigger_source,
            conversation_id=conv_id,
            _tags=[_trigger_tag],
        )
        return None

    if not outcome.needs_attention:
        logfire.info(
            "trigger processed silently — no action needed",
            trigger_source=trigger_source,
            conversation_id=conv_id,
            reason=outcome.no_action_reason,
            prompt_preview=trigger_prompt[:300],
            _tags=[_trigger_tag],
        )
        return None

    logfire.info(
        "trigger conversation created",
        trigger_source=trigger_source,
        conversation_id=conv_id,
        has_pending=bool(outcome.pending),
        _tags=[_trigger_tag],
    )
    return conv_id
