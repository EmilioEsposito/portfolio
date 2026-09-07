"""Narrow approval-free send path. Free-form send tools keep their HITL gates."""

import logfire
from pydantic_ai import FunctionToolset

from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import send_email
from api.src.sernia_ai.config import QUO_SHARED_EXTERNAL_PHONE_ID, SHARED_EXTERNAL_EMAIL
from api.src.sernia_ai.messaging.templates import (
    Delivery,
    MessageTemplate,
    TemplateId,
    TemplateRequest,
    TemplateSendResult,
    deliver_template,
    list_templates,
)
from api.src.sernia_ai.tools.quo_tools import _build_quo_client

# Separate toolset: never mutates deps or approval state on the general send tools.
templated_message_toolset = FunctionToolset()


@templated_message_toolset.tool
async def list_message_templates() -> tuple[MessageTemplate, ...]:
    """List deployed, fixed external message templates and when to use them.

    These exact SMS/email messages can be sent without HITL using
    send_templated_message. Includes lease-end USPS mail-forwarding reminders.
    """
    return list_templates()


@templated_message_toolset.tool
async def send_templated_message(
    template_id: TemplateId,
    contact_id: str,
    delivery: Delivery,
) -> TemplateSendResult:
    """Send a hard-coded external reminder by SMS, email, or both, without HITL.

    Read list_message_templates first. contact_id is an existing Quo contact ID,
    never a phone/address. Fresh lease dates must establish eligibility. Each
    requested channel needs exactly one stored destination. Check history first:
    once per lease; never automatically retry accepted/unknown channel results.
    No custom message, subject, sender, attachments, or reply headers accepted.
    """
    request = TemplateRequest(template_id=template_id, contact_id=contact_id, delivery=delivery)
    async with _build_quo_client() as client:
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
            credentials = get_delegated_credentials(
                user_email=SHARED_EXTERNAL_EMAIL,
                scopes=["https://mail.google.com"],
            )
            result = await send_email(
                to=recipient,
                subject=subject,
                message_text=body,
                sender=SHARED_EXTERNAL_EMAIL,
                credentials=credentials,
            )
            return result.get("id")

        result = await deliver_template(request, contact, sms, email)
    logfire.info("templated external message result", result=result.model_dump())
    return result
