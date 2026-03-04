"""
Team SMS event trigger for the Sernia AI agent.

Called from the OpenPhone webhook handler when an inbound SMS arrives at the
shared team number. Assesses whether the message warrants a ClickUp maintenance
task — it does NOT notify the team (Twilio escalation handles that separately).

The agent always uses the NoAction output so no web chat conversation is
created. Any ClickUp tasks are created/updated directly via tools.
"""
from textwrap import dedent

import logfire

from api.src.sernia_ai.config import CLICKUP_MAINTENANCE_LIST_ID
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger


async def handle_team_sms_event(event_data: dict) -> None:
    """
    Process an inbound SMS to the shared team number via the Sernia AI agent.

    Called as a FastAPI background task from the OpenPhone webhook handler.
    Runs alongside existing Twilio escalation — both fire independently.

    The agent evaluates whether a ClickUp maintenance task should be created
    or updated, then always responds silently (no team notification).

    Args:
        event_data: Extracted event data from OpenPhoneWebhookPayload.
                    Expected keys: from_number, message_text, event_id.
    """
    from_number = event_data.get("from_number", "")
    message_text = event_data.get("message_text", "")
    event_id = event_data.get("event_id", "")

    if not from_number or not message_text:
        logfire.info("team_sms_event: skipping event with missing data", event_id=event_id)
        return

    logfire.info(
        "team_sms_event: processing inbound SMS",
        event_id=event_id,
        from_number=from_number,
        message_length=len(message_text),
    )

    trigger_prompt = dedent(f"""\
        An inbound SMS was received at the shared team number. Assess whether
        this message warrants creating or updating a ClickUp maintenance task.

        **From:** {from_number}
        **Message:** {message_text}

        Steps:
        1. Look up who this person is — `search_contacts` with their phone number.
           Note their property address, unit number, name, email, and any other
           relevant custom fields from their Quo contact.
        2. Check recent SMS history with them — `get_contact_sms_history` for context.
        3. Search for open ClickUp tasks that might already cover this issue —
           `search_tasks` with their name or address in the maintenance list
           (list ID: {CLICKUP_MAINTENANCE_LIST_ID}).
        4. Decide: create a new task, update an existing one, or skip.
        5. If creating/updating, call `get_maintenance_field_options` first to get
           the correct field IDs and dropdown option UUIDs.
        6a. If creating a new task, use the NoAction output tool (he will be notified automatically)
        6b. If updating, text Peppino to notify him of the update.


        After your assessment, always use the NoAction output tool.""")

    trigger_instructions = dedent(f"""\
        You are assessing an inbound SMS for potential ClickUp maintenance task
        creation. Team notifications are handled separately by Twilio escalation —
        your ONLY job is ClickUp task management.

        **Maintenance list ID:** {CLICKUP_MAINTENANCE_LIST_ID}

        **When to create a task:**
        - Tenant reports a maintenance issue (leak, broken appliance, pest, etc.)
        - Message describes a problem that needs physical attention at the property
        - No existing open task covers the same issue for the same unit

        **When to update an existing task:**
        - An open task already exists for this issue/unit — add a comment or update
          the description with new info instead of creating a duplicate

        **When to skip:**
        - Simple acknowledgments ("ok", "thanks", "got it")
        - Automated messages or read receipts
        - Messages unrelated to maintenance (rent questions, lease inquiries, etc.)
        - Conversations already resolved

        **Filling out custom fields:**
        Fill out as many custom fields as you can based on the contact's info and
        the message content. Use the contact's Quo profile for property address,
        unit number, name, phone, and email. Infer the request type from the
        message content. Do NOT guess or fabricate information you don't have —
        leave fields empty rather than making something up. For example, if
        permission to enter or pets on property is not mentioned, leave those
        fields unset.

        **You MUST always use the NoAction output tool** if you created a task
        or nothing needs to be done. This prevents the system from creating a
        duplicate notification.""")

    trigger_metadata = {
        "trigger_source": "sms",
        "trigger_phone": from_number,
        "trigger_message_preview": message_text[:200],
        "trigger_event_id": event_id,
    }

    await run_agent_for_trigger(
        trigger_source="sms",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        trigger_instructions=trigger_instructions,
        notification_title=f"SMS from {from_number}",
        notification_body=message_text[:120],
        rate_limit_key=from_number,
    )
