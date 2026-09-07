"""Fixed-template sends under the standard MCP authentication middleware."""

import logfire

from sernia_mcp.clients.quo import build_quo_client
from sernia_mcp.config import QUO_SHARED_EXTERNAL_PHONE_ID
from sernia_mcp.core.google.gmail import send_email_core
from sernia_mcp.core.message_templates import (
    Delivery,
    MessageTemplate,
    TemplateId,
    TemplateRequest,
    TemplateSendResult,
    deliver_template,
    list_templates,
)
from sernia_mcp.server import mcp

# Code-owned sender. No model-controlled identity or workspace configuration.
_TEMPLATE_EMAIL_SENDER = "all@serniacapital.com"


@mcp.tool
async def list_message_templates() -> tuple[MessageTemplate, ...]:
    """List fixed external SMS/email templates approved for sends without HITL.

    Includes exact wording and usage for lease-end USPS forwarding reminders.
    Use send_templated_message after reading this catalog.
    """
    return list_templates()


@mcp.tool
async def send_templated_message(
    template_id: TemplateId,
    contact_id: str,
    delivery: Delivery,
) -> TemplateSendResult:
    """Send an approved hard-coded external reminder by sms, email, or both; no HITL.

    Read list_message_templates first. Use an existing Quo contact_id, not a raw
    address. Fresh lease dates must establish eligibility. Each requested channel
    needs exactly one stored destination. Check history first: once per lease.
    Never automatically retry accepted/unknown channels. No custom content,
    subject, sender, attachments, or reply headers are accepted.
    """
    request = TemplateRequest(template_id=template_id, contact_id=contact_id, delivery=delivery)
    async with build_quo_client() as client:
        response = await client.get(f"/v1/contacts/{request.contact_id}")
        response.raise_for_status()
        contact = response.json()["data"]

        async def sms(recipient: str, body: str) -> str | None:
            result = await client.post(
                "/v1/messages",
                json={
                    "to": [recipient],
                    "from": QUO_SHARED_EXTERNAL_PHONE_ID,
                    "content": body,
                },
            )
            result.raise_for_status()
            return result.json().get("data", {}).get("id")

        async def email(recipient: str, subject: str, body: str) -> str | None:
            result = await send_email_core(
                to=[recipient],
                subject=subject,
                body=body,
                user_email=_TEMPLATE_EMAIL_SENDER,
                sender_override=_TEMPLATE_EMAIL_SENDER,
            )
            return result.message_id

        result = await deliver_template(request, contact, sms, email)
    logfire.info("templated external message result", result=result.model_dump())
    return result
