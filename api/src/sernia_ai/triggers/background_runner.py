"""
Background agent runner for Sernia AI triggers.

Runs the Sernia agent outside of an HTTP request context (no Clerk user,
no streaming). Used by SMS and email triggers to process events autonomously.

When the agent decides human attention is needed, it creates a web chat
conversation and sends a push notification. When the event is routine,
the agent responds silently (may still update workspace memory).
"""
import asyncio
import time
import uuid

import logfire

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import AGENT_NAME, WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.instructions import SILENT_MARKER
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.push.service import notify_pending_approval, notify_trigger_alert
from api.src.ai_demos.models import save_agent_conversation
from api.src.ai_demos.hitl_utils import extract_pending_approvals

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


# System identity for trigger-initiated conversations.
# Not a real Clerk user — conversations use shared team access (clerk_user_id=None queries).
SYSTEM_USER_ID = "system:sernia-ai"
SYSTEM_USER_NAME = "Sernia AI (Trigger)"
# Use Emilio's email for Google API delegation (service account requires impersonation)
SYSTEM_USER_EMAIL = "emilio@serniacapital.com"


async def run_agent_for_trigger(
    trigger_source: str,
    trigger_prompt: str,
    trigger_metadata: dict,
    trigger_context: str = "",
    notification_title: str = "",
    notification_body: str = "",
    rate_limit_key: str | None = None,
) -> str | None:
    """
    Run the Sernia agent in background for a trigger event.

    Args:
        trigger_source: Origin of the trigger ("sms", "email", "zillow_email").
        trigger_prompt: Synthetic user message describing the event.
        trigger_metadata: Stored in conversation metadata_ JSON for frontend display.
        trigger_context: Injected into agent instructions via deps.trigger_context.
        notification_title: Title for the push notification (if conversation created).
        notification_body: Body for the push notification (if conversation created).
        rate_limit_key: Cooldown key (e.g. phone number for SMS). When provided,
            the trigger is skipped if the same key fired within the last 2 minutes.
            Falls back to trigger_source if not provided.

    Returns:
        The conversation_id if a conversation was created (agent needs human attention),
        or None if the agent processed silently or was rate-limited.
    """
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

    conv_id = str(uuid.uuid4())

    async with AsyncSessionFactory() as session:
        deps = SerniaDeps(
            db_session=session,
            conversation_id=conv_id,
            user_identifier=SYSTEM_USER_ID,
            user_name=SYSTEM_USER_NAME,
            user_email=SYSTEM_USER_EMAIL,
            modality="web_chat",
            workspace_path=WORKSPACE_PATH,
            trigger_context=trigger_context or f"Trigger source: {trigger_source}",
        )

        try:
            result = await sernia_agent.run(trigger_prompt, deps=deps)
        except Exception:
            logfire.exception(
                "trigger agent run failed",
                trigger_source=trigger_source,
                conversation_id=conv_id,
            )
            return None

        # Commit any workspace changes (memory updates, notes)
        asyncio.create_task(commit_and_push(WORKSPACE_PATH))

        # Check if the agent decided no action is needed
        output_text = result.output if isinstance(result.output, str) else ""
        if SILENT_MARKER in output_text:
            logfire.info(
                "trigger processed silently — no action needed",
                trigger_source=trigger_source,
                conversation_id=conv_id,
                prompt_preview=trigger_prompt[:300],
                agent_output=output_text[:500],
            )
            return None

        # Agent wants human attention — persist the conversation
        await save_agent_conversation(
            session=session,
            conversation_id=conv_id,
            agent_name=AGENT_NAME,
            messages=result.all_messages(),
            clerk_user_id=SYSTEM_USER_ID,
            metadata=trigger_metadata,
        )

        # Send push notification
        pending = extract_pending_approvals(result)
        if pending:
            first = pending[0]
            asyncio.create_task(
                notify_pending_approval(
                    conversation_id=conv_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                )
            )
        else:
            asyncio.create_task(
                notify_trigger_alert(
                    conversation_id=conv_id,
                    trigger_source=trigger_source,
                    title=notification_title or f"Sernia AI: {trigger_source} alert",
                    body=notification_body or "New event needs your attention",
                )
            )

        logfire.info(
            "trigger conversation created",
            trigger_source=trigger_source,
            conversation_id=conv_id,
            has_pending=bool(pending),
        )
        return conv_id
