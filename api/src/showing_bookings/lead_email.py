"""Where the reminder by email for a showing agreed in a Zillow thread goes.

A lead who agreed a showing in their email thread and has not added a mobile
with consent gets no reminder text. The evening before, they get the reminder
by email instead, in the same thread (``reminders.py`` queues it,
``delivery_checks.py`` checks it, ``outbox.py`` sends it).

Who it goes to is decided here, in code, from the lead conversation that booked
the place (``showing_attendees.lead_conversation_id``): the reply route stored
on that conversation when the lead agent handled the thread
(``handle.handle_thread``, metadata ``reply_route``). Nothing else is read: not
the attendee's own fields, not a request, nothing the lead wrote. The route is
checked again by the lead agent's own rule (``zillow_leads.relay``), and the
conversation must belong to the place's own Zillow thread. When any of that
fails, ``route_of`` answers a ``RouteProblem`` and the notice fails with its
reason, which the team's alert picks up; nothing is sent.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.models import AgentConversation
from api.src.showing_bookings.providers import ThreadRoute
from api.src.zillow_leads.identity import is_lead_conversation
from api.src.zillow_leads.relay import relay_address

# Kept on a notice queued while its route could not be found, so the row has an
# address to show. It is never sent to: the route is read again at delivery,
# and the notice fails when it is still missing.
NO_ROUTE_RECIPIENT = "no-route@route-not-found.invalid"
# The subject of a notice queued while its route could not be found.
NO_ROUTE_SUBJECT = "Your showing"


@dataclass(frozen=True)
class RouteProblem:
    """Why a reminder by email cannot go: ``reason`` is a key, ``detail`` the team's words."""

    reason: str
    detail: str


NO_CONVERSATION = RouteProblem(
    "no_conversation", "Not sent: this booking has no lead conversation to answer in."
)
CONVERSATION_MISSING = RouteProblem(
    "conversation_missing", "Not sent: the lead conversation that booked it cannot be found."
)
NOT_LEAD_CONVERSATION = RouteProblem(
    "not_lead_conversation", "Not sent: the conversation that booked it is not the lead agent's."
)
OTHER_THREAD = RouteProblem(
    "other_thread", "Not sent: the conversation that booked it is about another Zillow thread."
)
NO_ROUTE = RouteProblem("no_route", "Not sent: the lead conversation has no stored reply route.")
NOT_RELAY = RouteProblem(
    "not_relay", "Not sent: the stored reply route is not a Zillow relay address."
)
PROBLEM_REASONS = frozenset(
    problem.reason
    for problem in (
        NO_CONVERSATION,
        CONVERSATION_MISSING,
        NOT_LEAD_CONVERSATION,
        OTHER_THREAD,
        NO_ROUTE,
        NOT_RELAY,
    )
)


async def route_of(
    session: AsyncSession, conversation_id: str | None, thread_id: str | None
) -> ThreadRoute | RouteProblem:
    """The stored reply route of the lead conversation ``conversation_id``, checked.

    ``thread_id`` is the place's own Zillow thread; the conversation must be
    the lead agent's and about that thread, and its route's address a Zillow
    relay by the lead agent's rule.
    """
    if not conversation_id:
        return NO_CONVERSATION
    metadata = await session.scalar(
        select(AgentConversation.metadata_).where(AgentConversation.id == conversation_id)
    )
    if metadata is None:
        return CONVERSATION_MISSING
    if not is_lead_conversation(metadata):
        return NOT_LEAD_CONVERSATION
    if not thread_id or metadata.get("lead_thread_id") != thread_id:
        return OTHER_THREAD
    stored = metadata.get("reply_route")
    if not isinstance(stored, dict):
        return NO_ROUTE
    subject = str(stored.get("subject") or "").strip()
    message_id = str(stored.get("reply_to_message_id") or "").strip()
    if not subject or not message_id:
        return NO_ROUTE
    to = stored.get("to")
    address = relay_address(to) if isinstance(to, str) else None
    if address is None:
        return NOT_RELAY
    return ThreadRoute(to=address, subject=subject[:200], reply_to_message_id=message_id)
