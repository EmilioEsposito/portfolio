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

- **Showings agreed in the thread** are booked, moved and cancelled by code
  from the decision, only while the booking service's switch
  ``lead_agent_uses_open_times`` is on (``showing_bookings/lead_bookings.py``).
  A reply that confirms a time books it just before it is sent, and is not
  sent if the time is refused; see ``apply_approval``. The automatic send
  never sends a reply that would change a booking (``auto_reply``).

A conversation saved by this module is marked with ``HANDLER``. Approvals for
it are applied here (``apply_approval``) and never resume a model: the reply
that was approved is the reply that is sent.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import logfire
from fastapi import HTTPException
from googleapiclient.errors import HttpError
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
from api.src.agent_conversations.identity import AGENT_NAME, TRIGGER_BOT_ID
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.agent_conversations.push.service import notify_pending_approval
from api.src.database.database import AsyncSessionFactory
from api.src.google.common.config import (
    GOOGLE_DELEGATION_EMAIL,
    INTERNAL_EMAIL_DOMAIN,
    SHARED_EXTERNAL_EMAIL,
)
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import send_email as _send_email
from api.src.google.gmail.threading import GMAIL_SCOPES, _get_threading_headers
from api.src.showing_bookings import lead_bookings
from api.src.showing_bookings.lead_tours import lead_booking_for, tour_source_for_lead_agent
from api.src.utils.background_tasks import create_logged_task
from api.src.zillow_leads import listing_data
from api.src.zillow_leads.agent import (
    LEAD_AGENT_MODEL,
    Escalate,
    LeadFacts,
    NoReply,
    Reply,
    TourState,
    respond,
)
from api.src.zillow_leads.case import build_case, case_record, render_case
from api.src.zillow_leads.identity import HANDLER
from api.src.zillow_leads.thread import LeadThread, load_lead_thread
from api.src.zillow_leads.tours import NoTours, TourSource, tours_for_case

# Where open tour times and booked tours come from while the booking service's
# switch is off (``showing_bookings/lead_tours.py``): nothing open, nothing booked.
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
    source = await tour_source_for_lead_agent(fallback=TOUR_SOURCE)
    tours = await tours_for_case(source, loaded.data, now)
    # The thread's own booking links and showings: only from the booking source.
    lead = await lead_booking_for(source, thread_id, loaded.data, now)
    case = build_case(
        thread,
        loaded.data,
        now=now,
        slots=tours.open_times,
        booked_tours=tours.booked,
        booking_links=lead.booking_links if lead else None,
        own_showings=lead.own_showings if lead else None,
    )
    if loaded.problem:
        outcome: Reply | NoReply | Escalate = unusable_data_escalation(loaded.problem)
    else:
        outcome = await respond(case)
    route = reply_route(thread)
    messages = conversation_for(render_case(case), outcome, route)
    if isinstance(outcome, Reply) and route is not None:
        # The send card says what sending it does to the showings, before anyone
        # approves (switch on, and only when it changes something).
        plan = await lead_bookings.plan_reply(
            conversation_id=conversation_id,
            thread_id=thread_id,
            lead=outcome.lead,
            tour=outcome.tour,
            listed_units=[listing.unit for listing in case.listings],
            now=now,
        )
        if plan is not None:
            add_card_notes(messages, [plan.note(case.timezone)])
    if isinstance(outcome, Escalate):
        # An escalation books and cancels nothing; when it records the tour as
        # cancelled, its card says what the thread still has booked.
        still = await lead_bookings.still_booked(
            thread_id=thread_id, lead=outcome.lead, tour=outcome.tour, now=now
        )
        if still:
            add_card_notes(
                messages,
                [
                    f"Still booked for {outcome.lead.unit}: "
                    + ", ".join(_when(start, case.timezone) for start in still)
                    + ". Cancel it on the Booking settings tab if the lead cancelled."
                ],
            )
    pending = extract_pending_approval_from_messages(messages)

    stored = {
        **metadata,
        "handler": HANDLER,
        "lead_thread_id": thread_id,
        "outcome": outcome.model_dump(mode="json"),
        # The structured case the outcome was decided on, so it can be rebuilt
        # (``case.case_from_record``) and replayed as an eval scenario.
        "case": case_record(case),
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

    if persisted and isinstance(outcome, NoReply):
        # Nothing is sent, so nothing waits: a lead who wrote that they cannot
        # come has the thread's showing at that unit cancelled now.
        cancelled = await lead_bookings.cancel_from_decision(
            thread_id=thread_id, lead=outcome.lead, tour=outcome.tour, now=now
        )
        if cancelled is not None:
            await record_booking(conversation_id, cancelled.record())

    if persisted and isinstance(outcome, Reply) and route is not None:
        from api.src.zillow_leads.auto_reply import maybe_send_reply

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


def _when(start: datetime, timezone: str) -> str:
    local = start.astimezone(ZoneInfo(timezone))
    return f"{local:%A, %B} {local.day} at {local.strftime('%I:%M %p').lstrip('0')}"


def add_card_notes(
    messages: list[ModelMessage], notes: list[str], tool_call_id: str | None = None
) -> None:
    """Add lines to the team notes of a pending card (by default every pending card)."""
    pending = {
        item["tool_call_id"]
        for item in extract_pending_approval_from_messages(messages)
        if tool_call_id is None or item["tool_call_id"] == tool_call_id
    }
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_call_id in pending:
                args = part.args_as_dict()
                args["team_notes"] = [*args.get("team_notes", []), *notes]
                part.args = json.dumps(args)


async def record_booking(
    conversation_id: str, record: dict[str, Any], text: str | None = None
) -> None:
    """Save what a decision did to the thread's showings on its conversation.

    Under the conversation's row lock, after the send or the cancellation has
    happened, as ``showing_booking`` in its metadata, with an optional line in
    the conversation. Logged, never raised: the booking itself is saved already.
    """
    try:
        async with AsyncSessionFactory() as session:
            await session.get(
                AgentConversation, conversation_id, with_for_update=True, populate_existing=True
            )
            messages = await get_conversation_messages(conversation_id, None, session=session)
            if text:
                messages.append(ModelResponse(parts=[TextPart(content=text)]))
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={"showing_booking": record},
            )
    except Exception:
        logfire.exception(
            "zillow lead: showing booking not recorded", conversation_id=conversation_id
        )


def send_result(error: BaseException) -> Literal["failed", "uncertain"]:
    """Whether a failed send certainly sent nothing ("failed") or may have ("uncertain").

    Certain only when Gmail answered with a refusal (a 4xx other than a
    timeout), or the request was refused before it reached Gmail (a 4xx
    ``HTTPException`` raised inside ``send_email``, such as missing scopes).
    ``send_email`` wraps every error in a 500, so the original is found in the
    chain. A timeout, a 5xx or anything else may have gone out.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = None
        if isinstance(current, HttpError):
            status = getattr(current.resp, "status", None)
        elif isinstance(current, HTTPException) and current is not error:
            status = current.status_code
        try:
            code = int(status) if status is not None else None
        except (TypeError, ValueError):
            code = None
        if code is not None and 400 <= code < 500 and code != 408:
            return "failed"
        current = current.__cause__ or current.__context__
    return "uncertain"


def listed_units(record: dict[str, Any] | None) -> list[str]:
    """The units a saved case (``case.case_record``) listed."""
    return [
        listing.get("unit", "")
        for listing in (record or {}).get("listings", [])
        if listing.get("unit")
    ]


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
    # Cards left pending because the showing they confirm could not be booked.
    held: list[str] = []
    held_lines: list[str] = []
    booking: dict[str, Any] | None = None
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
            elif body.strip() != (args.get("body") or "").strip():
                # Edited words: the booking the draft would make may no longer be
                # what the reply says, so no booking change is applied at all.
                plan = await _plan_booking(conversation_id, metadata)
                outcome = await send_reply(route, body)
                skipped = await _not_applied(conversation_id, plan)
                if skipped is not None:
                    line, booking = skipped
                    outcome = f"{outcome} {line}"
            else:
                # The person approved the change the card stated. If the thread's
                # showings changed since, so that sending now would do something
                # else, the card is held with the new statement (switch on only).
                plan = await _plan_booking(conversation_id, metadata)
                stated = plan.note(_timezone(metadata)) if plan is not None else None
                shown = [
                    note
                    for note in args.get("team_notes") or []
                    if note.startswith(lead_bookings.PLAN_PREFIX)
                ]
                if stated is not None and (not shown or shown[-1] != stated):
                    add_card_notes(
                        messages, [lead_bookings.PLAN_CHANGED, stated], decision.tool_call_id
                    )
                    held.append(decision.tool_call_id)
                    held_lines.append(f"Not sent. {lead_bookings.PLAN_CHANGED} {stated}")
                    booking = {"action": "held_plan_changed", "stated": stated}
                    continue
                # Book what the reply confirms, then send (switch on only).
                prepared = await _prepare_booking(conversation_id, metadata)
                if prepared is not None and prepared.refused:
                    # Not sent: the card stays for a person, with the reason.
                    add_card_notes(messages, [prepared.problem()], decision.tool_call_id)
                    held.append(decision.tool_call_id)
                    held_lines.append(prepared.problem())
                    booking = prepared.record()
                    continue
                try:
                    outcome = await send_reply(route, body)
                except Exception as error:
                    if prepared is not None:
                        # Gmail refused it: the booking goes. Unknown: it stays, marked.
                        await lead_bookings.after_send(
                            prepared, send_result(error), now=datetime.now(UTC)
                        )
                    raise
                if prepared is not None:
                    prepared = await lead_bookings.after_send(
                        prepared, "sent", now=datetime.now(UTC)
                    )
                    booking = prepared.record()
                    outcome = f"{outcome} {prepared.note(_timezone(metadata))}"
        elif call["tool_name"] == SEND_REPLY:
            outcome = f"Not sent. {decision.denial_reason or 'Rejected.'}"
            # A place booked for this reply before it stopped is cancelled (switch on only).
            if await lead_bookings.cancel_unsent(conversation_id, now=datetime.now(UTC)):
                booking = {"action": "cancelled_unsent", "at": datetime.now(UTC).isoformat()}
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

    if not returns and not held:
        raise ValueError("Nothing is waiting for a decision in this conversation.")

    if returns:
        messages = [
            *messages,
            ModelRequest(parts=list(returns)),
            ModelResponse(parts=[TextPart(content=" ".join(results.values()))]),
        ]
    saved: dict[str, Any] = {"approval": record} if record else {}
    if booking is not None:
        saved["showing_booking"] = booking
    await save_agent_conversation(
        session,
        conversation_id=conversation_id,
        agent_name=AGENT_NAME,
        messages=messages,
        metadata=saved,
    )
    logfire.info("zillow lead: approval applied", conversation_id=conversation_id, record=record)
    if held:
        return {
            "conversation_id": conversation_id,
            "output": " ".join([*results.values(), *held_lines]),
            "pending": extract_pending_approval_from_messages(messages),
            "tool_results": results,
            "status": "pending_approval",
            "decisions": [
                {"tool_call_id": d.tool_call_id, "approved": d.approved}
                for d in decisions
                if d.tool_call_id in results
            ],
        }
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


def _timezone(metadata: dict[str, Any]) -> str:
    return (metadata.get("case") or {}).get("timezone") or "America/New_York"


async def _prepare_booking(
    conversation_id: str, metadata: dict[str, Any]
) -> lead_bookings.Prepared | None:
    """Book what an approved reply confirms, from its stored outcome and case."""
    reply = _stored_reply(metadata)
    if reply is None:
        return None
    return await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=metadata.get("lead_thread_id") or "",
        lead=reply.lead,
        tour=reply.tour,
        listed_units=listed_units(metadata.get("case")),
        now=datetime.now(UTC),
    )


def _stored_reply(metadata: dict[str, Any]) -> Reply | None:
    """The reply outcome a conversation was saved with; None for any other."""
    stored = metadata.get("outcome") or {}
    if stored.get("action") != "reply":
        return None
    try:
        return Reply.model_validate(stored)
    except ValueError:  # an outcome saved in another shape books nothing
        return None


async def _plan_booking(
    conversation_id: str, metadata: dict[str, Any]
) -> lead_bookings.Plan | None:
    """What sending the stored reply would book, move or cancel. Changes nothing."""
    reply = _stored_reply(metadata)
    if reply is None:
        return None
    return await lead_bookings.plan_reply(
        conversation_id=conversation_id,
        thread_id=metadata.get("lead_thread_id") or "",
        lead=reply.lead,
        tour=reply.tour,
        listed_units=listed_units(metadata.get("case")),
        now=datetime.now(UTC),
    )


async def _not_applied(
    conversation_id: str, plan: lead_bookings.Plan | None
) -> tuple[str, dict[str, Any]] | None:
    """After an edited reply was sent: what was not done, and the record of it.

    No booking change is applied for edited words. A place this conversation
    booked for the draft and never sent (a process stopped after booking) is
    cancelled, as a rejection cancels it: the edited words may not confirm it.
    None when there was nothing to apply (or the switch is off).
    """
    cancelled = await lead_bookings.cancel_unsent(conversation_id, now=datetime.now(UTC))
    if plan is None and not cancelled:
        return None
    action = plan.action if plan is not None else "book"
    line = (
        plan.not_applied()
        if plan is not None
        else lead_bookings.Plan("book", "", None).not_applied()
    )
    return line, {
        "action": "not_applied_edited",
        "not_applied": action,
        "unit": plan.unit if plan is not None else None,
        "start": plan.start.isoformat() if plan is not None and plan.start else None,
        "cancelled_unsent": cancelled,
        "at": datetime.now(UTC).isoformat(),
    }
