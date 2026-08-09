"""
Emergency escalation tools for the Sernia AI agent.

Wraps the Twilio Studio Flow escalation in ``api/src/open_phone/escalate.py``:
a phone call from the dedicated Twilio number that Emilio's and Peppino's
phones allow through Do Not Disturb.

No HITL approval on purpose — the whole point of the tool is to reach a human
immediately when nobody is watching the inbox. The guardrails live in the tool
docstring (strict use criteria) plus the automatic escalation pipeline's
existing observability (Logfire incident IDs).
"""

import logfire
from pydantic_ai import FunctionToolset, RunContext

from api.src.open_phone.escalate import trigger_twilio_escalation
from api.src.sernia_ai.deps import SerniaDeps

escalation_toolset = FunctionToolset()

# Twilio Studio Flow message params are read aloud / texted — keep them short.
MAX_ESCALATION_MESSAGE_CHARS = 500


@escalation_toolset.tool
async def trigger_escalation(ctx: RunContext[SerniaDeps], message: str) -> str:
    """Trigger an EMERGENCY escalation: an immediate phone call to the Sernia
    escalation contacts (Emilio and Peppino) from a special Twilio number that
    bypasses Do Not Disturb, so it gets through even overnight.

    Use ONLY for genuine, time-sensitive emergencies that would worsen if not
    addressed immediately, e.g.:
    * Water actively leaking/gushing (into walls, from ceilings, onto floors)
    * Fire, smoke, or explosion
    * Active break-in, burglary, or violence
    * Gas smell / carbon monoxide alarm
    * Active, ongoing property damage
    * A tenant in danger

    Do NOT use for anything that can wait for normal channels (use SMS/email
    instead): lockouts, power outages, dripping faucets, chirping smoke-alarm
    batteries, routine maintenance, or incidents already mitigated. False
    escalations cause alarm fatigue and are worse than a slightly delayed
    response.

    Args:
        message: Short description of the emergency (max 500 chars). Include
            who reported it, the property/unit, and their phone number when
            known, e.g. "Anna (320-02, +14125551234) reports water pouring
            through her kitchen ceiling."

    Returns:
        Confirmation of how many escalation calls were placed, or an error.
    """
    message = message.strip()
    if not message:
        return "Escalation NOT triggered: message must describe the emergency."
    if len(message) > MAX_ESCALATION_MESSAGE_CHARS:
        return (
            f"Escalation NOT triggered: message is {len(message)} chars "
            f"(max {MAX_ESCALATION_MESSAGE_CHARS}). Shorten it to the essential "
            "facts and call the tool again."
        )

    logfire.info(
        "Sernia AI trigger_escalation called",
        conversation_id=ctx.deps.conversation_id,
        user_identifier=ctx.deps.user_identifier,
        modality=ctx.deps.modality,
        message=message,
    )

    # Identify the source so recipients know this came from the agent, not the
    # automatic SMS-webhook pipeline.
    message_text = f"URGENT! Sernia AI escalation: {message}"

    successful_escalations = await trigger_twilio_escalation(message_text)
    if successful_escalations == 0:
        return (
            "Escalation FAILED: no escalation calls could be placed. "
            "Fall back to send_sms/email to reach Emilio and Peppino directly."
        )
    return (
        f"Escalation triggered: {successful_escalations} phone call(s) placed "
        "to the escalation contacts via the DND-bypassing Twilio line."
    )
