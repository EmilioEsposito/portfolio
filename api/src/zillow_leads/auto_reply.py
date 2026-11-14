"""Opt-in automatic delivery of a dedicated lead agent's reply.

Reserve the original RFC Message-ID before sending. Replace the send approval
with a delivery-review card before the external call: if a process dies after
Gmail accepts the email, neither a retry nor a simultaneous approval sends it
again. An uncertain delivery stays with the team for inspection.
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
from api.src.utils.automation import is_sernia_ai_enabled
from api.src.zillow_leads import handle
from api.src.zillow_leads.agent import Reply, load_instructions
from api.src.zillow_leads.case import LeadCase
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
    allowed_times = {slot.start for slot in case.offerable_slots} | {
        tour.start for tour in case.booked_tours
    }
    if any(slot.tzinfo is None or slot not in allowed_times for slot in reply.offered_slots):
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
        review = handle._call(
            handle.ASK_TEAM,
            {
                "question": "Automatic delivery was reserved. If no sent result appears, check the Gmail thread before replying; delivery may have succeeded.",
                "why": "Avoid sending the same reply twice when delivery is interrupted.",
                "team_notes": reply.team_notes,
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
        await save_agent_conversation(
            session,
            conversation_id=conversation_id,
            agent_name=AGENT_NAME,
            messages=messages,
            metadata={
                "auto_delivery": {
                    "status": "sending" if claimed else "needs_review",
                    "at": datetime.now(UTC).isoformat(),
                }
            },
        )
    if not claimed:
        return

    # The committed review card cannot send anything. A crash from here on
    # leaves an inspect-only task, never an actionable duplicate send approval.
    try:
        result = await handle.send_reply(route, reply.reply_body, threading=threading)
    except Exception:
        logfire.exception(
            "zillow lead: automatic delivery uncertain", conversation_id=conversation_id
        )
        return

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
                }
            },
        )
