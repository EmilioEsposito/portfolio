"""
Email trigger for the Sernia AI agent.

Runs periodically via APScheduler to check for emails needing attention.
Two modes:
1. General email check — checks INBOX for unread needing response
2. Zillow email check (business hours) — Zillow lead processing

The Zillow check subsumes the existing api/src/zillow_email/ jobs. The agent uses
its existing email tools (search_emails, read_email) and calendar/contact tools
to handle leads end-to-end.
"""
from textwrap import dedent

import logfire

from api.src.sernia_ai.config import (
    GENERAL_EMAIL_CHECK_INTERVAL_MINUTES,
    ZILLOW_EMAIL_CHECK_INTERVAL_HOURS,
)
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger


def _lookback_gmail(interval_minutes: int) -> str:
    """Return a Gmail newer_than value with ~50% overlap over the interval."""
    minutes = int(interval_minutes * 1.5)
    if minutes >= 60:
        hours = (minutes + 59) // 60  # round up
        return f"{hours}h"
    return f"{minutes}m"


async def check_general_emails() -> None:
    """
    Check for general emails that need Sernia team attention.

    Uses the agent's existing email tools to find and analyze relevant emails.
    """
    logfire.info("email_trigger: starting general email check")

    lookback = _lookback_gmail(GENERAL_EMAIL_CHECK_INTERVAL_MINUTES)

    trigger_prompt = dedent(f"""\
        You are running a scheduled email check. Search for recent unread
        emails that may need the team's attention.

        Steps:
        1. Search for unread emails from the last {lookback}:
           search_emails("in:inbox newer_than:{lookback} -from:zillow.com")
        2. For any that look relevant (not automated, not spam, not already
           handled), read the full email
        3. For each email needing attention, provide a summary and recommended action

        Focus on:
        - Tenant communications
        - Vendor responses
        - Business-critical emails (legal, financial, insurance)
        - Any email that seems to need a timely reply

        Ignore:
        - Marketing/promotional emails
        - Automated notifications from tools (ClickUp, GitHub, Railway, Logfire, etc.)
        - Emails already replied to
        - Zillow emails (handled by a separate check)

        If no emails need attention, no action is needed.
        If multiple emails need attention, prioritize the most urgent ones.""")

    trigger_instructions = dedent("""\
        This is a scheduled general email check. Search the inbox for recent
        unread emails that need the Sernia team's attention. Exclude Zillow
        emails (separate trigger) and automated tool notifications. Focus on
        business communications that need a reply.""")

    trigger_metadata = {
        "trigger_source": "email",
        "trigger_type": "scheduled_check",
    }

    await run_agent_for_trigger(
        trigger_source="email",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        trigger_instructions=trigger_instructions,
        notification_title="Email needs attention",
        notification_body="New email(s) requiring your review",
        rate_limit_key="general",
    )


async def check_zillow_emails() -> None:
    """
    Check Zillow emails for new leads and required follow-ups.

    Runs during business hours via APScheduler.
    Subsumes the existing zillow_email/ scheduled jobs.
    """
    logfire.info("email_trigger: starting Zillow email check")

    lookback = _lookback_gmail(ZILLOW_EMAIL_CHECK_INTERVAL_HOURS * 60)

    trigger_prompt = dedent(f"""\
        You are running a scheduled Zillow email check. Search for recent
        Zillow lead emails that need follow-up.

        Steps:
        1. Search for recent Zillow emails:
           search_emails("from:zillow.com newer_than:{lookback}")
        2. For each thread with new activity, read the full email to
           understand the state
        3. Assess each thread:
           - Is this a new lead? → Summarize the lead details
           - Does Sernia need to reply? → Explain why and suggest a response
           - Is an appointment scheduled? → Note the date/time, suggest
             creating a calendar event
           - Has the lead gone cold? → Note it but no action needed

        If no Zillow emails need attention, no action is needed.
        If you find actionable items, provide a concise analysis for each thread.""")

    trigger_instructions = dedent("""\
        This is a scheduled Zillow email check for new leads and follow-ups.

        **Zillow lead qualification criteria:**
        - Credit score below 600 → not qualified (no reply needed)
        - Credit score 670+ → qualified
        - Credit score 600-669 → case-by-case, worth engaging
        - No credit score shown → fine (no automatic disqualification)
        - Dogs → not allowed. If Zillow profile indicates dogs, ask for clarification
        - Cats → allowed
        - Other pets → case-by-case, ask for clarification
        - Move-in date mismatch → clarify if they have flexibility

        **Follow-up rules (when to NOT reply):**
        - Ball is in lead's court and they haven't responded → no follow-up
        - Appointment confirmed by both parties → no follow-up needed
        - Sernia directed them to text for phone confirmation → no follow-up
        - Lead requests virtual tour only → lower priority lead, no reply
        - Lead confirmed unqualified → no reply

        **When to reply:**
        - New qualified lead with no response from Sernia yet
        - Sernia was last to reply but implied a follow-up was needed from Sernia's side
        - Lead asked a question that hasn't been answered

        **If an appointment is scheduled:**
        - Suggest creating a calendar event with `create_calendar_event`
        - Suggest creating/updating the contact with the lead's info""")

    trigger_metadata = {
        "trigger_source": "zillow_email",
        "trigger_type": "scheduled_check",
    }

    await run_agent_for_trigger(
        trigger_source="zillow_email",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        trigger_instructions=trigger_instructions,
        notification_title="Zillow lead needs attention",
        notification_body="New Zillow lead activity detected",
        rate_limit_key="zillow",
    )
