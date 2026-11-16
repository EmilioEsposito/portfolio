"""Opt-in automatic delivery of a dedicated lead agent's reply.

Reserve the original RFC Message-ID before sending. Replace the send approval
with a delivery-review card before the external call: if a process dies after
Gmail accepts the email, neither a retry nor a simultaneous approval sends it
again. An uncertain delivery stays with the team for inspection.

With the booking service's switch on, a reply that confirms a showing books
it (``showing_bookings/lead_bookings.py``) after the reservation is claimed
and before the review card is committed and Gmail is called:

    checks -> threading -> claim -> book -> review card (commit) -> send -> settle

- Booking after the claim means only the run that will send books; a retry
  or a second run finds the claim taken and books nothing.
- A refused time does not send: the reason goes on the send card, which
  stays for a person (``needs_review``), as when a check fails. The claim
  stays used, so only a person can send it after that.
- If the review card cannot be saved, nothing is sent and the booking is
  cancelled at once.
- After the send: sent, the booking's held notices go; Gmail refused it, the
  booking is cancelled before its notices go; unknown, it stands, marked on
  the Showings section, and the review card names it.
- A process that dies after the booking and before the review card commits
  leaves the send card pending: a person who approves it finds the place
  already booked and books nothing new; one who rejects it cancels it.
"""

import json
import re
from datetime import UTC, datetime

import logfire
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart

from api.src.agent_conversations.delivery import claim_auto_approved_send
from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import (
    AgentConversation,
    extract_pending_approval_from_messages,
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory
from api.src.google.common.config import SHARED_EXTERNAL_EMAIL
from api.src.showing_bookings import lead_bookings
from api.src.utils.automation import is_sernia_ai_enabled
from api.src.zillow_leads import handle
from api.src.zillow_leads.agent import Reply, load_instructions
from api.src.zillow_leads.case import LeadCase, times_allowed
from api.src.zillow_leads.settings import effective_settings

LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)


def _link(value: str) -> str:
    return value.rstrip(".,;")


def reply_problems(case: LeadCase, reply: Reply) -> list[str]:
    """Checks with exact answers, before removing review from a reply."""
    problems = []
    allowed_links = {
        _link(url)
        for text in [load_instructions(), *[listing.booking_url or "" for listing in case.listings]]
        for url in LINK.findall(text)
    }
    if any(_link(url) not in allowed_links for url in LINK.findall(reply.reply_body)):
        problems.append("The reply contains a link that was not supplied in the case or policy.")
    if not times_allowed(case, reply.offered_slots, reply.lead.unit):
        problems.append("The reply offers a time outside the supplied availability.")
    if not reply.reply_body.strip():
        problems.append("The reply is empty.")
    return problems


async def maybe_send_reply(conversation_id: str, case: LeadCase, reply: Reply) -> None:
    settings = await effective_settings()
    if settings.require_approval or not await is_sernia_ai_enabled():
        return

    async with AsyncSessionFactory() as session:
        row = await session.get(
            AgentConversation, conversation_id, with_for_update=True, populate_existing=True
        )
        messages = await get_conversation_messages(conversation_id, None, session=session)
        pending = extract_pending_approval_from_messages(messages)
        if not row or len(pending) != 1 or pending[0]["tool_name"] != handle.SEND_REPLY:
            return  # Another approver already acted, or this is an escalation.
        card = pending[0]
        route = row.metadata_.get("reply_route")
        if not route:
            return

        problems = reply_problems(case, reply)
        latest = await handle.load_lead_thread(row.metadata_["lead_thread_id"])
        if (
            not latest
            or not latest.messages
            or latest.messages[-1].from_sernia
            or latest.messages[-1].message_id != route["reply_to_message_id"]
            or handle.reply_route(latest) is None
            or handle.reply_route(latest).to != route["to"]
        ):
            problems.append("The conversation changed while the reply was being prepared.")
        if problems:
            # Keep the draft for a person and put the reason on its existing card.
            for part in messages[-1].parts:
                if getattr(part, "tool_call_id", None) == card["tool_call_id"]:
                    args = {**card["args"]}
                    args["team_notes"] = [*args.get("team_notes", []), *problems]
                    part.args = json.dumps(args)
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={"auto_delivery": {"status": "needs_review", "reasons": problems}},
            )
            return

        threading = await handle._get_threading_headers(
            route["reply_to_message_id"], SHARED_EXTERNAL_EMAIL
        )
        original = threading.get("in_reply_to")
        if not original or not threading.get("thread_id"):
            return  # Never automatically send an unthreaded email.
        claimed = await claim_auto_approved_send(
            original, tool_name=handle.SEND_REPLY, conversation_id=conversation_id
        )
        # Book what the reply confirms, only in the run that will send it.
        prepared = (
            await lead_bookings.prepare_reply(
                conversation_id=conversation_id,
                thread_id=row.metadata_["lead_thread_id"],
                lead=reply.lead,
                tour=reply.tour,
                listed_units=[listing.unit for listing in case.listings],
                now=datetime.now(UTC),
            )
            if claimed
            else None
        )
        if prepared is not None and prepared.refused:
            handle.add_card_notes(messages, [prepared.problem()], card["tool_call_id"])
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={
                    "auto_delivery": {"status": "needs_review", "reasons": [prepared.problem()]},
                    "showing_booking": prepared.record(),
                },
            )
            return
        booking_notes = (
            [prepared.note(case.timezone)]
            if prepared is not None and prepared.action in ("booked", "moved")
            else []
        )
        review = handle._call(
            handle.ASK_TEAM,
            {
                "question": "Automatic delivery was reserved. If no sent result appears, check the Gmail thread before replying; delivery may have succeeded.",
                "why": "Avoid sending the same reply twice when delivery is interrupted.",
                "team_notes": [*reply.team_notes, *booking_notes],
            },
        )
        messages = [
            *messages,
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name=handle.SEND_REPLY,
                        tool_call_id=card["tool_call_id"],
                        content="Automatic delivery reserved; any uncertainty requires a thread check.",
                    )
                ]
            ),
            ModelResponse(parts=[review]),
        ]
        try:
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={
                    "auto_delivery": {
                        "status": "sending" if claimed else "needs_review",
                        "at": datetime.now(UTC).isoformat(),
                    },
                    **({"showing_booking": prepared.record()} if prepared else {}),
                },
            )
        except Exception:
            if prepared is not None:
                # Nothing will be sent: the booking goes before its notices do.
                await lead_bookings.after_send(prepared, "failed", now=datetime.now(UTC))
            raise
    if not claimed:
        return

    # The committed review card cannot send anything. A crash from here on
    # leaves an inspect-only task, never an actionable duplicate send approval.
    try:
        result = await handle.send_reply(route, reply.reply_body, threading=threading)
    except Exception as error:
        logfire.exception(
            "zillow lead: automatic delivery uncertain", conversation_id=conversation_id
        )
        if prepared is not None:
            outcome = handle.send_result(error)
            settled = await lead_bookings.after_send(prepared, outcome, now=datetime.now(UTC))
            await handle.record_booking(
                conversation_id,
                settled.record(),
                "Gmail refused the reply, so it was not sent. The showing booked for it was cancelled."
                if outcome == "failed" and settled.cancelled
                else None,
            )
        return
    if prepared is not None:
        prepared = await lead_bookings.after_send(prepared, "sent", now=datetime.now(UTC))

    async with AsyncSessionFactory() as session:
        await session.get(
            AgentConversation, conversation_id, with_for_update=True, populate_existing=True
        )
        messages = await get_conversation_messages(conversation_id, None, session=session)
        pending = extract_pending_approval_from_messages(messages)
        if any(card["tool_call_id"] == review.tool_call_id for card in pending):
            messages.append(
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name=handle.ASK_TEAM,
                            tool_call_id=review.tool_call_id,
                            content=result,
                        )
                    ]
                )
            )
        messages.append(ModelResponse(parts=[TextPart(content=result)]))
        if reply.team_notes:
            messages.append(
                ModelResponse(
                    parts=[
                        handle._call(
                            handle.ASK_TEAM,
                            {
                                "question": "Follow-up after the automatic reply.",
                                "team_notes": reply.team_notes,
                            },
                        )
                    ]
                )
            )
        await save_agent_conversation(
            session,
            conversation_id=conversation_id,
            agent_name=AGENT_NAME,
            messages=messages,
            metadata={
                "auto_delivery": {
                    "status": "sent",
                    "body": reply.reply_body,
                    "at": datetime.now(UTC).isoformat(),
                },
                **({"showing_booking": prepared.record()} if prepared else {}),
            },
        )
