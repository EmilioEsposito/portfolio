"""
AI SMS event trigger — direct conversation via SMS.

Handles messages sent directly to the AI's phone number. Unlike the team SMS
event trigger (team_sms_event_trigger.py) which monitors and analyzes, this
trigger treats the SMS thread AS the conversation — the AI responds natively
via SMS.

Only internal contacts (Sernia Capital LLC) are allowed; unknown numbers are
silently ignored.

Flow:
  1. Verify sender is an internal contact (Quo API lookup)
  2. Derive deterministic conversation_id from sender's phone number
  3. Load conversation history (DB if exists, else bootstrap from Quo)
  4. Run sernia_agent with modality="sms"
  5. Send agent's text response back via SMS, or handle HITL pause
"""

import asyncio
import os
import re
import time
from collections import defaultdict

import httpx
import logfire
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.models import is_sernia_ai_enabled
from api.src.sernia_ai.agent import NoAction, sernia_agent
from api.src.sernia_ai.config import (
    AGENT_NAME,
    GOOGLE_DELEGATION_EMAIL,
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    SMS_CONVERSATION_MAX_MESSAGES,
    TRIGGER_BOT_ID,
    TRIGGER_BOT_NAME,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.push.service import (
    notify_pending_approval,
    notify_team_sms,
)
from api.src.ai_demos.hitl_utils import extract_pending_approvals
from api.src.ai_demos.models import (
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.open_phone.service import send_message, find_contacts_by_phone


# ---------------------------------------------------------------------------
# Sliding-window rate limiter — allows up to AI_SMS_RATE_LIMIT_MAX_CALLS
# within AI_SMS_RATE_LIMIT_WINDOW_SECONDS per phone number.
# ---------------------------------------------------------------------------
AI_SMS_RATE_LIMIT_MAX_CALLS = 10
AI_SMS_RATE_LIMIT_WINDOW_SECONDS = 600  # 10 minutes

# {phone_number: [timestamp1, timestamp2, ...]}
_ai_sms_call_timestamps: dict[str, list[float]] = defaultdict(list)


def _is_ai_sms_rate_limited(phone: str) -> bool:
    """Return True if *phone* has exceeded the sliding-window rate limit."""
    now = time.monotonic()
    window_start = now - AI_SMS_RATE_LIMIT_WINDOW_SECONDS

    # Prune old timestamps outside the window
    timestamps = _ai_sms_call_timestamps[phone]
    _ai_sms_call_timestamps[phone] = [t for t in timestamps if t > window_start]

    if len(_ai_sms_call_timestamps[phone]) >= AI_SMS_RATE_LIMIT_MAX_CALLS:
        return True

    _ai_sms_call_timestamps[phone].append(now)
    return False


def _digits_only(phone: str) -> str:
    """Strip everything except digits from a phone number."""
    return re.sub(r"\D", "", phone)


async def _verify_internal_contact(phone: str) -> dict | None:
    """Check if phone belongs to a Sernia Capital LLC contact.

    Multiple contacts can share the same phone number.  Returns the first
    internal match, or None if no match is internal.
    Uses the centralized paginated contact lookup from open_phone.service.
    """
    try:
        contacts = await find_contacts_by_phone(phone)
        if not contacts:
            return None

        for contact in contacts:
            company = (
                contact.get("defaultFields", {}).get("company", "") or ""
            )
            if company.strip().lower() == QUO_INTERNAL_COMPANY.lower():
                return contact
        return None
    except Exception:
        logfire.exception(
            "ai_sms_event: failed to verify contact", phone=phone
        )
        return None


def _contact_display_name(contact: dict) -> str:
    """Extract a display name from a Quo contact dict."""
    fields = contact.get("defaultFields", {})
    first = fields.get("firstName", "") or ""
    last = fields.get("lastName", "") or ""
    name = f"{first} {last}".strip()
    return name or "Unknown"


async def _fetch_sms_thread(
    from_phone: str,
) -> list[ModelMessage]:
    """Fetch recent SMS messages from Quo and convert to PydanticAI format.

    Calls GET /v1/messages with the AI phone number ID and the sender's number.
    Returns messages in chronological order (oldest first).
    """
    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(
            base_url="https://api.openphone.com",
            headers={"Authorization": api_key},
            timeout=15,
        ) as client:
            resp = await client.get(
                "/v1/messages",
                params={
                    "phoneNumberId": QUO_SERNIA_AI_PHONE_ID,
                    "participants[]": from_phone,
                    "maxResults": SMS_CONVERSATION_MAX_MESSAGES,
                },
            )
            resp.raise_for_status()
            messages = resp.json().get("data", [])
            return _sms_to_model_messages(messages)
    except Exception:
        logfire.exception(
            "ai_sms_event: failed to fetch SMS thread", from_phone=from_phone
        )
        return []


def _sms_to_model_messages(messages: list[dict]) -> list[ModelMessage]:
    """Convert Quo message dicts to PydanticAI ModelMessage list.

    Quo returns newest-first; we reverse for chronological order.
    Incoming (direction="incoming") → ModelRequest with UserPromptPart
    Outgoing (direction="outgoing") → ModelResponse with TextPart
    """
    result: list[ModelMessage] = []

    # Reverse to get chronological order
    for msg in reversed(messages):
        body = msg.get("text") or msg.get("body") or msg.get("content") or ""
        if not body.strip():
            continue

        direction = msg.get("direction", "")
        if direction == "incoming":
            result.append(
                ModelRequest(parts=[UserPromptPart(content=body)])
            )
        elif direction == "outgoing":
            result.append(
                ModelResponse(parts=[TextPart(content=body)])
            )

    return result


def _extract_text_contents(messages: list[ModelMessage]) -> set[str]:
    """Extract all text content strings from a message list for dedup."""
    texts: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    texts.add(part.content.strip())
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    texts.add(part.content.strip())
    return texts


def _merge_sms_into_history(
    db_history: list[ModelMessage],
    sms_thread: list[ModelMessage],
) -> list[ModelMessage]:
    """Merge SMS thread messages into DB conversation history.

    DB history preserves tool call context from prior agent runs.
    SMS thread captures messages sent from any conversation (including
    web chat tool calls). Prepends any SMS messages whose text content
    is missing from the DB history.
    """
    if not sms_thread:
        return db_history
    if not db_history:
        return sms_thread

    # Find text contents already in DB history
    db_texts = _extract_text_contents(db_history)

    # Collect SMS messages not present in DB
    missing: list[ModelMessage] = []
    for msg in sms_thread:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    if part.content.strip() not in db_texts:
                        missing.append(msg)
                        break
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    if part.content.strip() not in db_texts:
                        missing.append(msg)
                        break

    if not missing:
        return db_history

    # Prepend missing SMS messages before DB history
    return missing + db_history


async def _send_sms_reply(to_phone: str, message: str) -> None:
    """Send an SMS reply from the AI phone number. Never raises."""
    try:
        await send_message(
            message=message,
            to_phone_number=to_phone,
            from_phone_number=QUO_SERNIA_AI_PHONE_ID,
        )
        logfire.info("ai_sms_event: reply sent", to_phone=to_phone)
    except Exception:
        logfire.exception(
            "ai_sms_event: failed to send SMS reply", to_phone=to_phone
        )


async def handle_ai_sms_event(event_data: dict) -> None:
    """Process an inbound SMS to the AI's phone number.

    Called as a FastAPI background task from the Quo webhook handler.
    This is a direct conversation — the AI responds natively via SMS.

    Args:
        event_data: Extracted event data from OpenPhoneWebhookPayload.
                    Expected keys: from_number, message_text, event_id.
    """
    from_number = event_data.get("from_number", "")
    message_text = event_data.get("message_text", "")
    event_id = event_data.get("event_id", "")

    if not from_number or not message_text:
        logfire.info(
            "ai_sms_event: skipping event with missing data", event_id=event_id
        )
        return

    # --- Universal kill switch ---
    if not await is_sernia_ai_enabled():
        logfire.info("sernia_ai disabled — skipping ai_sms_event", event_id=event_id)
        return

    logfire.info(
        "ai_sms_event: processing inbound SMS",
        event_id=event_id,
        from_number=from_number,
        message_length=len(message_text),
    )

    # Rate limit: sliding window (10 calls per 10 minutes per phone)
    if _is_ai_sms_rate_limited(from_number):
        logfire.info(
            "ai_sms_event: rate-limited", from_number=from_number
        )
        return

    # Gate: verify sender is an internal contact
    contact = await _verify_internal_contact(from_number)
    if not contact:
        logfire.info(
            "ai_sms_event: ignoring external/unknown sender",
            from_number=from_number,
        )
        return

    contact_name = _contact_display_name(contact)
    conv_id = f"ai_sms_from_{_digits_only(from_number)}"

    # Load conversation history — always fetch SMS thread from Quo and
    # merge with DB history so the agent has full context even when
    # messages were sent from other conversations (e.g. web chat tool calls).
    async with AsyncSessionFactory() as session:
        db_history = await get_conversation_messages(
            conv_id, clerk_user_id=None, session=session
        )
        sms_thread = await _fetch_sms_thread(from_number)

        history = _merge_sms_into_history(db_history, sms_thread)
        logfire.info(
            "ai_sms_event: history loaded",
            from_number=from_number,
            db_messages=len(db_history),
            sms_messages=len(sms_thread),
            merged_messages=len(history),
        )

        # If still no history, the Quo API may not have indexed recent
        # messages yet. Hint the agent to look up SMS history.
        sms_context_hint = ""
        if len(history) <= 1:
            sms_context_hint = (
                " [System: No prior conversation history found. This person may "
                "be replying to a message you sent from another conversation. "
                "Use `get_contact_sms_history` to check recent SMS thread before "
                "replying.]"
            )

        deps = SerniaDeps(
            db_session=session,
            conversation_id=conv_id,
            user_identifier=f"sms:{from_number}",
            user_name=contact_name,
            user_email=GOOGLE_DELEGATION_EMAIL,
            modality="sms",
            workspace_path=WORKSPACE_PATH,
        )

        try:
            result = await sernia_agent.run(
                message_text + sms_context_hint, message_history=history, deps=deps
            )
        except Exception:
            logfire.exception(
                "ai_sms_event: agent run failed",
                from_number=from_number,
                conversation_id=conv_id,
            )
            return

        # Commit any workspace changes
        asyncio.create_task(commit_and_push(WORKSPACE_PATH))

        trigger_metadata = {
            "trigger_source": "ai_sms",
            "trigger_phone": from_number,
            "trigger_contact_name": contact_name,
            "openphone_conversation_id": event_data.get("conversation_id"),
        }

        # Save conversation to DB
        await save_agent_conversation(
            session=session,
            conversation_id=conv_id,
            agent_name=AGENT_NAME,
            messages=result.all_messages(),
            clerk_user_id=TRIGGER_BOT_ID,
            metadata=trigger_metadata,
            modality="sms",
            contact_identifier=from_number,
        )

        # Handle result
        pending = extract_pending_approvals(result)
        if pending:
            # HITL pause — notify team, don't send SMS yet
            first = pending[0]
            asyncio.create_task(
                notify_pending_approval(
                    conversation_id=conv_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                )
            )
            asyncio.create_task(
                notify_team_sms(
                    title=f"SMS Approval Needed: {first['tool_name'].replace('_', ' ').title()}",
                    body=f"From {contact_name} ({from_number})",
                    conversation_id=conv_id,
                )
            )
            logfire.info(
                "ai_sms_event: HITL pause — awaiting approval",
                conversation_id=conv_id,
                tool_name=first["tool_name"],
            )
        elif isinstance(result.output, NoAction):
            logfire.info(
                "ai_sms_event: NoAction — no SMS reply",
                conversation_id=conv_id,
                reason=result.output.reason,
            )
        else:
            # Send agent's text response back via SMS
            output_text = (
                result.output if isinstance(result.output, str) else ""
            )
            if output_text:
                asyncio.create_task(
                    _send_sms_reply(from_number, output_text)
                )

        logfire.info(
            "ai_sms_event: completed",
            conversation_id=conv_id,
            from_number=from_number,
            has_pending=bool(pending),
        )
