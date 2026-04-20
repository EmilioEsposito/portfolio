"""
Background agent runner for Sernia AI triggers.

Runs the Sernia agent outside of an HTTP request context (no Clerk user,
no streaming). Used by SMS and email triggers to process events autonomously.

When the agent decides human attention is needed, it creates a web chat
conversation and sends a push notification. When the event is routine,
the agent responds silently (may still update workspace memory).
"""
import time
import uuid

import logfire
from pydantic_ai import capture_run_messages

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.models import is_sernia_ai_enabled
from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import (
    AGENT_NAME,
    GOOGLE_DELEGATION_EMAIL,
    TRIGGER_BOT_ID,
    TRIGGER_BOT_NAME,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.agent import NoAction
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.push.service import notify_pending_approval, notify_trigger_alert, notify_user_push
from api.src.sernia_ai.tools._logging import create_logged_task
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




async def run_agent_for_trigger(
    trigger_source: str,
    trigger_prompt: str,
    trigger_metadata: dict,
    notification_title: str = "",
    notification_body: str = "",
    rate_limit_key: str | None = None,
    notify_clerk_user_id: str | None = None,
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
        notification_title: Title for the push notification (if conversation created).
        notification_body: Body for the push notification (if conversation created).
        rate_limit_key: Cooldown key (e.g. phone number for SMS). When provided,
            the trigger is skipped if the same key fired within the last 2 minutes.
            Falls back to trigger_source if not provided.
        notify_clerk_user_id: When set, send push notifications only to this user
            instead of all Sernia users. Used for targeted triggers like Zillow drafts.
        conversation_id: Pre-generated conversation ID. When provided, the runner
            uses this instead of generating a new UUID. Useful when the caller
            needs to embed a deeplink in the trigger prompt.

    Returns:
        The conversation_id if a conversation was created (agent needs human attention),
        or None if the agent processed silently or was rate-limited.
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

    async with AsyncSessionFactory() as session:
        deps = SerniaDeps(
            db_session=session,
            conversation_id=conv_id,
            user_identifier=TRIGGER_BOT_ID,
            user_name=TRIGGER_BOT_NAME,
            user_email=GOOGLE_DELEGATION_EMAIL,
            modality="web_chat",
            workspace_path=WORKSPACE_PATH,
        )

        with capture_run_messages() as captured_messages:
            try:
                result = await sernia_agent.run(
                    trigger_prompt, deps=deps, metadata={"trigger_source": trigger_source}
                )
            except Exception:
                logfire.exception(
                    "trigger agent run failed",
                    trigger_source=trigger_source,
                    conversation_id=conv_id,
                    _tags=[_trigger_tag],
                )
                if captured_messages:
                    try:
                        await save_agent_conversation(
                            session=session,
                            conversation_id=conv_id,
                            agent_name=AGENT_NAME,
                            messages=captured_messages,
                            clerk_user_id=TRIGGER_BOT_ID,
                            metadata={**trigger_metadata, "partial": True, "error": True},
                        )
                        logfire.info("partial trigger conversation saved", conversation_id=conv_id)
                    except Exception:
                        logfire.exception("failed to save partial trigger conversation")
                return None

        # Commit any workspace changes (memory updates, notes)
        create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

        # persist the conversation
        try:
            await save_agent_conversation(
                session=session,
                conversation_id=conv_id,
                agent_name=AGENT_NAME,
                messages=result.all_messages(),
                clerk_user_id=TRIGGER_BOT_ID,
                metadata=trigger_metadata,
            )
        except Exception:
            logfire.exception(
                "trigger conversation save failed",
                trigger_source=trigger_source,
                conversation_id=conv_id,
            )
            return None

        # Check if the agent decided no action is needed
        if isinstance(result.output, NoAction):
            logfire.info(
                "trigger processed silently — no action needed",
                trigger_source=trigger_source,
                conversation_id=conv_id,
                reason=result.output.reason,
                prompt_preview=trigger_prompt[:300],
                _tags=[_trigger_tag],
            )
            return None

        # Send push notification (SMS notification removed — will move to agent level)
        pending = extract_pending_approvals(result)
        if pending:
            first = pending[0]
            create_logged_task(
                notify_pending_approval(
                    conversation_id=conv_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                ),
                name="notify_pending_approval",
            )
        elif notify_clerk_user_id:
            # Targeted push — only notify a specific user (e.g. Emilio for Zillow drafts)
            alert_title = notification_title or f"Sernia AI: {trigger_source} alert"
            alert_body = notification_body or "New event needs your attention"
            create_logged_task(
                notify_user_push(
                    clerk_user_id=notify_clerk_user_id,
                    title=alert_title,
                    body=alert_body,
                    data={
                        "url": f"/sernia-chat?id={conv_id}",
                        "conversation_id": conv_id,
                        "type": "alert",
                        "trigger_source": trigger_source,
                    },
                ),
                name="notify_user_push",
            )
        else:
            alert_title = notification_title or f"Sernia AI: {trigger_source} alert"
            alert_body = notification_body or "New event needs your attention"
            create_logged_task(
                notify_trigger_alert(
                    conversation_id=conv_id,
                    trigger_source=trigger_source,
                    title=alert_title,
                    body=alert_body,
                ),
                name="notify_trigger_alert",
            )

        logfire.info(
            "trigger conversation created",
            trigger_source=trigger_source,
            conversation_id=conv_id,
            has_pending=bool(pending),
            notify_target=notify_clerk_user_id or "all_sernia_users",
            _tags=[_trigger_tag],
        )
        return conv_id
