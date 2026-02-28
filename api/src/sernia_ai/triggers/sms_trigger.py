"""
SMS trigger for the Sernia AI agent.

Called from the OpenPhone webhook handler after an inbound SMS is persisted.
Runs the Sernia agent in a background task to analyze the message and decide
whether the team needs to be alerted.

Coexists with the existing Twilio escalation logic in open_phone/escalate.py.
"""
import logfire

from api.src.sernia_ai.triggers.background_runner import run_agent_for_trigger


async def handle_inbound_sms(event_data: dict) -> None:
    """
    Process an inbound SMS via the Sernia AI agent.

    Called as a FastAPI background task from the OpenPhone webhook handler.
    Runs alongside existing Twilio escalation — both fire independently.

    Args:
        event_data: Extracted event data from OpenPhoneWebhookPayload.
                    Expected keys: from_number, message_text, event_id.
    """
    from_number = event_data.get("from_number", "")
    message_text = event_data.get("message_text", "")
    event_id = event_data.get("event_id", "")

    if not from_number or not message_text:
        logfire.info("sms_trigger: skipping event with missing data", event_id=event_id)
        return

    logfire.info(
        "sms_trigger: processing inbound SMS",
        event_id=event_id,
        from_number=from_number,
        message_length=len(message_text),
    )

    trigger_prompt = f"""\
An inbound SMS was received. Analyze this message and decide if the team needs to be alerted.

**From:** {from_number}
**Message:** {message_text}

Use your tools to:
1. Look up who this person is — `search_contacts` with their phone number
2. Check recent SMS history with them — `get_contact_sms_history` for context
3. Review any relevant workspace notes or memory about this contact

Then decide whether the team needs to act on this."""

    trigger_context = """\
This is an inbound SMS from a contact. The message was received via the Quo/OpenPhone \
webhook. Analyze the message in context (who sent it, recent history, any open issues) \
and decide if the Sernia team needs to be alerted.

Common scenarios needing attention:
- Maintenance requests or complaints
- Questions that need a reply
- New leads or inquiries
- Lease or payment discussions
- Urgent matters

Common scenarios that are routine:
- Simple acknowledgments ("ok", "thanks", "got it", "sounds good")
- Automated messages or read receipts
- Messages where the conversation is already resolved"""

    trigger_metadata = {
        "trigger_source": "sms",
        "trigger_phone": from_number,
        "trigger_message_preview": message_text[:200],
        "trigger_event_id": event_id,
    }

    notification_title = f"SMS from {from_number}"
    notification_body = message_text[:120]

    await run_agent_for_trigger(
        trigger_source="sms",
        trigger_prompt=trigger_prompt,
        trigger_metadata=trigger_metadata,
        trigger_context=trigger_context,
        notification_title=notification_title,
        notification_body=notification_body,
        rate_limit_key=from_number,
    )
