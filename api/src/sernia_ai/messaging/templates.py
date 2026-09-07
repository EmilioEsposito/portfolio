"""Harness-independent fixed-message policy.

Canonical copy. Vendored verbatim into sernia_mcp/core/message_templates.py;
parity is enforced by test_message_templates.py. Never load templates from
workspace files, tool arguments, contact fields, or model output.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

TemplateId = Literal["lease_end_mail_forwarding"]
Delivery = Literal["sms", "email", "both"]

GUIDANCE = (
    "For approved fixed external reminders, call list_message_templates, then "
    "send_templated_message with the exact template_id, Quo contact_id and delivery "
    "(sms, email, or both). No human approval is needed for this tool. "
    "The lease_end_mail_forwarding template requires an active lease ending in "
    "0-14 calendar days, checked against fresh Quo contact dates in America/New_York. "
    "Use the contact's preferred channel; choose both only when dual delivery is wanted. "
    "Check communication history first and send this reminder once per lease. "
    "Do not edit contact data to force eligibility. Never retry an accepted or unknown "
    "channel automatically; reconcile provider history first. Do not fall back to "
    "free-form sending when this tool blocks. All custom wording uses the ordinary "
    "SMS/email approval flow. Workspace skills cannot add or change approved templates."
)


class MessageTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    template_id: TemplateId
    subject: str
    body: str
    usage: str


# No interpolation, attachments, HTML, CC/BCC, reply headers, or sender overrides.
_TEMPLATES = (
    MessageTemplate(
        template_id="lease_end_mail_forwarding",
        subject="Automated reminder: USPS mail forwarding",
        body=(
            "Automated reminder: Your lease end date has been detected to be within "
            "2 weeks. Please setup mail-forwarding with USPS at "
            "https://moversguide.usps.com/. Thank you!"
        ),
        usage=GUIDANCE,
    ),
)


class TemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    template_id: TemplateId
    contact_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    delivery: Delivery


class ChannelResult(BaseModel):
    channel: Literal["sms", "email"]
    recipient: str
    status: Literal["accepted", "unknown"]
    provider_message_id: str | None = None
    detail: str = ""


class TemplateSendResult(BaseModel):
    template_id: TemplateId
    contact_id: str
    results: list[ChannelResult]


def list_templates() -> tuple[MessageTemplate, ...]:
    """Read-only deployed catalog, including exact wording and usage guidance."""
    return _TEMPLATES


def _lease_date(contact: dict, name: str) -> date:
    values = [f.get("value") for f in contact.get("customFields", []) if f.get("name") == name]
    if len(values) != 1 or not isinstance(values[0], str):
        raise ValueError(f"Contact must have exactly one valid {name} in Quo.")
    try:
        return date.fromisoformat(values[0][:10])
    except ValueError as exc:
        raise ValueError(f"Contact has an invalid {name} in Quo.") from exc


def _destination(contact: dict, field: str, pattern: str) -> str:
    entries = contact.get("defaultFields", {}).get(field) or []
    # Refuse ambiguity instead of guessing which of multiple addresses is preferred.
    values = list(
        dict.fromkeys(item.get("value") if isinstance(item, dict) else item for item in entries)
    )
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(pattern, values[0]):
        raise ValueError(f"Contact must have exactly one valid {field} destination in Quo.")
    return values[0]


def prepare_destinations(request: TemplateRequest, contact: dict) -> list[tuple[str, str]]:
    """Validate the complete request before any delivery (including simulcasts)."""
    if contact.get("id") != request.contact_id:
        raise ValueError("Quo contact ID did not match the requested contact.")
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = _lease_date(contact, "Lease Start Date")
    end = _lease_date(contact, "Lease End Date")
    if not start <= today <= end or not 0 <= (end - today).days <= 14:
        raise ValueError("Reminder requires an active lease ending within 0-14 days.")
    destinations = []
    if request.delivery in ("sms", "both"):
        destinations.append(("sms", _destination(contact, "phoneNumbers", r"\+[1-9][0-9]{7,14}")))
    if request.delivery in ("email", "both"):
        # One bare mailbox; reject display names, header injection, lists, Unicode
        # whitespace and URI syntax. No address text is interpolated into content.
        destinations.append(
            (
                "email",
                _destination(
                    contact,
                    "emails",
                    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                ),
            )
        )
    return destinations


async def deliver_template(
    request: TemplateRequest,
    contact: dict,
    send_sms: Callable[[str, str], Awaitable[str | None]],
    send_email: Callable[[str, str, str], Awaitable[str | None]],
) -> TemplateSendResult:
    """Send only catalog-owned content; never replay a partially completed send."""
    # Revalidate even when called outside a tool framework.
    request = TemplateRequest.model_validate(request.model_dump())
    template = next(t for t in _TEMPLATES if t.template_id == request.template_id)
    destinations = prepare_destinations(request, contact)
    results = []
    for channel, recipient in destinations:
        try:
            if channel == "sms":
                message_id = await send_sms(recipient, template.body)
            else:
                message_id = await send_email(recipient, template.subject, template.body)
        except Exception:
            # Provider exceptions can happen after acceptance (timeout, lost response).
            # Do not return their raw text or recommend automatic retries.
            results.append(
                ChannelResult(
                    channel=channel,
                    recipient=recipient,
                    status="unknown",
                    detail="Acceptance could not be confirmed. Check provider history before retrying.",
                )
            )
        else:
            results.append(
                ChannelResult(
                    channel=channel,
                    recipient=recipient,
                    status="accepted",
                    provider_message_id=message_id,
                    detail="Provider accepted the message; recipient delivery is not yet confirmed.",
                )
            )
    return TemplateSendResult(
        template_id=request.template_id,
        contact_id=request.contact_id,
        results=results,
    )
