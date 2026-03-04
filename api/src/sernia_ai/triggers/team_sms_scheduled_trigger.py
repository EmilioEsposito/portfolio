"""
Scheduled SMS inbox review for the Sernia AI agent.

Runs periodically via APScheduler to review recent inbound SMS to the shared
team number and decide whether any threads need the team's attention (e.g.
unanswered questions, urgent issues).

Complements the event trigger (team_sms_event_trigger.py) which handles
ClickUp task creation in real-time. This scheduled check catches anything
that fell through the cracks — messages that arrived during off-hours, threads
where the team hasn't responded, etc.
"""
from datetime import datetime, timedelta, timezone
from textwrap import dedent

import logfire
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.models import OpenPhoneEvent
from api.src.sernia_ai.config import (
    QUO_SHARED_EXTERNAL_PHONE_ID,
    SMS_INBOX_CHECK_INTERVAL_HOURS,
)
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger


async def _fetch_recent_inbound_sms(lookback_hours: float) -> str:
    """Query the DB for recent inbound SMS to the shared team number.

    Returns a formatted summary grouped by sender, or an empty string if
    no messages were found.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(OpenPhoneEvent)
            .where(
                OpenPhoneEvent.event_type == "message.received",
                OpenPhoneEvent.phone_number_id == QUO_SHARED_EXTERNAL_PHONE_ID,
                OpenPhoneEvent.created_at >= cutoff,
            )
            .order_by(OpenPhoneEvent.created_at.desc())
        )
        events = result.scalars().all()

    if not events:
        return ""

    # Group by sender
    by_sender: dict[str, list[OpenPhoneEvent]] = {}
    for event in events:
        sender = event.from_number or "unknown"
        by_sender.setdefault(sender, []).append(event)

    lines: list[str] = []
    for sender, msgs in by_sender.items():
        lines.append(f"**From: {sender}** ({len(msgs)} message(s))")
        for msg in reversed(msgs):  # chronological order
            ts = msg.event_timestamp or msg.created_at
            ts_str = ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "?"
            text = (msg.message_text or "(no text)")[:300]
            lines.append(f"  [{ts_str}] {text}")
        lines.append("")

    return "\n".join(lines)


async def check_team_sms_inbox() -> None:
    """
    Scheduled review of recent inbound SMS to the shared team number.

    Pre-fetches messages from the DB, then runs the agent to identify
    threads needing team attention (unanswered questions, urgent issues).
    """
    logfire.info("sms_inbox_trigger: starting scheduled SMS inbox check")

    # Use 1.5x the interval for overlap (same pattern as email trigger)
    lookback_hours = SMS_INBOX_CHECK_INTERVAL_HOURS * 1.5
    messages_summary = await _fetch_recent_inbound_sms(lookback_hours)

    if not messages_summary:
        logfire.info("sms_inbox_trigger: no recent inbound SMS, skipping")
        return

    trigger_prompt = dedent(f"""\
        You are running a scheduled review of the shared team SMS inbox.
        Below are recent inbound messages from the last {lookback_hours:.0f} hours.

        {messages_summary}

        Steps:
        1. For each sender, look up who they are — `search_contacts` with
           their phone number.
        2. Check the full recent SMS thread — `get_contact_sms_history` to
           see if the team has already replied.
        3. Assess each thread:
           - Has the team replied? → No action needed
           - Is a reply expected from the team? → Flag it
           - Is this urgent or time-sensitive? → Flag with priority
           - Is this a routine auto-message or acknowledgment? → Skip

        If no threads need attention, no action is needed.
        If threads need attention, summarize each with recommended actions.""")

    trigger_instructions = dedent("""\
        This is a scheduled SMS inbox review. Your job is to catch threads
        where the team hasn't responded but should have.

        **Flag for team attention:**
        - Unanswered tenant questions or requests
        - Maintenance issues with no acknowledgment
        - Messages waiting more than a few hours for a reply
        - Urgent or time-sensitive matters

        **Do NOT flag:**
        - Threads where the team already replied
        - Simple acknowledgments ("ok", "thanks", "got it")
        - Automated messages or read receipts
        - Conversations that are clearly resolved

        **Filling out information:**
        Provide as much detail as you can from the contact's profile and
        the message history. Do NOT guess or fabricate information you
        don't have — if you're unsure about something, say so rather
        than making it up.

        If multiple threads need attention, prioritize the most urgent.""")

    trigger_metadata = {
        "trigger_source": "sms_inbox",
        "trigger_type": "scheduled_check",
    }

    await run_agent_for_trigger(
        trigger_source="sms_inbox",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        trigger_instructions=trigger_instructions,
        notification_title="SMS thread needs attention",
        notification_body="Unanswered SMS in the team inbox",
        rate_limit_key="inbox_check",
    )
