"""Everything around the lead agent that touches the outside world.

The agent reads a case and returns an outcome (``agent.py``). This module is
the code on either side of it:

    load the thread -> build the case -> ask the agent -> put the outcome
    into the conversation -> send automatically or wait for approval

The split matters for safety. The agent reads text written by strangers, so it
is given nothing private and no way to act. Every decision with consequences
is made here, in code, from facts the lead cannot influence:

- **Who a reply goes to** is the reply address of the lead's latest message,
  and only if that address is a Zillow relay. It is fixed before the agent
  runs and stored with the conversation. An approver can edit the words of a
  reply, never its recipient.
- **Whether a reply needs approval** comes from this agent's own settings.
  Approval defaults on. Explicitly opting out enables `auto_reply.py`, with
  fixed recipient/thread checks and a durable send reservation. Escalations
  and uncertain deliveries still go to the team.

A conversation saved by this module is marked with ``HANDLER``. Approvals for
it are applied here (``apply_approval``) and never resume a model: the reply
that was approved is the reply that is sent.
"""

import json
import uuid
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
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.sernia_ai.config import (
    AGENT_NAME,
    GOOGLE_DELEGATION_EMAIL,
    INTERNAL_EMAIL_DOMAIN,
    SHARED_EXTERNAL_EMAIL,
    TRIGGER_BOT_ID,
)
from api.src.sernia_ai.push.service import notify_pending_approval
from api.src.sernia_ai.tools._logging import create_logged_task
from api.src.sernia_ai.tools.google_tools import (
    GMAIL_SCOPES,
    _get_threading_headers,
    _send_email,
)
from api.src.sernia_ai.zillow_leads import listing_data
from api.src.sernia_ai.zillow_leads.agent import (
    LEAD_AGENT_MODEL,
    Escalate,
    LeadFacts,
    NoReply,
    Reply,
    TourState,
    respond,
)
from api.src.sernia_ai.zillow_leads.case import build_case, render_case
from api.src.sernia_ai.zillow_leads.thread import LeadThread, load_lead_thread
from api.src.sernia_ai.zillow_leads.tours import NoTours, TourSource, tours_for_case

# Marks a conversation as belonging to the lead agent.
HANDLER = "zillow_lead_agent"
# Where open tour times and booked tours come from. Nothing knows them until
# the booking system exists (issue #368), which replaces this.
TOUR_SOURCE: TourSource = NoTours()
# Names shown on the approval card. Neither is a tool the model can call.
SEND_REPLY = "send_lead_reply"
ASK_TEAM = "lead_needs_a_person"

_ZILLOW_DOMAIN = "zillow.com"


@dataclass(frozen=True)
class ReplyRoute:
    """Where a reply to this thread goes. Decided by code, from the thread."""

    to: str
    subject: str
    reply_to_message_id: str  # the lead's latest message, in the shared mailbox


@dataclass(frozen=True)
class Handled:
    """What happened to a thread."""

    persisted: bool
    action: str  # reply, no_reply, escalate, or skipped
    pending: bool = False


def unusable_data_escalation(problem: str) -> Escalate:
    """The outcome when listing data cannot be used: a person, with the reason.

    Decided by code, without the agent. An agent with no facts could only
    escalate or guess, and the person needs to know the data is the cause.
    """
    return Escalate(
        lead=LeadFacts(),
        tour=TourState(),
        reason="Listing data could not be used, so no reply was drafted.",
        question=(
            f"The lead agent's listing data (the rental_listings table) cannot be used: {problem}. "
            "Every lead comes to a person until it is fixed. Please fix the data, then "
            "answer this lead."
        ),
    )


def reply_route(thread: LeadThread) -> ReplyRoute | None:
    """Where to answer the lead's latest message, or None if there is nowhere safe.

    A reply goes only to a Zillow relay address. Zillow gives each lead one,
    and answering it is the only kind of outbound email this module sends, so
    an address anywhere else (a lead's own mailbox, one of ours, a no-reply
    sender) is refused here and the thread goes to a person.
    """
    lead_messages = [message for message in thread.messages if not message.from_sernia]
    if not lead_messages:
        return None
    latest = lead_messages[-1]
    address = (latest.reply_to or latest.from_address or "").strip().lower()
    local, _, domain = address.partition("@")
    at_zillow = domain == _ZILLOW_DOMAIN or domain.endswith(f".{_ZILLOW_DOMAIN}")
    if not local or not at_zillow or domain == INTERNAL_EMAIL_DOMAIN:
        return None
    if local.replace("-", "").replace("_", "") in {"noreply", "donotreply"}:
        return None
    subject = latest.subject.strip()
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return ReplyRoute(to=address, subject=subject, reply_to_message_id=latest.message_id)


def _call(tool_name: str, args: dict[str, Any]) -> ToolCallPart:
    return ToolCallPart(
        tool_name=tool_name, args=json.dumps(args), tool_call_id=f"lead-{uuid.uuid4().hex}"
    )


def conversation_for(
    case_text: str, outcome: Reply | NoReply | Escalate, route: ReplyRoute | None
) -> list[ModelMessage]:
    """The outcome as a conversation the approval screen can show.

    A reply or an escalation becomes a pending call for a person to decide.
    ``to`` and ``subject`` are on the card so the approver can see them; they
    are for display, and the send uses the stored route.
    """
    request = ModelRequest(parts=[UserPromptPart(content=case_text)])
    if isinstance(outcome, Reply) and route is not None:
        part: ToolCallPart | TextPart = _call(
            SEND_REPLY,
            {
                "to": route.to,
                "subject": route.subject,
                "body": outcome.reply_body,
                "why": outcome.reason,
                "team_notes": outcome.team_notes,
            },
        )
    elif isinstance(outcome, NoReply):
        part = TextPart(content=f"No reply needed. {outcome.reason}")
    else:
        question = (
            outcome.question
            if isinstance(outcome, Escalate)
            else "A reply was drafted, but this thread has no Zillow address to send it to."
        )
        part = _call(
            ASK_TEAM,
            {"question": question, "why": outcome.reason, "team_notes": outcome.team_notes},
        )
    return [request, ModelResponse(parts=[part], model_name=LEAD_AGENT_MODEL)]


async def handle_thread(
    thread_id: str, *, conversation_id: str, metadata: dict[str, Any]
) -> Handled | None:
    """Run the lead agent, save its outcome, and apply its delivery setting.

    Returns None when the thread cannot be loaded, so the queue retries. Errors
    from Gmail or the model propagate for the same reason.
    """
    thread = await load_lead_thread(thread_id)
    if thread is None or not thread.messages:
        return None
    if thread.messages[-1].from_sernia:
        # Someone on the team answered while this was queued. Nothing is owed.
        logfire.info("zillow lead: already answered, skipping", thread_id=thread_id)
        return Handled(persisted=True, action="skipped")

    now = datetime.now(UTC)
    loaded = await listing_data.load()
    tours = await tours_for_case(TOUR_SOURCE, loaded.data, now)
    case = build_case(
        thread, loaded.data, now=now, slots=tours.open_times, booked_tours=tours.booked
    )
    if loaded.problem:
        outcome: Reply | NoReply | Escalate = unusable_data_escalation(loaded.problem)
    else:
        outcome = await respond(case)
    route = reply_route(thread)
    messages = conversation_for(render_case(case), outcome, route)
    pending = extract_pending_approval_from_messages(messages)

    stored = {
        **metadata,
        "handler": HANDLER,
        "lead_thread_id": thread_id,
        "outcome": outcome.model_dump(mode="json"),
        # The route the send will use. Stored here, not read back from the
        # card, so nothing typed into an approval can redirect a reply.
        "reply_route": (
            {
                "to": route.to,
                "subject": route.subject,
                "reply_to_message_id": route.reply_to_message_id,
            }
            if route
            else None
        ),
    }
    try:
        async with AsyncSessionFactory() as session:
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                clerk_user_id=TRIGGER_BOT_ID,
                metadata=stored,
            )
        persisted = True
    except Exception:
        logfire.exception("zillow lead: conversation not saved", conversation_id=conversation_id)
        persisted = False

    if persisted and isinstance(outcome, Reply) and route is not None:
        from api.src.sernia_ai.zillow_leads.auto_reply import maybe_send_reply

        try:
            await maybe_send_reply(conversation_id, case, outcome)
        except Exception:
            logfire.exception(
                "zillow lead: automatic delivery held for review", conversation_id=conversation_id
            )
        # Delivery may have resolved the send card or replaced it with an
        # inspect-only task. Notify about what is actually waiting now.
        async with AsyncSessionFactory() as session:
            messages = await get_conversation_messages(conversation_id, None, session=session)
        pending = extract_pending_approval_from_messages(messages)

    if pending and persisted:
        first = pending[0]
        create_logged_task(
            notify_pending_approval(
                conversation_id=conversation_id,
                tool_name=first["tool_name"],
                tool_args=first.get("args"),
            ),
            name="notify_pending_approval",
        )
    logfire.info(
        "zillow lead: handled",
        thread_id=thread_id,
        conversation_id=conversation_id,
        action=outcome.action,
        pending=bool(pending),
    )
    return Handled(persisted=persisted, action=outcome.action, pending=bool(pending))


async def send_reply(
    route: dict[str, str], body: str, *, threading: dict[str, str] | None = None
) -> str:
    """Send an approved reply to the stored route, on the lead's thread."""
    if threading is None:
        threading = await _get_threading_headers(
            route["reply_to_message_id"], SHARED_EXTERNAL_EMAIL
        )
    result = await _send_email(
        to=route["to"],
        subject=route["subject"],
        message_text=body,
        message_html=None,
        sender=GOOGLE_DELEGATION_EMAIL,
        credentials=get_delegated_credentials(
            user_email=SHARED_EXTERNAL_EMAIL, scopes=GMAIL_SCOPES
        ),
        **threading,
    )
    return f"Email sent to {route['to']} (message ID: {result.get('id', 'unknown')})."


def is_lead_conversation(metadata: dict[str, Any] | None) -> bool:
    return bool(metadata) and metadata.get("handler") == HANDLER


async def apply_approval(
    session: AsyncSession,
    *,
    conversation_id: str,
    metadata: dict[str, Any],
    decisions: list[ApprovalDecision],
    approver: str,
) -> dict[str, Any]:
    """Carry out a person's decisions on a lead conversation. No model runs.

    An approved reply is sent to the stored route with the approved words (the
    draft, or the approver's edit). A rejected one is not sent. Acknowledging
    an escalation sends nothing. Each decision is recorded in the conversation
    so it is no longer pending, and so approvals can later be read as labels:
    sent unchanged, edited, or rejected.

    Two approvals of one reply send one email. The conversation's row is locked
    from here until the decisions are saved, so a second approval (two people
    at once, or a double click) waits, then finds nothing pending.
    """
    # Lock the row and re-read it: the caller's session may hold a copy loaded
    # before another request decided this conversation. The lock is released
    # when the save below commits, or when the session ends after an error.
    await session.get(
        AgentConversation, conversation_id, with_for_update=True, populate_existing=True
    )
    messages = await get_conversation_messages(conversation_id, None, session=session)
    if not messages:
        raise ValueError(f"No conversation found with ID: {conversation_id}")
    pending = {
        item["tool_call_id"]: item for item in extract_pending_approval_from_messages(messages)
    }
    route = metadata.get("reply_route")

    returns: list[ToolReturnPart] = []
    results: dict[str, str] = {}
    record: list[dict[str, Any]] = []
    for decision in decisions:
        # Taken out as it is decided, so the same id twice in one request acts once.
        call = pending.pop(decision.tool_call_id, None)
        if call is None:  # already decided, or never existed
            continue
        args = call["args"] or {}
        edited = False
        body = None
        if call["tool_name"] == SEND_REPLY and decision.approved:
            # Only the words can be changed by an approver; any other key in an
            # override is ignored, and the recipient comes from the stored route.
            body = (decision.override_args or {}).get("body") or args.get("body", "")
            edited = body != args.get("body", "")
            if not route or not body.strip():
                outcome = "Not sent: this conversation has no stored route or no text."
            else:
                outcome = await send_reply(route, body)
        elif call["tool_name"] == SEND_REPLY:
            outcome = f"Not sent. {decision.denial_reason or 'Rejected.'}"
        else:
            outcome = "Acknowledged." if decision.approved else "Dismissed."
        returns.append(
            ToolReturnPart(
                tool_name=call["tool_name"], content=outcome, tool_call_id=decision.tool_call_id
            )
        )
        results[decision.tool_call_id] = outcome
        record.append(
            {
                "tool_name": call["tool_name"],
                "approved": decision.approved,
                "edited": edited,
                "sent_body": body
                if call["tool_name"] == SEND_REPLY and decision.approved
                else None,
                "by": approver,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    if not returns:
        raise ValueError("Nothing is waiting for a decision in this conversation.")

    messages = [
        *messages,
        ModelRequest(parts=list(returns)),
        ModelResponse(parts=[TextPart(content=" ".join(results.values()))]),
    ]
    await save_agent_conversation(
        session,
        conversation_id=conversation_id,
        agent_name=AGENT_NAME,
        messages=messages,
        metadata={"approval": record},
    )
    logfire.info("zillow lead: approval applied", conversation_id=conversation_id, record=record)
    return {
        "conversation_id": conversation_id,
        "output": " ".join(results.values()),
        "pending": [],
        "tool_results": results,
        "status": "completed",
        "decisions": [
            {"tool_call_id": d.tool_call_id, "approved": d.approved}
            for d in decisions
            if d.tool_call_id in results
        ],
    }
