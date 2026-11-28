"""A lead conversation handed on to a person's own chat with another agent.

The lead agent has no tools, and its conversations cannot be continued (see
``agent.py``). When a card needs more than approve or reject, a person hands
the thread on: they say what they want in a new conversation with the general
agent, which works behind its own approval gates. This module is the lead
side of that:

- ``lead_context`` writes what the new conversation is told: the Gmail thread
  and where a reply goes, what the lead agent did, and the case it was given.
- ``close`` declines whatever was still waiting on the lead conversation and
  records where it went.

Nothing here starts a run or names another agent's tools. That wording belongs
to the caller (``sernia_ai/lead_hand_off.py``), so this package stays
independent of it. A hand-off happens only when a person sends a message. An
escalation alone starts nothing.

The lead conversation is not changed by the hand-off beyond that close. The
two agents share the Gmail thread and the listings, not each other's
conversations: the lead's next email is a new case for the lead agent, built
from the thread as it then stands.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_agent_conversation,
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory
from api.src.zillow_leads.handle import SEND_REPLY, apply_approval
from api.src.zillow_leads.identity import is_lead_conversation

# Where a hand-off went, in the lead conversation's metadata.
HANDED_TO = "hand_off"


@dataclass(frozen=True)
class LeadContext:
    """What a hand-off tells the next conversation about a lead conversation."""

    conversation_id: str
    thread_id: str | None
    text: str


async def lead_context(session: AsyncSession, conversation_id: str) -> LeadContext | None:
    """A lead conversation as text for another agent. None if it is not one."""
    conversation = await get_agent_conversation(session, conversation_id, retries=1)
    if (
        conversation is None
        or conversation.agent_name != AGENT_NAME
        or not is_lead_conversation(conversation.metadata_)
    ):
        return None
    messages = await get_conversation_messages(conversation_id, None, session=session)
    if not messages:
        return None
    metadata = conversation.metadata_ or {}
    return LeadContext(
        conversation_id=conversation_id,
        thread_id=metadata.get("lead_thread_id"),
        text=render(metadata, messages),
    )


def render(metadata: dict[str, Any], messages: list[ModelMessage]) -> str:
    """The thread, where a reply goes, what the lead agent did, and its case."""
    lines = [f"Gmail thread ID: {metadata.get('lead_thread_id') or '(not recorded)'}"]
    route = metadata.get("reply_route")
    if route:
        lines += [
            "A reply to this lead goes to the Zillow relay address its latest message came from:",
            f"- To: {route.get('to')}",
            f"- Subject: {route.get('subject')}",
            f"- Gmail message ID to reply to: {route.get('reply_to_message_id')}",
        ]
    else:
        lines.append("This thread has no Zillow relay address to reply to.")

    lines += ["", "What the lead agent did:"]
    steps = _steps(messages) + _edited_sends(metadata)
    for number, step in enumerate(steps or [["Nothing is recorded."]], start=1):
        lines.append(f"{number}. {step[0]}")
        lines += [f"   {line}" for line in step[1:]]

    lines += [
        "",
        "The case the lead agent was given, between the <case> tags:",
        "<case>",
        _case_text(messages),
        "</case>",
    ]
    return "\n".join(lines)


def _case_text(messages: list[ModelMessage]) -> str:
    first = messages[0]
    if isinstance(first, ModelRequest):
        for part in first.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content.strip()
    return "(not recorded)"


def _steps(messages: list[ModelMessage]) -> list[list[str]]:
    """Each card and note in the conversation, in order, with how it ended."""
    results: dict[str, str] = {}
    already_said: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        returned = [part for part in message.parts if isinstance(part, ToolReturnPart)]
        for part in returned:
            results[part.tool_call_id] = str(part.content)
        # A decision is followed by its results again, as one line of text.
        already_said.add(" ".join(str(part.content) for part in returned))
    already_said |= set(results.values())

    steps: list[list[str]] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                steps.append(_card(part, results.get(part.tool_call_id)))
            elif isinstance(part, TextPart) and part.content.strip() not in already_said:
                steps.append([f"Noted: {part.content.strip()}"])
    return steps


def _card(call: ToolCallPart, result: str | None) -> list[str]:
    args = call.args_as_dict() or {}
    ended = f"result: {result}" if result is not None else "still waiting for a person"
    if call.tool_name == SEND_REPLY:
        lines = [f"Drafted a reply to the lead ({ended}):"]
        lines += _quoted(str(args.get("body") or ""))
    else:
        lines = [f"Asked the team ({ended}):"]
        if args.get("question"):
            lines.append(f"Question: {args['question']}")
    if args.get("why"):
        lines.append(f"Why: {args['why']}")
    if notes := args.get("team_notes"):
        lines.append("Notes for the team:")
        lines += [f"- {note}" for note in notes]
    return lines


def _edited_sends(metadata: dict[str, Any]) -> list[list[str]]:
    """Replies a person reworded before sending: what the lead was told is this."""
    return [
        [f"{record.get('by') or 'A person'} edited the draft; these words were sent:"]
        + _quoted(str(record["sent_body"]))
        for record in metadata.get("approval") or []
        if record.get("edited") and record.get("sent_body")
    ]


def _quoted(text: str) -> list[str]:
    return [f"> {line}" for line in text.splitlines()]


async def close(conversation_id: str, *, reason: str, by: str, to_conversation_id: str) -> None:
    """Decline what still waits on a lead conversation, and record where it went.

    A waiting draft is not sent, and a place booked for it is cancelled, as for
    any rejected reply (``handle.apply_approval``): the person asked for
    something else. Logged, never raised: the new conversation has started
    already, and a card left waiting is only a card to mark handled.
    """
    try:
        async with AsyncSessionFactory() as session:
            conversation = await get_agent_conversation(session, conversation_id, retries=1)
            if conversation is None or not is_lead_conversation(conversation.metadata_):
                return
            messages = await get_conversation_messages(conversation_id, None, session=session)
            pending = extract_pending_approval_from_messages(messages)
            if pending:
                try:
                    await apply_approval(
                        session,
                        conversation_id=conversation_id,
                        metadata=conversation.metadata_ or {},
                        decisions=[
                            ApprovalDecision(
                                tool_call_id=card["tool_call_id"],
                                approved=False,
                                denial_reason=reason,
                            )
                            for card in pending
                        ],
                        approver=by,
                    )
                except ValueError:
                    # Someone decided it between the read and the lock.
                    await session.rollback()
            await session.get(
                AgentConversation, conversation_id, with_for_update=True, populate_existing=True
            )
            messages = await get_conversation_messages(conversation_id, None, session=session)
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={
                    HANDED_TO: {
                        "to": to_conversation_id,
                        "by": by,
                        "at": datetime.now(UTC).isoformat(),
                    }
                },
            )
    except Exception:
        logfire.exception(
            "zillow lead: hand-off not recorded",
            conversation_id=conversation_id,
            to_conversation_id=to_conversation_id,
        )
