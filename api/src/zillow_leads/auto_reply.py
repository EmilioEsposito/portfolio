"""Opt-in automatic delivery of a dedicated lead agent's reply.

Reserve the original RFC Message-ID before sending. Replace the send approval
with a delivery-review card before the external call: if a process dies after
Gmail accepts the email, neither a retry nor a simultaneous approval sends it
again. An uncertain delivery stays with the team for inspection.

With the booking service's switch ``lead_agent_uses_open_times`` on, a reply
whose sending would book, move or cancel a showing goes automatically only
when the lead agent's setting for that kind is on (a new booking, a move, a
cancellation: ``settings.LeadSettings``), and, for one that books or moves,
only when its words state the time code would book in the policy's form
(``states_tour_time``). Otherwise it stays on its send card for a person, with
the reason ("This reply books a showing, so a person sends it."), as a reply
that fails a check does, and approval (``handle.apply_approval``) does the
booking.

One that may go is booked just before the send, as an approved one is
(``lead_bookings.prepare_reply``), before anything is reserved: a refused time
leaves the draft with a person. So does a booking that turns out to be another
kind of change than the one planned, when that kind is not switched on (the
lead booked or cancelled through their own link in between): what was booked
is undone and the card says what sending would do now. Once the send's result is known the booking is
settled the same way (``lead_bookings.after_send``): sent, it stands and its
held notices go; refused by Gmail, it is cancelled; unknown, it stands, marked
for the team.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

import logfire
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)
from sqlalchemy.ext.asyncio import AsyncSession

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
from api.src.zillow_leads.case import LeadCase, times_allowed, tour_time_words
from api.src.zillow_leads.settings import LeadSettings, effective_settings

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


def states_tour_time(case: LeadCase, reply: Reply) -> bool:
    """Whether the reply's words state the time its outcome confirms, in the policy's form.

    True when the text contains the outcome's ``tour.start``, in the case's
    time zone, written as the policy has a confirming reply write it
    ("Wednesday, April 14 at 4:00 PM", from ``case.tour_time_words``). Line
    breaks, repeated spaces and letter case are ignored; any other wording is
    not the fixed form. False without a start, or with one that has no UTC
    offset.

    The automatic send requires it of a reply that would book or move a
    showing (``booking_hold``): the booked time is the outcome's structured
    start, and this is the check that the words agree with it. The evals'
    ``ReplyChecks`` report it too.
    """
    start = reply.tour.start
    if start is None or start.tzinfo is None:
        return False
    wanted = " ".join(tour_time_words(start, case.timezone).split()).casefold()
    said = " ".join(reply.reply_body.split()).casefold()
    return re.search(rf"(?<!\w){re.escape(wanted)}(?![\w:])", said) is not None


def booking_hold(
    settings: LeadSettings, plan: lead_bookings.Plan, case: LeadCase, reply: Reply
) -> str | None:
    """Why a reply that would book, move or cancel a showing waits for a person.

    None when it may go automatically: the setting for its kind is on, and one
    that books or moves states the time code would book (``states_tour_time``).
    A cancellation names no time to check.
    """
    allowed = {
        "book": settings.auto_send_new_booking,
        "move": settings.auto_send_reschedule,
        "cancel": settings.auto_send_cancellation,
    }[plan.action]
    if not allowed:
        return plan.person_sends()
    if plan.action != "cancel" and not states_tour_time(case, reply):
        return plan.time_not_stated(case.timezone)
    return None


async def _hold(
    session: AsyncSession,
    conversation_id: str,
    messages: list[ModelMessage],
    card: dict[str, Any],
    reasons: list[str],
    booking: dict[str, Any] | None = None,
) -> None:
    """Keep the draft for a person and put the reasons on its existing card."""
    for part in messages[-1].parts:
        if getattr(part, "tool_call_id", None) == card["tool_call_id"]:
            args = {**card["args"]}
            args["team_notes"] = [*args.get("team_notes", []), *reasons]
            part.args = json.dumps(args)
    metadata: dict[str, Any] = {"auto_delivery": {"status": "needs_review", "reasons": reasons}}
    if booking is not None:
        metadata["showing_booking"] = booking
    await save_agent_conversation(
        session,
        conversation_id=conversation_id,
        agent_name=AGENT_NAME,
        messages=messages,
        metadata=metadata,
    )


async def _settle_unsent(
    conversation_id: str,
    prepared: lead_bookings.Prepared,
    result: lead_bookings.SendResult,
    card_id: str,
) -> None:
    """Settle a booking whose reply did not go out, or may not have, and say so
    on the inspect-only card left for the team, which stays pending.

    The note goes on that card, never after it: a message after a pending card
    would hide it. Logged, never raised: the booking itself is settled already.
    """
    settled = await lead_bookings.after_send(prepared, result, now=datetime.now(UTC))
    note = settled.unsent_note()
    try:
        async with AsyncSessionFactory() as session:
            await session.get(
                AgentConversation, conversation_id, with_for_update=True, populate_existing=True
            )
            messages = await get_conversation_messages(conversation_id, None, session=session)
            if note:
                handle.add_card_notes(messages, [note], card_id)
            await save_agent_conversation(
                session,
                conversation_id=conversation_id,
                agent_name=AGENT_NAME,
                messages=messages,
                metadata={"showing_booking": settled.record()},
            )
    except Exception:
        logfire.exception(
            "zillow lead: showing booking not recorded", conversation_id=conversation_id
        )


async def maybe_send_reply(conversation_id: str, case: LeadCase, reply: Reply) -> None:
    settings = await effective_settings()
    if settings.require_approval or not await is_sernia_ai_enabled():
        return

    # The showing this attempt booked for the reply, until its send is settled.
    prepared: lead_bookings.Prepared | None = None
    try:
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
            # A reply whose sending would book, move or cancel a showing (switch
            # on only) waits for a person unless the setting for its kind is on
            # and, when it books, its words state the time code would book.
            booking = {
                "conversation_id": conversation_id,
                "thread_id": row.metadata_["lead_thread_id"],
                "lead": reply.lead,
                "tour": reply.tour,
                "listed_units": [listing.unit for listing in case.listings],
            }
            plan = await lead_bookings.plan_reply(**booking, now=datetime.now(UTC))
            if plan is not None:
                hold = booking_hold(settings, plan, case, reply)
                if hold is not None:
                    problems.append(hold)
            if problems:
                await _hold(session, conversation_id, messages, card, problems)
                return

            threading = await handle._get_threading_headers(
                route["reply_to_message_id"], SHARED_EXTERNAL_EMAIL
            )
            original = threading.get("in_reply_to")
            if not original or not threading.get("thread_id"):
                return  # Never automatically send an unthreaded email.
            if plan is not None:
                # Book what the reply confirms before anything is reserved. A
                # refused time leaves the draft with a person, with the reason.
                prepared = await lead_bookings.prepare_reply(**booking, now=datetime.now(UTC))
                if prepared is not None and prepared.refused:
                    refused, prepared = prepared, None
                    await _hold(
                        session,
                        conversation_id,
                        messages,
                        card,
                        [refused.problem()],
                        booking=refused.record(),
                    )
                    return
                change = prepared.change if prepared is not None else None
                if prepared is not None and change is not None and change != plan.action:
                    # The thread's showings changed between the plan and the
                    # booking (the lead used their own link), so sending would
                    # now do another kind of thing. That kind needs its own
                    # setting; without it, undo what was booked and hold.
                    hold = booking_hold(
                        settings,
                        lead_bookings.Plan(change, prepared.unit, prepared.start),
                        case,
                        reply,
                    )
                    if hold is not None:
                        booked, prepared = prepared, None
                        if not booked.recovered:
                            await lead_bookings.after_send(booked, "failed", now=datetime.now(UTC))
                        fresh = await lead_bookings.plan_reply(**booking, now=datetime.now(UTC))
                        stated = [fresh.note(case.timezone)] if fresh is not None else []
                        await _hold(
                            session,
                            conversation_id,
                            messages,
                            card,
                            [lead_bookings.PLAN_CHANGED, *stated, hold],
                        )
                        return
            claimed = await claim_auto_approved_send(
                original, tool_name=handle.SEND_REPLY, conversation_id=conversation_id
            )
            question = "Automatic delivery was reserved. If no sent result appears, check the Gmail thread before replying; delivery may have succeeded."
            if claimed and plan is not None and prepared is not None:
                question = f"{question} {plan.if_unsent(case.timezone)}"
            review = handle._call(
                handle.ASK_TEAM,
                {
                    "question": question,
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
    except Exception:
        # Nothing was sent. A place this attempt booked goes, as for a refused
        # send; one found from an earlier attempt stays as it was found.
        if prepared is not None and not prepared.recovered:
            await lead_bookings.after_send(prepared, "failed", now=datetime.now(UTC))
        raise
    if not claimed:
        # An earlier automatic attempt holds this message's one send, so this
        # one sends nothing. A place booked just now goes; one that earlier
        # attempt booked may have been confirmed to the lead, so it stands, marked.
        if prepared is not None:
            await _settle_unsent(
                conversation_id,
                prepared,
                "uncertain" if prepared.recovered else "failed",
                review.tool_call_id,
            )
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
            # Gmail refused it: the booking goes. Unknown: it stays, marked.
            await _settle_unsent(
                conversation_id, prepared, handle.send_result(error), review.tool_call_id
            )
        return
    sent: dict[str, Any] = {
        "status": "sent",
        "body": reply.reply_body,
        "at": datetime.now(UTC).isoformat(),
    }
    saved: dict[str, Any] = {"auto_delivery": sent}
    if prepared is not None:
        # Settled before the result is saved, so a failed save cannot leave it open.
        prepared = await lead_bookings.after_send(prepared, "sent", now=datetime.now(UTC))
        saved["showing_booking"] = prepared.record()
        result = f"{result} {prepared.note(case.timezone)}"

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
            metadata=saved,
        )
