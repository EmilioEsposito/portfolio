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

import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
import logfire
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from api.src.ai_demos.hitl_utils import extract_pending_approvals
from api.src.ai_demos.models import (
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.service import find_contacts_by_phone, send_message
from api.src.sernia_ai.agent import NoAction, sernia_agent
from api.src.sernia_ai.config import (
    AGENT_NAME,
    GOOGLE_DELEGATION_EMAIL,
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    SMS_CONVERSATION_MAX_MESSAGES,
    SMS_HISTORY_MIN_DAYS,
    SMS_HISTORY_MIN_MESSAGES,
    TRIGGER_BOT_ID,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.model_config import resolve_active_run_kwargs
from api.src.sernia_ai.models import is_sernia_ai_enabled
from api.src.sernia_ai.push.service import notify_pending_approval
from api.src.sernia_ai.tools._logging import create_logged_task

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


# ---------------------------------------------------------------------------
# Group SMS support — a message.received on the AI line whose ``to`` includes
# recipients besides the AI's own number is a GROUP text. The AI replies into
# the same group thread (all human participants) instead of 1:1.
# ---------------------------------------------------------------------------


def _split_to_numbers(to_number) -> list[str]:
    """Normalize the webhook ``to`` field to a list of E.164 strings.

    OpenPhone delivers ``to`` as a single string — comma-joined when the
    message went to a group. Accepts a list too, defensively.
    """
    if not to_number:
        return []
    if isinstance(to_number, str):
        return [p.strip() for p in to_number.split(",") if p.strip()]
    return [str(p).strip() for p in to_number if str(p).strip()]


def _extract_group_participants(event_data: dict, ai_phone: str | None) -> list[str]:
    """Human group participants besides the sender: every ``to`` recipient
    that isn't the AI line itself.

    Returns [] for a 1:1 message (the only recipient is the AI line) — and
    also when ``ai_phone`` is unknown (None), because without it a 1:1
    message's own ``to`` entry can't be told apart from a real participant.
    Falling back to 1:1 handling is the safe failure mode.
    """
    if not ai_phone:
        return []
    from_number = event_data.get("from_number", "")
    return [
        p
        for p in _split_to_numbers(event_data.get("to_number"))
        if p and p != from_number and p != ai_phone
    ]


def _group_prompt(sender_name: str, text: str) -> str:
    """Prefix a group message with its sender so the agent can tell group
    members apart. Used for BOTH the live prompt and history reconstruction
    from the events table — the formats must match for text dedup to work."""
    return f"[{sender_name}]: {text}"


def _group_activities_to_model_messages(
    activities: list[dict],
    ai_phone: str | None,
    name_by_phone: dict[str, str],
    exclude_message_id: str | None = None,
) -> list[ModelMessage]:
    """Convert events-table group activities into PydanticAI messages.

    Outgoing messages from the AI line become assistant turns; everything
    else becomes a user turn prefixed with the sender's name via
    ``_group_prompt``. ``exclude_message_id`` drops the activity for the
    message currently being processed — it is passed as the run's prompt
    instead (the webhook saves the event to the DB before the background
    task runs, so the current message is already in the table).
    """
    result: list[ModelMessage] = []
    for act in activities:
        if act.get("_kind") != "message":
            continue
        if exclude_message_id and act.get("id") == exclude_message_id:
            continue
        text = (act.get("text") or "").strip()
        if not text:
            continue
        ts = _parse_timestamp(act.get("createdAt"))
        sender = act.get("from") or ""
        if ai_phone and sender == ai_phone:
            resp_kwargs: dict = {"timestamp": ts} if ts is not None else {}
            result.append(ModelResponse(parts=[TextPart(content=text)], **resp_kwargs))
        else:
            name = name_by_phone.get(sender, sender or "Unknown")
            part_kwargs: dict = {"timestamp": ts} if ts is not None else {}
            result.append(
                ModelRequest(
                    parts=[UserPromptPart(content=_group_prompt(name, text), **part_kwargs)]
                )
            )
    return result


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
            company = contact.get("defaultFields", {}).get("company", "") or ""
            if company.strip().lower() == QUO_INTERNAL_COMPANY.lower():
                return contact
        return None
    except Exception:
        logfire.exception("ai_sms_event: failed to verify contact", phone=phone)
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
                    # Plain ``participants``, NOT ``participants[]``: httpx
                    # percent-encodes the brackets to ``participants%5B%5D``,
                    # which OpenPhone does not recognise and rejects with a
                    # 400. (Literal brackets work in curl, which is why the
                    # bracketed form looks right in the API docs.) Every other
                    # call site in the codebase uses the plain key — see
                    # ``quo_tools._fetch_latest_message``.
                    "phoneNumberId": QUO_SERNIA_AI_PHONE_ID,
                    "participants": from_phone,
                    "maxResults": SMS_CONVERSATION_MAX_MESSAGES,
                },
            )
            resp.raise_for_status()
            messages = resp.json().get("data", [])
            return _sms_to_model_messages(messages)
    except Exception:
        logfire.exception("ai_sms_event: failed to fetch SMS thread", from_phone=from_phone)
        return []


def _parse_timestamp(raw: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp from the Quo API, returning None on failure."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _sms_to_model_messages(messages: list[dict]) -> list[ModelMessage]:
    """Convert Quo message dicts to PydanticAI ModelMessage list.

    Quo returns newest-first; we reverse for chronological order.
    Incoming (direction="incoming") → ModelRequest with UserPromptPart
    Outgoing (direction="outgoing") → ModelResponse with TextPart

    Preserves original ``createdAt`` timestamps so history trimming can
    accurately determine message age.  Timestamps are set on
    ``UserPromptPart.timestamp`` (for requests) and
    ``ModelResponse.timestamp`` (for responses) — the two places
    PydanticAI actually persists them through serialization.
    """
    result: list[ModelMessage] = []

    # Reverse to get chronological order
    for msg in reversed(messages):
        body = msg.get("text") or msg.get("body") or msg.get("content") or ""
        if not body.strip():
            continue

        ts = _parse_timestamp(msg.get("createdAt"))
        direction = msg.get("direction", "")

        if direction == "incoming":
            part_kwargs: dict = {}
            if ts is not None:
                part_kwargs["timestamp"] = ts
            result.append(ModelRequest(parts=[UserPromptPart(content=body, **part_kwargs)]))
        elif direction == "outgoing":
            resp_kwargs: dict = {}
            if ts is not None:
                resp_kwargs["timestamp"] = ts
            result.append(ModelResponse(parts=[TextPart(content=body)], **resp_kwargs))

    return result


# Prompts persisted to the DB can carry appended system hints (history-trim
# notes, the group-thread hint) that the same message reconstructed from the
# Quo API / events table doesn't have. Strip them before dedup comparison,
# otherwise every hinted turn is re-prepended as "missing" on the next run
# and the agent sees each prior message twice.
_SYSTEM_HINT_RE = re.compile(r"\s*\[System:.*$", re.DOTALL)


def _dedup_key(text: str) -> str:
    """Normalize a message text for dedup: strip appended system hints."""
    return _SYSTEM_HINT_RE.sub("", text).strip()


def _extract_text_contents(messages: list[ModelMessage]) -> set[str]:
    """Extract all (normalized) text content strings from a message list for
    dedup — appended ``[System: ...]`` hints are stripped via ``_dedup_key``."""
    texts: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    texts.add(_dedup_key(part.content))
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    texts.add(_dedup_key(part.content))
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

    # Find text contents already in DB history (normalized via _dedup_key)
    db_texts = _extract_text_contents(db_history)

    # Collect SMS messages not present in DB
    missing: list[ModelMessage] = []
    for msg in sms_thread:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    if _dedup_key(part.content) not in db_texts:
                        missing.append(msg)
                        break
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    if _dedup_key(part.content) not in db_texts:
                        missing.append(msg)
                        break

    if not missing:
        return db_history

    # Prepend missing SMS messages before DB history
    return missing + db_history


def _get_message_timestamp(msg: ModelMessage) -> datetime | None:
    """Extract the effective timestamp from a PydanticAI message.

    PydanticAI stores timestamps in different places depending on message type:
    - ``ModelRequest``: ``ModelRequest.timestamp`` is always ``None`` after
      serialization.  The real timestamp lives on ``UserPromptPart.timestamp``.
    - ``ModelResponse``: ``ModelResponse.timestamp`` is preserved correctly.
    """
    if isinstance(msg, ModelResponse):
        return msg.timestamp
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if isinstance(part, UserPromptPart):
                return getattr(part, "timestamp", None)
    return None


def _trim_sms_history(
    messages: list[ModelMessage],
    min_days: int = SMS_HISTORY_MIN_DAYS,
    min_messages: int = SMS_HISTORY_MIN_MESSAGES,
) -> tuple[list[ModelMessage], int]:
    """Trim conversation history to reduce token usage on SMS-triggered runs.

    Keeps the **larger** of two windows (whichever goes further back):
    - All messages from the last ``min_days`` days
    - The last ``min_messages`` user-message turns (with all associated
      agent responses and tool calls between them)

    Returns:
        (trimmed_messages, number_of_messages_removed)
    """
    if not messages or len(messages) <= min_messages:
        return messages, 0

    now = datetime.now(UTC)
    time_cutoff = now - timedelta(days=min_days)

    # --- Time-based cutoff ---
    # Scan BACKWARDS to find where recent messages start.  The merge step
    # may prepend recent SMS messages at the very beginning of the list
    # (out of chronological order), which would make a forward scan
    # immediately think "keep everything from index 0".  Scanning
    # backwards finds the true boundary in the DB-history portion.
    time_keep_from = len(messages)  # default: nothing qualifies on time alone
    ts_found = 0
    for i in range(len(messages) - 1, -1, -1):
        ts = _get_message_timestamp(messages[i])
        if ts is not None:
            ts_found += 1
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts < time_cutoff:
                # This message is outside the window; keep from the next one
                time_keep_from = i + 1
                break
    else:
        # Every message with a timestamp was within the window (or no timestamps)
        if ts_found > 0:
            time_keep_from = 0  # all messages are recent, keep all

    # --- Message-count cutoff ---
    # Identify indices of user-turn starts (ModelRequest with a UserPromptPart
    # that isn't purely tool-return plumbing).
    user_turn_indices: list[int] = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            has_user_prompt = any(isinstance(p, UserPromptPart) for p in msg.parts)
            is_pure_tool_return = all(isinstance(p, ToolReturnPart) for p in msg.parts)
            if has_user_prompt and not is_pure_tool_return:
                user_turn_indices.append(i)

    if len(user_turn_indices) <= min_messages:
        msg_keep_from = 0  # not enough turns to trim
    else:
        msg_keep_from = user_turn_indices[-min_messages]

    # Whichever window goes further back (smaller index = more history kept)
    keep_from = min(time_keep_from, msg_keep_from)

    logfire.info(
        "ai_sms_event: trim diagnostics",
        total_messages=len(messages),
        timestamps_found=ts_found,
        time_keep_from=time_keep_from,
        user_turns=len(user_turn_indices),
        msg_keep_from=msg_keep_from,
        keep_from=keep_from,
    )

    if keep_from <= 0:
        return messages, 0

    return messages[keep_from:], keep_from


def _sanitize_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Sanitize tool call/return pairs in message history.

    Handles two scenarios that break model APIs:
    1. Orphaned ToolReturnParts — tool returns without matching tool calls.
       This happens when history trimming cuts off older messages containing
       the ToolCallPart, or when the main agent model changes (e.g., Anthropic
       to OpenAI) and old tool_call_ids have incompatible formats.
    2. Trailing ToolCallParts — tool calls at the end without subsequent returns.
       This happens when a previous run crashed or history was trimmed.

    Returns a sanitized copy of the message list.
    """
    if not messages:
        return messages

    # Step 1: Collect all tool_call_ids from ToolCallParts
    valid_tool_call_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    valid_tool_call_ids.add(part.tool_call_id)

    # Step 2: Remove orphaned ToolReturnParts (returns without matching calls)
    result: list[ModelMessage] = []
    orphans_removed = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            # Filter out ToolReturnParts that reference non-existent tool calls
            new_parts = []
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    if part.tool_call_id not in valid_tool_call_ids:
                        orphans_removed += 1
                        continue
                new_parts.append(part)

            # Only include the request if it has remaining parts
            if new_parts:
                result.append(ModelRequest(parts=new_parts))
        else:
            result.append(msg)

    if orphans_removed > 0:
        logfire.info(
            "ai_sms_event: removed orphaned tool returns",
            orphans_removed=orphans_removed,
        )

    # Step 3: Remove trailing tool calls without returns
    while result:
        last = result[-1]
        if isinstance(last, ModelResponse):
            has_tool_call = any(isinstance(p, ToolCallPart) for p in last.parts)
            if has_tool_call:
                logfire.info(
                    "ai_sms_event: removing trailing tool call",
                    removed_message_type=type(last).__name__,
                )
                result.pop()
                continue
        break

    return result


async def _send_sms_reply(to_phone: str | list[str], message: str) -> None:
    """Send an SMS reply from the AI phone number, auto-splitting if long.

    ``to_phone`` may be a single number (1:1 reply) or a list of numbers —
    a list replies into the shared group thread. Never raises.
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
            logfire.exception("ai_sms_event: failed to send SMS reply", to_phone=to_phone)
            return
    logfire.info(
        "ai_sms_event: reply sent",
        to_phone=to_phone,
        parts=len(chunks),
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
        logfire.info("ai_sms_event: skipping event with missing data", event_id=event_id)
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
        logfire.info("ai_sms_event: rate-limited", from_number=from_number)
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

    # --- Group detection: to includes recipients besides the AI line ---
    from api.src.open_phone.service import get_ai_phone_number

    ai_phone = await get_ai_phone_number()
    other_participants = _extract_group_participants(event_data, ai_phone)
    if other_participants:
        await _handle_group_sms(
            event_data,
            sender_contact=contact,
            sender_name=contact_name,
            other_participants=other_participants,
            ai_phone=ai_phone,
        )
        return

    conv_id = f"ai_sms_from_{_digits_only(from_number)}"

    # Load conversation history — always fetch SMS thread from Quo and
    # merge with DB history so the agent has full context even when
    # messages were sent from other conversations (e.g. web chat tool calls).
    async with AsyncSessionFactory() as session:
        db_history = await get_conversation_messages(conv_id, clerk_user_id=None, session=session)
        sms_thread = await _fetch_sms_thread(from_number)

        merged = _merge_sms_into_history(db_history, sms_thread)
        trimmed, trimmed_count = _trim_sms_history(merged)
        history = _sanitize_tool_calls(trimmed)
        logfire.info(
            "ai_sms_event: history loaded",
            from_number=from_number,
            db_messages=len(db_history),
            sms_messages=len(sms_thread),
            merged_messages=len(merged),
            after_trim=len(trimmed),
            after_sanitize=len(history),
            trimmed=trimmed_count,
        )

        # Build context hints for the agent
        sms_context_hint = ""
        if len(history) <= 1:
            # Quo API may not have indexed recent messages yet
            sms_context_hint = (
                " [System: No prior conversation history found. This person may "
                "be replying to a message you sent from another conversation. "
                "Use `db_get_contact_sms_history` to check recent SMS thread "
                "before replying.]"
            )
        elif trimmed_count > 0:
            sms_context_hint = (
                f" [System: Conversation history trimmed to reduce context — "
                f"{trimmed_count} older message(s) omitted. You are seeing the "
                f"last {SMS_HISTORY_MIN_DAYS} days or last "
                f"{SMS_HISTORY_MIN_MESSAGES} exchanges (whichever is more). "
                f"If you need earlier context, use `db_get_contact_sms_history` "
                f"or `db_search_sms_history`.]"
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

        run_kwargs = await resolve_active_run_kwargs()

        try:
            result = await sernia_agent.run(
                message_text + sms_context_hint,
                message_history=history,
                deps=deps,
                metadata={"trigger_source": "ai_sms"},
                **run_kwargs,
            )
        except Exception:
            logfire.exception(
                "ai_sms_event: agent run failed",
                from_number=from_number,
                conversation_id=conv_id,
            )
            return

        # Commit any workspace changes
        create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

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
            # HITL pause — push the approval prompt; don't send SMS yet.
            first = pending[0]
            create_logged_task(
                notify_pending_approval(
                    conversation_id=conv_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                ),
                name="notify_pending_approval",
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
            output_text = result.output if isinstance(result.output, str) else ""
            if output_text:
                create_logged_task(
                    _send_sms_reply(from_number, output_text),
                    name="sms_reply",
                )

        logfire.info(
            "ai_sms_event: completed",
            conversation_id=conv_id,
            from_number=from_number,
            has_pending=bool(pending),
        )


async def _handle_group_sms(
    event_data: dict,
    *,
    sender_contact: dict,
    sender_name: str,
    other_participants: list[str],
    ai_phone: str,
) -> None:
    """Process an inbound GROUP SMS on the AI line.

    Differences from the 1:1 flow:

    - **Gate**: every human participant (sender + other recipients) must be
      an internal contact — one external/unknown member and the whole event
      is silently ignored, same as an external 1:1 sender.
    - **Conversation**: keyed to the Quo group conversation
      (``ai_sms_group_{CN...}``) so all members share one AI conversation,
      instead of the sender's personal ``ai_sms_from_{digits}``.
    - **History**: group thread reconstructed from the local
      ``open_phone_events`` webhook table (the only source of full group
      history — OpenPhone's API can't list group messages by participant),
      merged with DB history. User turns are prefixed ``[Sender Name]:``.
    - **Reply**: sent to ALL human participants in one message, landing in
      the same group thread.
    """
    from_number = event_data.get("from_number", "")
    message_text = event_data.get("message_text", "")
    quo_conv_id = event_data.get("conversation_id")

    # Gate: every other participant must also be internal.
    participant_contacts: dict[str, dict] = {from_number: sender_contact}
    for phone in other_participants:
        member = await _verify_internal_contact(phone)
        if not member:
            logfire.info(
                "ai_sms_event: ignoring group with external/unknown participant",
                participant=phone,
                from_number=from_number,
            )
            return
        participant_contacts[phone] = member

    name_by_phone = {p: _contact_display_name(c) for p, c in participant_contacts.items()}
    all_humans = sorted(participant_contacts)  # sender + others, deterministic order

    if quo_conv_id:
        conv_id = f"ai_sms_group_{quo_conv_id}"
    else:
        conv_id = "ai_sms_group_" + "_".join(_digits_only(p) for p in all_humans)

    # The current message's Quo ID — already saved to the events table by the
    # webhook route, so exclude it from reconstructed history (it becomes the
    # run's prompt instead).
    current_msg_id = (
        ((event_data.get("event_data") or {}).get("data") or {}).get("object") or {}
    ).get("id")

    logfire.info(
        "ai_sms_event: processing GROUP SMS",
        conversation_id=conv_id,
        from_number=from_number,
        participants=all_humans,
    )

    async with AsyncSessionFactory() as session:
        db_history = await get_conversation_messages(conv_id, clerk_user_id=None, session=session)

        group_thread: list[ModelMessage] = []
        if quo_conv_id:
            try:
                from api.src.sernia_ai.tools.quo_tools import (
                    _fetch_group_thread_from_events_table,
                )

                activities = await _fetch_group_thread_from_events_table(
                    quo_conv_id,
                    max_results=SMS_CONVERSATION_MAX_MESSAGES,
                )
                group_thread = _group_activities_to_model_messages(
                    activities,
                    ai_phone,
                    name_by_phone,
                    exclude_message_id=current_msg_id,
                )
            except Exception:
                logfire.exception(
                    "ai_sms_event: group thread reconstruction failed",
                    conversation_id=conv_id,
                )

        merged = _merge_sms_into_history(db_history, group_thread)
        trimmed, trimmed_count = _trim_sms_history(merged)
        history = _sanitize_tool_calls(trimmed)
        logfire.info(
            "ai_sms_event: group history loaded",
            conversation_id=conv_id,
            db_messages=len(db_history),
            group_thread_messages=len(group_thread),
            after_trim=len(trimmed),
            trimmed=trimmed_count,
        )

        participants_label = ", ".join(f"{name_by_phone[p]} ({p})" for p in all_humans)
        group_hint = (
            f" [System: This is a GROUP SMS thread with {participants_label}. "
            "Messages are prefixed with the sender's name. Your reply will be "
            "sent to the WHOLE group.]"
        )
        if trimmed_count > 0:
            group_hint += (
                f" [System: History trimmed — {trimmed_count} older message(s) omitted. "
                "Use `get_thread_messages` with the participant list for earlier context.]"
            )

        deps = SerniaDeps(
            db_session=session,
            conversation_id=conv_id,
            user_identifier=f"sms_group:{from_number}",
            user_name=sender_name,
            user_email=GOOGLE_DELEGATION_EMAIL,
            modality="sms",
            workspace_path=WORKSPACE_PATH,
        )

        run_kwargs = await resolve_active_run_kwargs()

        try:
            result = await sernia_agent.run(
                _group_prompt(sender_name, message_text) + group_hint,
                message_history=history,
                deps=deps,
                metadata={"trigger_source": "ai_sms_group"},
                **run_kwargs,
            )
        except Exception:
            logfire.exception(
                "ai_sms_event: group agent run failed",
                conversation_id=conv_id,
            )
            return

        create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

        await save_agent_conversation(
            session=session,
            conversation_id=conv_id,
            agent_name=AGENT_NAME,
            messages=result.all_messages(),
            clerk_user_id=TRIGGER_BOT_ID,
            metadata={
                "trigger_source": "ai_sms_group",
                "trigger_phone": from_number,
                # Group label (not the sender's name): the web UI sidebar shows
                # this as the conversation's participant, and a bare sender
                # name would make the group thread look like a 1:1.
                "trigger_contact_name": "Group: " + ", ".join(name_by_phone[p] for p in all_humans),
                "openphone_conversation_id": quo_conv_id,
                # Post-approval replies use this to respond into the group
                # thread instead of 1:1 (see routes.approve_conversation).
                "trigger_group_participants": all_humans,
            },
            modality="sms",
            contact_identifier=",".join(all_humans),
        )

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
            logfire.info(
                "ai_sms_event: group HITL pause — awaiting approval",
                conversation_id=conv_id,
                tool_name=first["tool_name"],
            )
        elif isinstance(result.output, NoAction):
            logfire.info(
                "ai_sms_event: group NoAction — no SMS reply",
                conversation_id=conv_id,
                reason=result.output.reason,
            )
        else:
            output_text = result.output if isinstance(result.output, str) else ""
            if output_text:
                create_logged_task(
                    _send_sms_reply(all_humans, output_text),
                    name="sms_reply",
                )

        logfire.info(
            "ai_sms_event: group completed",
            conversation_id=conv_id,
            from_number=from_number,
            participants=all_humans,
            has_pending=bool(pending),
        )
