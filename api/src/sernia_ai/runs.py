"""
The one pipeline every Sernia agent run goes through.

A run reaches the agent five ways — web chat (streaming), an approval resume,
a 1:1 SMS, a group SMS, and a background trigger (Zillow email, scheduled
check). They differ in how the run *starts* (gates, history, prompt, deps),
which stays with each caller. What happens *after* the model stops is the same
everywhere, and lives here:

    sync the workspace -> save the conversation -> deliver the outcome

Keeping that in one place is what stops the paths from drifting: before this
module each path had its own copy, and they had come to disagree on whether a
run's cost was recorded, whether a failed run left a trace, and whether a
reply waited on the save.

Delivery rules, applied identically on every path:

- Paused for approval -> push the approval prompt. No reply is sent.
- ``NoAction`` -> nothing is delivered.
- Text -> SMS reply when the run owns an SMS thread
  (``deps.sms_reply_recipients``), and a web push to the person who asked
  (``notify_user_id``).
- Pushes deep-link to the conversation, so they are only sent once the save
  is confirmed. An SMS reply does not depend on the save: a person waiting on
  a text should still get it if the database write failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import logfire
from pydantic_ai import capture_run_messages
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import ModelMessage

from api.src.ai_demos.hitl_utils import extract_pending_approvals
from api.src.ai_demos.models import persist_agent_run_result, save_agent_conversation
from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.service import send_message
from api.src.sernia_ai.agent import NoAction, sernia_agent
from api.src.sernia_ai.config import AGENT_NAME, QUO_SERNIA_AI_PHONE_ID, WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.model_config import resolve_active_run_kwargs
from api.src.sernia_ai.push.service import notify_chat_response, notify_pending_approval
from api.src.sernia_ai.tools._logging import create_logged_task


@dataclass(frozen=True)
class RunOutcome:
    """What a finished run produced, after it was saved and delivered."""

    persisted: bool
    # Tool calls waiting on a human; non-empty means the run is paused.
    pending: list[dict] = field(default_factory=list)
    # The agent's final text. None when paused or when it chose NoAction.
    reply_text: str | None = None
    no_action_reason: str | None = None

    @property
    def needs_attention(self) -> bool:
        """True when the run left something for a human to read or approve."""
        return bool(self.pending) or self.reply_text is not None


async def send_sms_reply(to_phone: str | list[str], message: str) -> None:
    """Send the agent's reply from the AI phone number, splitting it if long.

    ``to_phone`` may be a single number (1:1 reply) or a list of numbers — a
    list replies into the shared group thread. Never raises.
    """
    from api.src.sernia_ai.tools.quo_tools import split_sms

    chunks = split_sms(message)
    for chunk in chunks:
        try:
            await send_message(
                message=chunk,
                to_phone_number=to_phone,
                from_phone_number=QUO_SERNIA_AI_PHONE_ID,
            )
        except Exception:
            logfire.exception("sernia run: failed to send SMS reply", to_phone=to_phone)
            return
    logfire.info("sernia run: SMS reply sent", to_phone=to_phone, parts=len(chunks))


async def run_agent(
    prompt: str | None,
    *,
    deps: SerniaDeps,
    trigger_source: str,
    message_history: list[ModelMessage] | None = None,
) -> AgentRunResult:
    """Run the agent on the currently configured model.

    ``trigger_source`` is stamped on the run's telemetry (it is what the
    cost-by-source dashboard groups on).
    """
    run_kwargs = await resolve_active_run_kwargs()
    return await sernia_agent.run(
        prompt,
        message_history=message_history,
        deps=deps,
        metadata={"trigger_source": trigger_source},
        **run_kwargs,
    )


async def finalize_run(
    result: AgentRunResult,
    *,
    deps: SerniaDeps,
    clerk_user_id: str | None,
    metadata: dict[str, Any] | None = None,
    modality: str | None = None,
    contact_identifier: str | None = None,
    notify_user_id: str | None = None,
) -> RunOutcome:
    """Sync the workspace, save the conversation, and deliver the outcome.

    The agent's text is sent by SMS to ``deps.sms_reply_recipients`` when that
    is set. It is the same field that stops the ``send_sms`` tool from texting
    that thread itself, so "who the final answer goes to" has one definition.

    Args:
        deps: The deps the run used.
        clerk_user_id: Owner recorded on the conversation row.
        metadata: Merged onto the conversation's stored metadata.
        modality, contact_identifier: Set on the row when given.
        notify_user_id: Clerk user to web-push when the agent answers with
            text. Only set for runs a person started from the web app.
    """
    conversation_id = deps.conversation_id
    # Memory and notes the agent wrote during the run.
    create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

    persisted = await persist_agent_run_result(
        result,
        conversation_id=conversation_id,
        agent_name=AGENT_NAME,
        clerk_user_id=clerk_user_id,
        metadata=metadata,
        modality=modality,
        contact_identifier=contact_identifier,
    )
    if not persisted:
        logfire.warn(
            "sernia run: conversation not saved — push notifications skipped",
            conversation_id=conversation_id,
        )

    pending = extract_pending_approvals(result)
    if pending:
        if persisted:
            first = pending[0]
            create_logged_task(
                notify_pending_approval(
                    conversation_id=conversation_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                ),
                name="notify_pending_approval",
            )
        return RunOutcome(persisted=persisted, pending=pending)

    output = result.output
    if isinstance(output, NoAction):
        return RunOutcome(persisted=persisted, no_action_reason=output.reason)

    reply_text = output if isinstance(output, str) and output.strip() else None
    if reply_text is None:
        return RunOutcome(persisted=persisted)

    recipients = deps.sms_reply_recipients
    if recipients:
        # One number replies 1:1; several reply into the shared group thread.
        await send_sms_reply(recipients[0] if len(recipients) == 1 else recipients, reply_text)
    if notify_user_id and persisted:
        create_logged_task(
            notify_chat_response(
                conversation_id=conversation_id,
                clerk_user_id=notify_user_id,
                response_text=reply_text,
            ),
            name="notify_chat_response",
        )
    return RunOutcome(persisted=persisted, reply_text=reply_text)


async def save_failed_run(
    messages: list[ModelMessage] | None,
    *,
    conversation_id: str,
    clerk_user_id: str | None,
    metadata: dict[str, Any] | None = None,
    llm_error: bool = False,
) -> None:
    """Keep what a crashed run got through, flagged as partial. Never raises.

    ``messages`` comes from ``capture_run_messages``; nothing is written when
    the run failed before producing any.
    """
    if not messages:
        return
    flags: dict[str, Any] = {"partial": True, "error": True}
    if llm_error:
        flags["llm_error"] = True
    try:
        # A fresh session: the caller's may have sat idle through a long run.
        async with AsyncSessionFactory() as session:
            await save_agent_conversation(
                session=session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                clerk_user_id=clerk_user_id,
                metadata={**(metadata or {}), **flags},
            )
        logfire.info("sernia run: partial conversation saved", conversation_id=conversation_id)
    except Exception:
        logfire.exception(
            "sernia run: failed to save partial conversation", conversation_id=conversation_id
        )


async def run_and_finalize(
    prompt: str,
    *,
    deps: SerniaDeps,
    trigger_source: str,
    clerk_user_id: str | None,
    message_history: list[ModelMessage] | None = None,
    metadata: dict[str, Any] | None = None,
    modality: str | None = None,
    contact_identifier: str | None = None,
    keep_failed_run: bool = True,
) -> RunOutcome | None:
    """Run the agent, then ``finalize_run``. Returns None if the run failed.

    The non-streaming paths (SMS and background triggers) use this. A failed
    run is logged and never raises — these run as background tasks with no
    caller to report to.

    Args:
        keep_failed_run: Save the partial conversation when the run crashes.
            SMS conversations pass False: their history is rebuilt on every
            inbound text by merging the stored conversation with the live Quo
            thread, and a half-finished turn in the stored copy is one more
            thing for that merge to repair.
    """
    conversation_id = deps.conversation_id
    with capture_run_messages() as captured:
        try:
            result = await run_agent(
                prompt, deps=deps, trigger_source=trigger_source, message_history=message_history
            )
        except Exception:
            logfire.exception(
                "sernia run: agent run failed",
                trigger_source=trigger_source,
                conversation_id=conversation_id,
            )
            if keep_failed_run:
                await save_failed_run(
                    captured,
                    conversation_id=conversation_id,
                    clerk_user_id=clerk_user_id,
                    metadata=metadata,
                )
            return None

    return await finalize_run(
        result,
        deps=deps,
        clerk_user_id=clerk_user_id,
        metadata=metadata,
        modality=modality,
        contact_identifier=contact_identifier,
    )
