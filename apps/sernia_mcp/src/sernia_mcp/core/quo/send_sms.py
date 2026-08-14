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
    GROUP_SMS_MAX_RECIPIENTS,
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
    SMS_MAX_LENGTH,
    SMS_SPLIT_THRESHOLD,
)
from sernia_mcp.core.errors import ExternalServiceError, NotFoundError, ValidationError
from sernia_mcp.core.types import GroupSmsRouting, SmsResult, SmsRouting


def _contact_display_name(contact: dict, phone: str) -> str:
    defaults = contact.get("defaultFields", {})
    first = defaults.get("firstName") or ""
    last = defaults.get("lastName") or ""
    return f"{first} {last}".strip() or phone


def _is_internal_contact(contact: dict) -> bool:
    return (contact.get("defaultFields", {}).get("company") or "") == QUO_INTERNAL_COMPANY


def _get_contact_unit(contact: dict) -> tuple[str, str] | None:
    """Extract (property, unit) from a Quo contact's custom fields.

    Returns None if either field is missing (non-tenant contact). Vendored
    from ``api/src/sernia_ai/tools/quo_tools.py``.
    """
    prop = unit = None
    for field in contact.get("customFields", []):
        if field.get("name") == "Property":
            prop = (field.get("value") or "").strip()
        elif field.get("name") == "Unit #":
            unit = (field.get("value") or "").strip()
    if prop and unit:
        return (prop, unit)
    return None


async def resolve_sms_routing_core(to_phone: str) -> SmsRouting:
    """Resolve a phone number to SMS routing parameters.

    Raises ``NotFoundError`` if the phone is not a Quo contact — SMS is only
    allowed to known contacts to prevent accidental sends.
    """
    async with build_quo_client() as client:
        contact = await find_contact_by_phone(to_phone, client)
    if contact is None:
        raise NotFoundError(
            f"{to_phone} is not a Quo contact. Messages can only be sent to numbers stored in Quo."
        )
    is_internal = _is_internal_contact(contact)
    return SmsRouting(
        contact_id=contact.get("id"),
        contact_name=_contact_display_name(contact, to_phone),
        is_internal=is_internal,
        from_phone_id=QUO_SERNIA_AI_PHONE_ID if is_internal else QUO_SHARED_EXTERNAL_PHONE_ID,
        line_name="Sernia AI" if is_internal else "Sernia Capital Team",
    )


async def resolve_group_sms_routing_core(phones: list[str]) -> GroupSmsRouting:
    """Resolve a recipient list for a group SMS (one shared thread).

    The Quo API's ``POST /v1/messages`` accepts up to
    ``GROUP_SMS_MAX_RECIPIENTS`` (10) numbers in ``to`` — the message lands
    in one shared group conversation (verified live 2026-08-13).

    Deterministic gates (raise ``ValidationError`` / ``NotFoundError``) —
    all of them block before the approval card is even shown:

    - 2..GROUP_SMS_MAX_RECIPIENTS unique recipients.
    - Every recipient must be a Quo contact.
    - **Unit isolation**: tenants from different units (per the
      Property / Unit # custom fields) can never share a group thread —
      they would see each other's phone numbers.
    - **Internal/external separation**: internal team members and external
      contacts can never be mixed in one group — a mixed group would expose
      internal numbers to external recipients.

    Line selection: all-internal → AI line; all-external → shared team line.
    """
    unique_phones = list(dict.fromkeys(phones))
    if len(unique_phones) < 2:
        raise ValidationError(
            "a group SMS needs at least 2 unique recipients; "
            "pass a single phone string for a 1:1 send"
        )
    if len(unique_phones) > GROUP_SMS_MAX_RECIPIENTS:
        raise ValidationError(
            f"group SMS supports at most {GROUP_SMS_MAX_RECIPIENTS} recipients "
            f"per message (got {len(unique_phones)}); split into smaller groups"
        )

    contacts: list[dict] = []
    async with build_quo_client() as client:
        for phone in unique_phones:
            contact = await find_contact_by_phone(phone, client)
            if contact is None:
                raise NotFoundError(
                    f"{phone} is not a Quo contact. Messages can only be sent "
                    "to numbers stored in Quo."
                )
            contacts.append(contact)

    names = [_contact_display_name(c, p) for c, p in zip(contacts, unique_phones, strict=True)]
    internal_flags = [_is_internal_contact(c) for c in contacts]

    # Unit-isolation gate: block cross-unit tenant groups deterministically.
    units: dict[tuple[str, str], list[str]] = {}
    for contact, name, is_internal in zip(contacts, names, internal_flags, strict=True):
        if is_internal:
            continue
        cu = _get_contact_unit(contact)
        if cu is not None:
            units.setdefault(cu, []).append(name)
    if len(units) > 1:
        desc = "; ".join(
            f"{prop} Unit {unit}: {', '.join(unit_names)}"
            for (prop, unit), unit_names in sorted(units.items())
        )
        raise ValidationError(
            "tenants from different units can never share a group thread "
            f"({desc}); send one message per unit instead"
        )

    all_internal = all(internal_flags)

    # Internal/external separation: hard-block mixed groups deterministically.
    if not all_internal and any(internal_flags):
        internal_names = ", ".join(n for n, i in zip(names, internal_flags, strict=True) if i)
        external_names = ", ".join(n for n, i in zip(names, internal_flags, strict=True) if not i)
        raise ValidationError(
            "internal team members and external contacts can never share a "
            f"group thread (internal: {internal_names}; external: "
            f"{external_names}); send separate messages instead"
        )

    return GroupSmsRouting(
        phones=unique_phones,
        recipient_names=names,
        all_internal=all_internal,
        from_phone_id=QUO_SERNIA_AI_PHONE_ID if all_internal else QUO_SHARED_EXTERNAL_PHONE_ID,
        line_name="Sernia AI" if all_internal else "Sernia Capital Team",
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


async def _post_sms_chunks(
    recipients: list[str],
    chunks: list[str],
    from_phone_id: str,
) -> None:
    """POST each chunk to Quo with all recipients in one ``to`` array."""
    async with build_quo_client() as client:
        for i, chunk in enumerate(chunks):
            payload = {"content": chunk, "from": from_phone_id, "to": recipients}
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
                to=recipients,
                part=f"{i + 1}/{len(chunks)}",
            )


async def send_sms_core(
    to_phone: str,
    message: str,
    *,
    routing: SmsRouting | None = None,
) -> SmsResult:
    """Send a 1:1 SMS via Quo. Caller handles approval gating."""
    if len(message) > SMS_MAX_LENGTH:
        raise ValidationError(
            f"message is {len(message)} chars, max is {SMS_MAX_LENGTH}. "
            "Shorten or summarize before sending."
        )

    if routing is None:
        routing = await resolve_sms_routing_core(to_phone)

    chunks = _split_sms(message)
    await _post_sms_chunks([to_phone], chunks, routing.from_phone_id)

    return SmsResult(
        to_phone=to_phone,
        contact_name=routing.contact_name,
        line_name=routing.line_name,
        parts_sent=len(chunks),
        message_chars=len(message),
    )


async def send_group_sms_core(
    phones: list[str],
    message: str,
    *,
    routing: GroupSmsRouting | None = None,
) -> SmsResult:
    """Send a GROUP SMS via Quo — one shared thread, every recipient sees
    the message and each other's replies. Caller handles approval gating;
    the deterministic gates (contacts-only, 2-10 size, cross-unit tenant
    isolation) live in ``resolve_group_sms_routing_core``.

    Auto-splits long messages; every chunk goes to the full group.
    """
    if len(message) > SMS_MAX_LENGTH:
        raise ValidationError(
            f"message is {len(message)} chars, max is {SMS_MAX_LENGTH}. "
            "Shorten or summarize before sending."
        )

    if routing is None:
        routing = await resolve_group_sms_routing_core(phones)

    chunks = _split_sms(message)
    await _post_sms_chunks(routing.phones, chunks, routing.from_phone_id)

    return SmsResult(
        to_phone=", ".join(routing.phones),
        contact_name=", ".join(routing.recipient_names),
        line_name=routing.line_name,
        parts_sent=len(chunks),
        message_chars=len(message),
    )
