"""Approval prompts and single-use decisions for the general agent's SMS threads."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import logfire
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
)
from api.src.agent_conversations.run_state import require_idle
from api.src.contact.service import get_contact_by_slug
from api.src.sernia_ai.config import (
    EMILIO_CONTACT_SLUG,
    QUO_SERNIA_AI_PHONE_ID,
    SMS_SPLIT_THRESHOLD,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools.quo_tools import _build_quo_client, _fetch_group_messages
from api.src.utils import team_links

STATE_KEY = "sms_approval"
RESUME_TIMEOUT_SECONDS = 15 * 60


def new_sms_approval(pending: list[dict]) -> dict:
    return {"call_ids": [p["tool_call_id"] for p in pending], "ref": uuid4().hex}


def review_link(conversation_id: str) -> str:
    return team_links.team_url(team_links.chat_path(conversation_id))


async def approval_prompt(
    conversation_id: str, recipients: list[str], pending: list[dict], state: dict
) -> tuple[str, bool]:
    link = review_link(conversation_id)
    summary = pending[0]["tool_name"] if len(pending) == 1 else f"{len(pending)} actions"
    fallback = f"Approval needed for {summary}. Review and decide here: {link}"
    if len(recipients) != 1 or len(pending) != 1 or not state.get("ref") or state.get("web_review"):
        return fallback, False
    call = pending[0]
    args = call.get("args") or {}
    if call["tool_name"] == "clickup_delete_task" and set(args) == {"task_id"}:
        action = f"Delete ClickUp task {args['task_id']}?"
    else:
        action = f"Approve {call['tool_name']}?\n{json.dumps(args, ensure_ascii=True)}"
    # The unique link identifies this exact offer even if two actions look identical.
    prompt = f"{action}\nReply with just Y or Yes to approve. Review or deny: {link}#approval-{state['ref']}"
    if len(prompt) > SMS_SPLIT_THRESHOLD:
        return fallback, False
    try:
        approver = await get_contact_by_slug(EMILIO_CONTACT_SLUG)
    except Exception:
        logfire.exception("SMS approver lookup failed", conversation_id=conversation_id)
        return fallback, False
    if not approver or recipients != [approver.phone_number]:
        return fallback, False
    return prompt, True


async def directly_answers_prompt(
    event: dict, phone: str, ai_phone: str | None, prompt: str
) -> bool:
    """Require the last message before this signed inbound SMS to be our actual offer."""
    obj = (event.get("event_data") or {}).get("data", {}).get("object", {})
    thread = event.get("conversation_id")
    if not (
        ai_phone
        and event.get("to_number") == ai_phone
        and thread
        and obj.get("id")
        and obj.get("createdAt")
    ):
        return False
    if obj.get("media"):
        return False
    async with _build_quo_client() as client:
        # Quo can deliver the webhook before the messages endpoint indexes it.
        # Require both sides of the exchange so a lagging read cannot hide a
        # newer question between the offer and this "yes".
        for delay in (0, 0.5, 1.5):
            if delay:
                await asyncio.sleep(delay)
            messages = await _fetch_group_messages(
                client, {"id": thread, "phoneNumberId": QUO_SERNIA_AI_PHONE_ID}, [phone], 2
            )
            if messages and messages[-1]["id"] == obj["id"]:
                break
        else:
            return False
    if len(messages) < 2:
        return False
    previous = messages[-2]
    return (
        previous.get("direction") == "outgoing"
        and previous.get("from") == ai_phone
        and previous.get("text") == prompt
        and datetime.fromisoformat(previous["createdAt"]) < datetime.fromisoformat(obj["createdAt"])
    )


async def claim_sms_conversation_decision(
    session: AsyncSession,
    conversation_id: str,
    ids: list[str],
    *,
    expected_ref: str | None = None,
    deny_all: bool = False,
) -> None:
    """Web and SMS compete for the same stored call set before either can execute it."""
    conv = await session.get(
        AgentConversation, conversation_id, with_for_update=True, populate_existing=True
    )
    meta = (conv.metadata_ or {}) if conv else {}
    require_idle(meta)
    state = meta.get(STATE_KEY) or {}
    messages = await get_conversation_messages(conversation_id, session=session, retries=1)
    current = [p["tool_call_id"] for p in extract_pending_approval_from_messages(messages)]
    already_claimed = state.get("claimed") and sorted(state["call_ids"]) == sorted(ids)
    # A worker may die after an external effect. Web review can close that
    # round by denying it once the bounded resume has ended, never by replay.
    closing_interrupted = deny_all and claim_expired(state)
    if (
        not ids
        or sorted(ids) != sorted(current)
        or (already_claimed and not closing_interrupted)
        or (expected_ref and (state.get("ref") != expected_ref or state.get("call_ids") != ids))
    ):
        await session.rollback()
        raise HTTPException(409, "This approval has changed or has already been submitted.")
    expires_at = datetime.now(UTC) + timedelta(seconds=RESUME_TIMEOUT_SECONDS + 60)
    conv.metadata_ = {
        **meta,
        STATE_KEY: {
            **state,
            "call_ids": ids,
            "claimed": True,
            "expires_at": expires_at.isoformat(),
        },
    }
    await session.commit()


def claim_expired(state: dict) -> bool:
    return bool(
        state.get("expires_at") and datetime.fromisoformat(state["expires_at"]) < datetime.now(UTC)
    )


async def handle_pending_reply(
    deps: SerniaDeps, event: dict, pending: list[dict], ai_phone: str | None
) -> None:
    """Handle the reply before the SMS history sanitizer removes unresolved calls."""
    from api.src.agent_conversations.hitl import ApprovalDecision
    from api.src.agent_conversations.identity import TRIGGER_BOT_ID
    from api.src.sernia_ai.conversation_handler import resume_general_decision
    from api.src.sernia_ai.runs import send_sms_reply

    recipients = deps.sms_reply_recipients
    destination = recipients[0] if len(recipients) == 1 else recipients
    conv = await deps.db_session.get(AgentConversation, deps.conversation_id)
    meta = (conv.metadata_ or {}) if conv else {}
    state = meta.get(STATE_KEY) or {}
    ids = [p["tool_call_id"] for p in pending]
    if state.get("call_ids") != ids:
        state = {}  # Older conversations remain reviewable in web chat.
    link = review_link(deps.conversation_id)
    if state.get("claimed"):
        message = (
            "Execution was interrupted. Check the action’s result, then deny the old request in web chat."
            if claim_expired(state)
            else "A decision was already submitted. Check the result."
        )
        await send_sms_reply(destination, f"{message} {link}")
        return

    prompt, eligible = await approval_prompt(deps.conversation_id, recipients, pending, state)
    exact_yes = event["message_text"].lower() in {"y", "yes"}
    if (
        eligible
        and exact_yes
        and meta.get("openphone_conversation_id") == event.get("conversation_id")
    ):
        try:
            if await directly_answers_prompt(event, recipients[0], ai_phone, prompt):
                await claim_sms_conversation_decision(
                    deps.db_session, deps.conversation_id, ids, expected_ref=state["ref"]
                )
                await resume_general_decision(
                    deps,
                    [ApprovalDecision(ids[0], True)],
                    clerk_user_id=TRIGGER_BOT_ID,
                    trigger_source="ai_sms_approval",
                )
                return
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            await send_sms_reply(destination, f"The approval has changed. Check its status: {link}")
            return
        except Exception:
            logfire.exception(
                "Could not complete SMS approval", conversation_id=deps.conversation_id
            )
            await send_sms_reply(
                destination, f"Check the action's status before trying again: {link}"
            )
            return
    await send_sms_reply(destination, prompt)
