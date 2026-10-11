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
        Per-recipient provider acceptance/queued/uncertain status. Acceptance does
        not confirm that a person answered or heard the message. Do not retry
        this tool or send duplicate fallback notifications for queued/uncertain
        recipients; the durable dispatcher handles safe retries.
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

    tool_call_id = ctx.tool_call_id
    if not tool_call_id or not ctx.deps.conversation_id:
        return "Escalation NOT queued: missing stable conversation/tool-call identity. Contact an operator."
    source_key = f"agent:{ctx.deps.conversation_id}:{tool_call_id}"
    try:
        result = await trigger_twilio_escalation(message_text, source_key=source_key)
    except Exception:
        # A commit or provider call may have succeeded despite a lost response.
        # Generic tool errors invite the model to create another tool-call ID.
        from api.src.open_phone.escalation_delivery import identity

        request_id = identity(source_key)
        logfire.exception("Escalation status unavailable; do not repeat", request_id=request_id)
        return (
            f"Escalation {request_id[:12]} status UNKNOWN: work may already be queued or accepted. "
            "Do not repeat this tool call or send duplicate fallback notifications. "
            "Ask an operator to inspect the durable request/provider records; human receipt is unconfirmed."
        )
    states = ", ".join(f"{recipient}: {state}" for recipient, state in result.recipients.items())
    return (
        f"Escalation {result.request_id[:12]}: {result.accepted} recipient(s) accepted by Twilio. "
        f"Status: {states or 'requires operator attention'}. "
        "Acceptance does not confirm human receipt. Queued, retry, blocked, sending, and unknown "
        "outcomes are tracked durably; do not repeat this tool call or send duplicate fallback "
        "notifications for them. Failed/manual outcomes require operator attention."
    )
