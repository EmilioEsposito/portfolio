"""SMS routing + send for Quo (OpenPhone).

Routing rule: contacts with the configured internal company are sent via the
Sernia AI line; everyone else goes through the shared team line. The MCP tool
wrappers gate external sends behind an approval card.
"""
from __future__ import annotations

import re

import httpx
import logfire

from sernia_mcp.clients.quo import build_quo_client, find_contact_by_phone
from sernia_mcp.config import (
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
    SMS_MAX_LENGTH,
    SMS_SPLIT_THRESHOLD,
)
from sernia_mcp.core.errors import ExternalServiceError, NotFoundError, ValidationError
from sernia_mcp.core.types import SmsResult, SmsRouting


def _contact_display_name(contact: dict, phone: str) -> str:
    defaults = contact.get("defaultFields", {})
    first = defaults.get("firstName") or ""
    last = defaults.get("lastName") or ""
    return f"{first} {last}".strip() or phone


def _is_internal_contact(contact: dict) -> bool:
    return (contact.get("defaultFields", {}).get("company") or "") == QUO_INTERNAL_COMPANY


async def resolve_sms_routing_core(to_phone: str) -> SmsRouting:
    """Resolve a phone number to SMS routing parameters.

    Raises ``NotFoundError`` if the phone is not a Quo contact — SMS is only
    allowed to known contacts to prevent accidental sends.
    """
    async with build_quo_client() as client:
        contact = await find_contact_by_phone(to_phone, client)
    if contact is None:
        raise NotFoundError(
            f"{to_phone} is not a Quo contact. "
            "Messages can only be sent to numbers stored in Quo."
        )
    is_internal = _is_internal_contact(contact)
    return SmsRouting(
        contact_id=contact.get("id"),
        contact_name=_contact_display_name(contact, to_phone),
        is_internal=is_internal,
        from_phone_id=QUO_SERNIA_AI_PHONE_ID if is_internal else QUO_SHARED_EXTERNAL_PHONE_ID,
        line_name="Sernia AI" if is_internal else "Sernia Capital Team",
    )


def _split_sms(text: str, limit: int = SMS_SPLIT_THRESHOLD) -> list[str]:
    """Split at sentence/newline boundaries when above ``limit``."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        candidate = remaining[:limit]
        split_idx = -1
        for match in re.finditer(r"[.!?]\s", candidate):
            split_idx = match.end()
        if split_idx == -1:
            nl = candidate.rfind("\n")
            if nl > 0:
                split_idx = nl + 1
        if split_idx == -1:
            sp = candidate.rfind(" ")
            if sp > 0:
                split_idx = sp + 1
        if split_idx == -1:
            split_idx = limit
        chunks.append(remaining[:split_idx].rstrip())
        remaining = remaining[split_idx:].lstrip()
    return chunks


async def send_sms_core(
    to_phone: str,
    message: str,
    *,
    routing: SmsRouting | None = None,
) -> SmsResult:
    """Send an SMS via Quo. Caller handles approval gating."""
    if len(message) > SMS_MAX_LENGTH:
        raise ValidationError(
            f"message is {len(message)} chars, max is {SMS_MAX_LENGTH}. "
            "Shorten or summarize before sending."
        )

    if routing is None:
        routing = await resolve_sms_routing_core(to_phone)

    chunks = _split_sms(message)
    async with build_quo_client() as client:
        for i, chunk in enumerate(chunks):
            payload = {"content": chunk, "from": routing.from_phone_id, "to": [to_phone]}
            try:
                resp = await client.post("/v1/messages", json=payload)
            except httpx.HTTPError as exc:
                raise ExternalServiceError(f"Quo API error on part {i + 1}: {exc}") from exc
            if resp.status_code not in (200, 201, 202):
                raise ExternalServiceError(
                    f"Quo API HTTP {resp.status_code} on part {i + 1}: {resp.text[:200]}"
                )
            logfire.info(
                "send_sms_core part sent",
                to=to_phone,
                part=f"{i + 1}/{len(chunks)}",
                is_internal=routing.is_internal,
            )

    return SmsResult(
        to_phone=to_phone,
        contact_name=routing.contact_name,
        line_name=routing.line_name,
        parts_sent=len(chunks),
        message_chars=len(message),
    )
