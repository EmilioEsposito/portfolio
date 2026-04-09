"""
Quo (formerly OpenPhone) tools — full API via FastMCP OpenAPI bridge + guarded send.

NOTE: This module was renamed from ``openphone_tools.py`` to ``quo_tools.py``
to reflect Quo's rebrand.  The underlying ``api/src/open_phone/`` service
module still uses the old name — that migration is tracked separately.

Fetches the public Quo OpenAPI spec, patches known schema issues, trims
verbose descriptions to save tokens, and exposes a curated set of MCP tools
(messages, contacts, calls, recordings, transcripts, conversations).

The native ``sendMessage_v1`` and ``listContacts_v1`` are filtered out and
replaced by custom tools:

- ``send_sms``: unified SMS with conditional approval — internal contacts
  send from the AI line (no approval), external contacts from the shared
  team number (requires HITL).
- ``search_contacts``: fuzzy search against a TTL-cached contact list (avoids
  dumping 50-item pages into the context window).

Core routing and sending logic (``SmsRouting``, ``resolve_sms_routing``,
``execute_sms``) is exposed at module level for reuse by scheduling tools.
"""

import json
import os
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field
from api.src.open_phone.service import (
    get_all_contacts,
    find_contact_by_phone,
    invalidate_contact_cache,
)
import re

import logfire
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.toolsets import CombinedToolset
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

from api.src.sernia_ai.config import (
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
    SMS_MAX_LENGTH,
    SMS_SPLIT_THRESHOLD,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools._logging import log_tool_error
from api.src.utils.fuzzy_json import fuzzy_filter_json

OPENPHONE_SPEC_URL = (
    "https://openphone-public-api-prod.s3.us-west-2.amazonaws.com"
    "/public/openphone-public-api-v1-prod.json"
)

# MCP-generated tools that mutate data and require human approval.
# createContact_v1 / updateContactById_v1 replaced by custom tools with
# proper Pydantic types, first-class tags, and read-merge-write safety.
_MCP_WRITE_TOOLS = frozenset({
    "deleteContact_v1",
})

# Tools to keep from the MCP toolset (the rest are filtered out to save tokens).
# Contact create/update/search → custom tools; keep delete + field defs.
_KEEP_TOOLS = frozenset({
    # Contacts
    "deleteContact_v1",
    "getContactCustomFields_v1",
    # Calls
    "listCalls_v1",
    "getCallById_v1",
    "getCallSummary_v1",
    "getCallTranscript_v1",
})


# ---------------------------------------------------------------------------
# OpenAPI spec helpers
# ---------------------------------------------------------------------------

def _fetch_and_patch_spec() -> dict:
    """Fetch the Quo (OpenPhone) OpenAPI spec, patch schema issues, and trim for tokens."""
    resp = httpx.get(OPENPHONE_SPEC_URL, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    spec = resp.json()

    # Patch: the webhooks endpoint has `examples` as a bare string instead of a
    # list, which fails Pydantic validation.  Fix it in-place before parsing.
    for path_val in spec.get("paths", {}).values():
        for method_val in path_val.values():
            if not isinstance(method_val, dict):
                continue
            for param in method_val.get("parameters", []):
                schema = param.get("schema", {})
                if isinstance(schema.get("examples"), str):
                    schema["examples"] = [schema["examples"]]

    # Patch: OpenPhone message schema uses `from` as a field name, which is a
    # Python keyword. Rename to `from_` so Pydantic can parse structured content.
    _rename_keyword_fields(spec)

    # Strip examples and verbose fields from the spec to reduce token usage.
    # These are documentation-only and don't affect API behavior.
    _strip_examples(spec)

    # Simplify bloated schemas (customFields union, deprecated params) to save
    # ~1,000+ tokens per LLM call.
    _simplify_schemas(spec)

    return spec


_PYTHON_KEYWORDS = frozenset({"from", "import", "class", "return", "global", "pass", "raise", "yield", "del", "assert"})


def _rename_keyword_fields(obj: dict | list, _depth: int = 0) -> None:
    """Rename schema property names that are Python keywords (e.g. 'from' → 'from_').

    FastMCP's json_schema_to_type fails on Python keywords in property names.
    This patches the OpenAPI spec in-place before it's parsed.
    """
    if _depth > 50:
        return
    if isinstance(obj, dict):
        # Patch 'properties' dicts in JSON schemas
        props = obj.get("properties")
        if isinstance(props, dict):
            for kw in _PYTHON_KEYWORDS:
                if kw in props:
                    props[f"{kw}_"] = props.pop(kw)
            # Also fix 'required' list references
            required = obj.get("required")
            if isinstance(required, list):
                obj["required"] = [f"{r}_" if r in _PYTHON_KEYWORDS else r for r in required]
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _rename_keyword_fields(val, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _rename_keyword_fields(item, _depth + 1)


def _simplify_schemas(spec: dict) -> None:
    """Simplify bloated OpenAPI schemas to reduce token usage.

    - customFields: Replace 5-variant anyOf union with a simple object schema.
      The full union (string|string[]|bool|datetime|number × nullable) adds ~500
      tokens per endpoint. The LLM doesn't need type-level detail — just key+value.
    - listCalls: Remove deprecated ``since`` param (replaced by createdAfter/Before).
    """
    # --- Simplify customFields everywhere in the spec ---
    _simplify_custom_fields(spec)

    # --- Remove deprecated params from listCalls ---
    calls_path = spec.get("paths", {}).get("/v1/calls", {})
    for method_val in calls_path.values():
        if not isinstance(method_val, dict):
            continue
        params = method_val.get("parameters", [])
        method_val["parameters"] = [
            p for p in params if not p.get("deprecated")
        ]


def _simplify_custom_fields(obj, _depth: int = 0) -> None:
    """Replace verbose customFields array schema with a compact version."""
    if _depth > 50:
        return
    if isinstance(obj, dict):
        props = obj.get("properties", {})
        if "customFields" in props:
            # Replace the bloated 5-variant anyOf union with a simple schema.
            props["customFields"] = {
                "type": "array",
                "description": "Custom field values. Each item needs a 'key' (field key) and 'value' (string, number, boolean, ISO datetime, string[], or null to clear).",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "The custom field key."},
                        "value": {"description": "The field value."},
                    },
                    "required": ["key", "value"],
                },
            }
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _simplify_custom_fields(val, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _simplify_custom_fields(item, _depth + 1)


def _strip_examples(obj: dict | list | str, _depth: int = 0) -> None:
    """Recursively remove 'examples', 'example', and 'deprecated' noise from a spec."""
    if isinstance(obj, dict):
        # Remove example values (they bulk up parameter schemas).
        obj.pop("examples", None)
        obj.pop("example", None)
        # Trim long descriptions (keep first sentence only).
        desc = obj.get("description")
        if isinstance(desc, str) and len(desc) > 120:
            # Keep first sentence.
            period = desc.find(". ")
            if 20 < period < 200:
                obj["description"] = desc[: period + 1]
        for val in obj.values():
            _strip_examples(val, _depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _strip_examples(item, _depth + 1)


# ---------------------------------------------------------------------------
# Contact helpers — delegated to central open_phone.service
# ---------------------------------------------------------------------------



def _get_contact_unit(contact: dict) -> tuple[str, str] | None:
    """Extract (property, unit) from a Quo contact's custom fields.

    Returns None if either field is missing (non-tenant contact).
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


def _is_internal_contact(contact: dict) -> bool:
    """Check if a contact belongs to the internal company."""
    company = contact.get("defaultFields", {}).get("company") or ""
    return company == QUO_INTERNAL_COMPANY


def _filter_tenants_by_property_unit(
    contacts: list[dict],
    properties: list[str],
    units: list[str] | None,
) -> dict[tuple[str, str], list[dict]]:
    """Group tenant contacts by (property, unit), filtered by selectors.

    Returns dict mapping (property, unit) -> list of matching contacts.
    Skips contacts without Property/Unit # fields and internal contacts.
    """
    from collections import defaultdict

    prop_set = {p.strip() for p in properties}
    unit_set = {u.strip() for u in units} if units is not None else None

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for contact in contacts:
        cu = _get_contact_unit(contact)
        if cu is None:
            continue
        if _is_internal_contact(contact):
            continue
        prop, unit = cu
        if prop not in prop_set:
            continue
        if unit_set is not None and unit not in unit_set:
            continue
        grouped[(prop, unit)].append(contact)

    return dict(grouped)


# ---------------------------------------------------------------------------
# SMS core logic — shared by send tools and scheduling tools
# ---------------------------------------------------------------------------


@dataclass
class SmsRouting:
    """Resolved SMS routing — determines phone line and approval requirement."""

    contact: dict
    contact_name: str
    is_internal: bool
    from_phone_id: str  # QUO_SERNIA_AI_PHONE_ID or QUO_SHARED_EXTERNAL_PHONE_ID
    line_name: str  # "Sernia AI" or "Sernia Capital Team"


def _contact_display_name(contact: dict, phone: str) -> str:
    """Build a display name from a Quo contact."""
    first = contact.get("defaultFields", {}).get("firstName") or ""
    last = contact.get("defaultFields", {}).get("lastName") or ""
    return f"{first} {last}".strip() or phone


async def resolve_sms_routing(
    phone: str,
    client: httpx.AsyncClient,
    conversation_id: str = "",
) -> SmsRouting | str:
    """Resolve a phone number to SMS routing parameters.

    Returns SmsRouting on success, or an error string if the recipient
    cannot be resolved or is not a Quo contact.
    """
    try:
        contact = await find_contact_by_phone(phone, client)
    except httpx.HTTPStatusError as exc:
        log_tool_error("send_sms", exc, conversation_id=conversation_id)
        return f"Error looking up contact for {phone}: HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        log_tool_error("send_sms", exc, conversation_id=conversation_id)
        return f"Error looking up contact for {phone}: {exc}"

    if contact is None:
        logfire.warn("sms blocked: recipient not in Quo", to=phone)
        return (
            f"Blocked: {phone} is not a Quo contact. "
            "Messages can only be sent to numbers stored in Quo."
        )

    is_internal = _is_internal_contact(contact)
    return SmsRouting(
        contact=contact,
        contact_name=_contact_display_name(contact, phone),
        is_internal=is_internal,
        from_phone_id=QUO_SERNIA_AI_PHONE_ID if is_internal else QUO_SHARED_EXTERNAL_PHONE_ID,
        line_name="Sernia AI" if is_internal else "Sernia Capital Team",
    )


async def execute_sms(
    client: httpx.AsyncClient,
    phone: str,
    message: str,
    from_phone_id: str,
    line_name: str,
    conversation_id: str = "",
    tool_name: str = "send_sms",
) -> str:
    """Send an SMS via Quo API, auto-splitting if over SMS_SPLIT_THRESHOLD.

    Public API — used by send_sms tool and scheduling executor.
    Delegates to ``_send_sms`` which handles chunking.
    """
    return await _send_sms(
        client, tool_name, phone, message,
        from_phone_id, line_name, conversation_id,
    )


# ---------------------------------------------------------------------------
# Retrieval tool implementations (standalone for testability)
# ---------------------------------------------------------------------------


def _build_phone_map(contacts: list[dict]) -> dict[str, str]:
    """Build phone→display_name map from a list of Quo contacts.

    Includes property-unit prefix for tenants (e.g. "659-03 Hailey Trainor"),
    unless the name already contains the prefix.
    """
    phone_map: dict[str, str] = {}
    for c in contacts:
        first = c.get("defaultFields", {}).get("firstName", "")
        last = c.get("defaultFields", {}).get("lastName", "")
        name = f"{first} {last}".strip() or "Unknown"
        unit_info = _get_contact_unit(c)
        if unit_info:
            prop, unit = unit_info
            prefix = f"{prop}-{unit}"
            if prefix not in name:
                name = f"{prefix} {name}"
        for pn in c.get("defaultFields", {}).get("phoneNumbers", []):
            val = pn.get("value")
            if val:
                phone_map[val] = name
    return phone_map


def _is_done_conversation(conv: dict) -> bool:
    """Check if a conversation is marked as done (snoozed 100+ years)."""
    snoozed = conv.get("snoozedUntil")
    if not snoozed:
        return False
    # OpenPhone marks "done" by snoozing 100 years into the future
    try:
        return snoozed[:4] > "2100"
    except (TypeError, IndexError):
        return False


async def search_contacts_impl(
    client: httpx.AsyncClient, query: str,
) -> str:
    """Core implementation of contact search (no RunContext dependency)."""
    contacts = await get_all_contacts(client)
    return fuzzy_filter_json(contacts, query, top_n=5)


async def list_active_threads_impl(
    client: httpx.AsyncClient,
    max_results: int = 20,
    updated_after_days: int | None = None,
) -> str:
    """Core implementation of active threads listing (no RunContext dependency).

    Mimics the Quo active inbox: returns all non-done conversations, sorted by
    most recent activity.  An optional ``updated_after_days`` narrows the window.
    """
    # Paginate through results to collect enough active threads.
    # ~95% of conversations are "done" (snoozed 100yr), so we must page past them.
    active: list[dict] = []
    page_token: str | None = None
    max_pages = 5  # safety limit

    for _ in range(max_pages):
        params: list[tuple[str, str]] = [
            ("phoneNumbers[]", QUO_SHARED_EXTERNAL_PHONE_ID),
            ("maxResults", "100"),
            ("excludeInactive", "true"),
        ]
        if updated_after_days is not None:
            from datetime import datetime, timedelta, timezone
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=updated_after_days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            params.append(("updatedAfter", cutoff))
        if page_token:
            params.append(("pageToken", page_token))

        try:
            resp = await client.get("/v1/conversations", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Error fetching conversations: {exc}"

        data = resp.json()
        for conv in data.get("data", []):
            if not _is_done_conversation(conv):
                active.append(conv)

        page_token = data.get("nextPageToken")
        if not page_token or len(active) >= max_results:
            break

    # Sort by most recent activity
    active.sort(key=lambda c: c.get("lastActivityAt", ""), reverse=True)
    conversations = active[:max_results]

    if not conversations:
        return "No active conversation threads found."

    contacts = await get_all_contacts(client)
    phone_map = _build_phone_map(contacts)

    # Fetch last message snippet for each thread in parallel
    import asyncio

    async def _fetch_snippet(participant_phone: str) -> tuple[str, str] | None:
        try:
            resp = await client.get(
                "/v1/messages",
                params={
                    "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                    "participants": participant_phone,
                    "maxResults": "1",
                },
            )
            resp.raise_for_status()
            msgs = resp.json().get("data", [])
            if msgs:
                direction = msgs[0].get("direction", "")
                text = msgs[0].get("text") or msgs[0].get("body") or ""
                return (direction, text)
        except httpx.HTTPError:
            pass
        return None

    # Get the external participant for each conversation (first non-internal phone)
    snippet_phones = []
    for conv in conversations:
        participants = conv.get("participants", [])
        snippet_phones.append(participants[0] if participants else "")

    snippets = await asyncio.gather(
        *(_fetch_snippet(p) for p in snippet_phones if p),
        return_exceptions=True,
    )
    # Map phone → snippet
    snippet_map: dict[str, tuple[str, str]] = {}
    for phone, result in zip(snippet_phones, snippets):
        if isinstance(result, tuple):
            snippet_map[phone] = result

    lines: list[str] = []
    for conv in conversations:
        participants = conv.get("participants", [])
        last_activity = conv.get("lastActivityAt", "?")
        conv_id = conv.get("id", "?")

        enriched = []
        for phone in participants:
            name = phone_map.get(phone, phone)
            enriched.append(f"{name} ({phone})" if name != phone else phone)

        # Build snippet line
        snippet_line = ""
        ext_phone = participants[0] if participants else ""
        snippet = snippet_map.get(ext_phone)
        if snippet:
            direction, text = snippet
            if text:
                preview = text[:80] + "..." if len(text) > 80 else text
                if direction == "outgoing":
                    snippet_line = f"\n  Snippet: You: {preview}"
                else:
                    sender = phone_map.get(ext_phone, ext_phone).split(" (")[0]
                    snippet_line = f"\n  Snippet: {sender}: {preview}"

        lines.append(
            f"Thread: {', '.join(enriched)}{snippet_line}\n"
            f"  Last activity: {last_activity}\n"
            f"  Conversation ID: {conv_id}"
        )

    return f"Active threads ({len(conversations)}):\n\n" + "\n\n".join(lines)


async def get_thread_messages_impl(
    client: httpx.AsyncClient, phone_number: str, max_results: int = 20,
) -> str:
    """Core implementation of thread message retrieval (no RunContext dependency)."""
    try:
        resp = await client.get(
            "/v1/messages",
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone_number,
                "maxResults": str(max_results),
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Error fetching messages for {phone_number}: {exc}"

    data = resp.json()
    messages = data.get("data", [])
    if not messages:
        return f"No messages found with {phone_number}."

    contacts = await get_all_contacts(client)
    phone_map = _build_phone_map(contacts)

    # Messages come newest-first; reverse for chronological order
    messages = list(reversed(messages))

    contact_name = phone_map.get(phone_number, phone_number)
    lines: list[str] = [f"SMS thread with {contact_name} ({phone_number}) — {len(messages)} messages\n"]
    for msg in messages:
        created = msg.get("createdAt", "?")
        direction = msg.get("direction", "?")
        text = msg.get("text") or msg.get("body") or "(no text)"
        sender_phone = msg.get("from_") or msg.get("from", "?")

        if direction == "outgoing":
            sender_name = "Sernia Capital"
            recipient_name = contact_name
        else:
            sender_name = phone_map.get(sender_phone, sender_phone) if isinstance(sender_phone, str) else "?"
            recipient_name = "Sernia Capital"

        if len(text) > 500:
            text = text[:500] + "..."

        lines.append(f"[{created}] {sender_name} → {recipient_name}: {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SMS conversation seeding — inject hidden context for reply handling
# ---------------------------------------------------------------------------


async def _seed_sms_conversation(
    phone: str,
    outbound_text: str,
    context: str,
) -> None:
    """Seed the recipient's ai_sms_from_ conversation with hidden context.

    Creates or appends to the conversation so that when the recipient
    replies via SMS, the AI agent has context about why the message was sent.
    """
    from api.src.ai_demos.models import (
        get_conversation_messages,
        save_agent_conversation,
    )
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.config import AGENT_NAME, TRIGGER_BOT_ID

    digits = re.sub(r"\D", "", phone)
    conv_id = f"ai_sms_from_{digits}"

    async with AsyncSessionFactory() as session:
        # Load existing conversation (if any) so we append, not overwrite
        existing = await get_conversation_messages(
            conv_id, clerk_user_id=None, session=session,
        )

        seed: list[ModelMessage] = [
            ModelRequest(parts=[
                UserPromptPart(content=f"[Context — not visible to SMS recipient: {context}]"),
            ]),
            ModelResponse(parts=[
                TextPart(content=outbound_text),
            ]),
        ]

        messages = existing + seed

        await save_agent_conversation(
            session=session,
            conversation_id=conv_id,
            agent_name=AGENT_NAME,
            messages=messages,
            clerk_user_id=TRIGGER_BOT_ID,
            metadata={"trigger_source": "ai_sms", "seeded_from_tool": True},
            modality="sms",
            contact_identifier=phone,
        )
    logfire.info(
        "sms conversation seeded with context",
        conv_id=conv_id,
        phone=phone,
        context_length=len(context),
    )


# ---------------------------------------------------------------------------
# Contact Pydantic models & helpers
# ---------------------------------------------------------------------------

# Custom field keys — kept in sync with Quo account settings.
# Run `GET /v1/contact-custom-fields` to refresh.
_CF_KEY_PROPERTY = "67a69fc2ea4fe3a7edd09276"
_CF_KEY_UNIT = "67e1f12cd6d6910515ec7ca2"
_CF_KEY_TAGS = "6827a195fe60ba0130f30b92"
_CF_KEY_LEASE_START = "68e3c2314fa9b10d97c5e294"
_CF_KEY_LEASE_END = "68e3c23f4fa9b10d97c5e296"
_CF_KEY_EXTERNAL_ID = "67a3fe231c0f12583994d994"


class PhoneNumber(BaseModel):
    """A phone number entry on a Quo contact."""
    name: str = Field(default="Phone Number", description='Label, e.g. "Phone Number", "Work", "mobile".')
    value: str = Field(description="Phone number in E.164 format, e.g. +14125551234.")


class Email(BaseModel):
    """An email entry on a Quo contact."""
    name: str = Field(default="Email", description='Label, e.g. "Email", "Work".')
    value: str = Field(description="Email address.")


class CustomField(BaseModel):
    """A custom field entry. Use getContactCustomFields_v1 to look up keys."""
    key: str = Field(description="The 24-char hex custom field key.")
    value: str | list[str] | None = Field(description="Value — string for text/date fields, list of strings for multi-select (e.g. Tags).")


def _build_custom_fields(
    tags: list[str] | None,
    custom_fields: list[CustomField | dict] | None,
) -> list[dict]:
    """Merge first-class tag param with raw custom fields list."""
    cf_map: dict[str, dict] = {}
    if custom_fields:
        for cf in custom_fields:
            d = cf.model_dump() if isinstance(cf, CustomField) else cf
            cf_map[d["key"]] = d
    if tags is not None:
        cf_map[_CF_KEY_TAGS] = {"key": _CF_KEY_TAGS, "value": tags}
    return list(cf_map.values())


def _build_contact_payload(
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    role: str | None = None,
    phone_numbers: list[PhoneNumber | dict] | None = None,
    emails: list[Email | dict] | None = None,
    tags: list[str] | None = None,
    custom_fields: list[CustomField | dict] | None = None,
    existing: dict | None = None,
) -> dict:
    """Build an API payload for create or update.

    For create (existing=None): builds from scratch.
    For update (existing=contact dict): merges only provided fields.
    """
    if existing:
        merged = json.loads(json.dumps(existing))  # deep copy
    else:
        merged = {"defaultFields": {}, "customFields": []}

    df = merged.setdefault("defaultFields", {})

    # Scalar defaultFields — set if provided (or always for create)
    for field_name, value in [
        ("firstName", first_name),
        ("lastName", last_name),
        ("company", company),
        ("role", role),
    ]:
        if value is not None:
            df[field_name] = value

    # List defaultFields — replace when provided
    if phone_numbers is not None:
        df["phoneNumbers"] = [
            pn.model_dump() if isinstance(pn, PhoneNumber) else pn
            for pn in phone_numbers
        ]
    if emails is not None:
        df["emails"] = [
            em.model_dump() if isinstance(em, Email) else em
            for em in emails
        ]

    # Custom fields — merge by key
    new_cfs = _build_custom_fields(tags, custom_fields)
    if new_cfs:
        existing_cf = {cf["key"]: cf for cf in merged.get("customFields", [])}
        for cf in new_cfs:
            existing_cf[cf["key"]] = cf
        merged["customFields"] = list(existing_cf.values())

    return {
        "defaultFields": merged["defaultFields"],
        "customFields": merged.get("customFields", []),
    }


async def _get_contact_by_id(client: httpx.AsyncClient, contact_id: str) -> dict:
    """Fetch a single contact by ID from Quo. Raises on error."""
    resp = await client.get(f"/v1/contacts/{contact_id}")
    resp.raise_for_status()
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# Build the toolset
# ---------------------------------------------------------------------------

def _build_quo_client() -> httpx.AsyncClient:
    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        logfire.warn("OPEN_PHONE_API_KEY not set — Quo tools will fail at runtime")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": api_key},
        timeout=30,
    )


def split_sms(text: str, limit: int = SMS_SPLIT_THRESHOLD) -> list[str]:
    """Split a message into chunks at sentence/newline boundaries."""
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
        for match in re.finditer(r'[.!?]\s', candidate):
            split_idx = match.end()
        if split_idx == -1:
            nl = candidate.rfind('\n')
            if nl > 0:
                split_idx = nl + 1
        if split_idx == -1:
            sp = candidate.rfind(' ')
            if sp > 0:
                split_idx = sp + 1
        if split_idx == -1:
            split_idx = limit
        chunks.append(remaining[:split_idx].rstrip())
        remaining = remaining[split_idx:].lstrip()
    return chunks


async def _send_single_sms(
    client: httpx.AsyncClient,
    tool_name: str,
    phone: str,
    message: str,
    from_phone_id: str,
    conversation_id: str,
) -> tuple[bool, str]:
    """Send one SMS via Quo API. Returns (success, detail)."""
    payload = {"content": message, "from": from_phone_id, "to": [phone]}
    logfire.info(
        "{tool_name} request",
        tool_name=tool_name,
        to=phone,
        from_phone_id=from_phone_id,
        message_length=len(message),
        payload=payload,
    )
    try:
        resp = await client.post("/v1/messages", json=payload)
    except httpx.HTTPError as exc:
        log_tool_error(tool_name, exc, conversation_id=conversation_id)
        return False, f"Error sending message: {exc}"

    if resp.status_code in (200, 201, 202):
        logfire.info(
            "{tool_name} success",
            tool_name=tool_name,
            to=phone,
            status=resp.status_code,
            response_body=resp.text[:500],
        )
        return True, ""

    logfire.error(
        "sernia tool error: {tool_name}",
        tool_name=tool_name,
        status=resp.status_code,
        body=resp.text[:500],
        conversation_id=conversation_id,
    )
    return False, f"Failed to send message (HTTP {resp.status_code}): {resp.text}"


async def _send_sms(
    client: httpx.AsyncClient,
    tool_name: str,
    phone: str,
    message: str,
    from_phone_id: str,
    line_name: str,
    conversation_id: str,
) -> str:
    """Send an SMS via Quo API, auto-splitting if over SMS_SPLIT_THRESHOLD."""
    chunks = split_sms(message)
    if len(chunks) > 1:
        logfire.info(
            "{tool_name} auto-splitting message",
            tool_name=tool_name,
            original_length=len(message),
            chunk_count=len(chunks),
        )
    for i, chunk in enumerate(chunks):
        ok, err = await _send_single_sms(
            client, tool_name, phone, chunk, from_phone_id, conversation_id,
        )
        if not ok:
            part_info = f" (part {i + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            return f"{err}{part_info}"
    parts_note = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
    return f"Message sent to {phone} from {line_name}.{parts_note}"


def _build_quo_toolset():
    spec = _fetch_and_patch_spec()
    client = _build_quo_client()

    # --- MCP toolset (read ops + contact writes) ---
    mcp_server = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="quo",
        route_maps=[RouteMap(pattern=r"^/v1/webhooks", mcp_type=MCPType.EXCLUDE)],
    )
    mcp_base = FastMCPToolset(mcp_server)

    # Keep only curated tools (drops sendMessage, listContacts + low-value lookups).
    mcp_filtered = mcp_base.filtered(
        filter_func=lambda _ctx, tool_def: tool_def.name in _KEEP_TOOLS,
    )

    # Approval gate on contact-write tools.
    mcp_toolset = mcp_filtered.approval_required(
        approval_required_func=lambda _ctx, tool_def, _args: tool_def.name in _MCP_WRITE_TOOLS,
    )

    # --- Custom tools (search_contacts + SMS tools) ---
    custom_toolset = FunctionToolset[SerniaDeps]()

    @custom_toolset.tool
    async def search_contacts(
        ctx: RunContext[SerniaDeps],
        query: str,
    ) -> str:
        """Search Quo contacts by name, phone number, or company.

        Uses fuzzy matching so slight typos still return results.
        Returns the top matching contacts as JSON.

        Args:
            query: Search term — a name, phone number, or company name.
        """
        return await search_contacts_impl(client, query)

    # ------------------------------------------------------------------
    # send_sms — unified SMS with conditional approval
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def send_sms(
        ctx: RunContext[SerniaDeps],
        to: str,
        message: str,
        context: str = "",
    ) -> str:
        """Send an SMS to any Quo contact.

        Automatically determines routing based on the recipient:
        - Internal (Sernia Capital LLC) → sends from AI direct line, no approval.
        - External (tenants, vendors) → sends from shared team number, requires approval.

        To message multiple people, call this tool once per recipient.

        Max 1000 chars — messages over this limit are rejected (shorten/summarize
        and retry). Messages over 500 chars are auto-split into multiple texts.

        Args:
            to: Recipient phone number in E.164 format (e.g. "+14125551234").
            message: The text message body to send (max 1000 chars).
            context: Optional hidden context about why this message is being sent.
                Not included in the SMS — saved to the recipient's conversation
                history so the AI has context if they reply later.
        """
        logfire.info("send_sms called", to=to, message_length=len(message))

        # Gate: message length — carriers (e.g. AT&T) reject long messages.
        if len(message) > SMS_MAX_LENGTH:
            return (
                f"Blocked: message is {len(message)} chars (max {SMS_MAX_LENGTH}). "
                "Please shorten or summarize the message and try again."
            )

        routing = await resolve_sms_routing(to, client, ctx.deps.conversation_id)
        if isinstance(routing, str):
            return routing

        # Conditional approval: external recipients require HITL
        if not routing.is_internal and not ctx.tool_call_approved:
            raise ApprovalRequired()

        logfire.info(
            "send_sms sending",
            to=to,
            name=routing.contact_name,
            is_internal=routing.is_internal,
            from_phone_id=routing.from_phone_id,
        )

        send_result = await execute_sms(
            client, to, message,
            routing.from_phone_id, routing.line_name,
            ctx.deps.conversation_id,
        )

        # Seed recipient's AI SMS conversation with hidden context
        if context and "Failed" not in send_result:
            try:
                await _seed_sms_conversation(to, message, context)
            except Exception:
                logfire.exception("send_sms: failed to seed conversation", to=to)

        return send_result

    # ------------------------------------------------------------------
    # mass_text_tenants — per-unit sharding, requires HITL
    # ------------------------------------------------------------------

    @custom_toolset.tool(requires_approval=True)
    async def mass_text_tenants(
        ctx: RunContext[SerniaDeps],
        message: str,
        properties: list[str],
        units: list[str] | None = None,
    ) -> str:
        """Send the same SMS to all tenants in one or more properties.

        Automatically finds matching tenants from the contact list, groups
        by (Property, Unit #), and sends one SMS per unit group. Roommates
        in the same unit share a thread; different units are isolated.

        Max 1000 chars — messages over this limit are rejected (shorten/summarize
        and retry). Messages over 500 chars are auto-split into multiple texts.

        Args:
            message: The text message body to send to all matching tenants (max 1000 chars).
            properties: Property names to target (e.g. ["320"] or ["320", "400"]).
            units: Optional unit filter within those properties. None = all units.
        """
        logfire.info(
            "mass_text_tenants called",
            properties=properties,
            units=units,
            message_length=len(message),
        )

        # Gate: message length — carriers (e.g. AT&T) reject long messages.
        if len(message) > SMS_MAX_LENGTH:
            return (
                f"Blocked: message is {len(message)} chars (max {SMS_MAX_LENGTH}). "
                "Please shorten or summarize the message and try again."
            )

        contacts = await get_all_contacts(client)
        grouped = _filter_tenants_by_property_unit(contacts, properties, units)

        if not grouped:
            unit_desc = f" units {units}" if units else ""
            return f"No tenants found matching properties {properties}{unit_desc}."

        results: list[str] = []
        failures: list[str] = []

        for (prop, unit), group_contacts in sorted(grouped.items()):
            phones = []
            names = []
            for c in group_contacts:
                phone_numbers = c.get("defaultFields", {}).get("phoneNumbers", [])
                if phone_numbers:
                    phones.append(phone_numbers[0].get("value"))
                    names.append(_contact_display_name(c, phone_numbers[0].get("value", "")))

            if not phones:
                failures.append(f"{prop} Unit {unit}: no phone numbers")
                continue

            # Send to each tenant in the unit individually
            unit_sent = 0
            for phone in phones:
                result = await execute_sms(
                    client,
                    phone,
                    message,
                    QUO_SHARED_EXTERNAL_PHONE_ID,
                    "Sernia Capital Team",
                    ctx.deps.conversation_id,
                    tool_name="mass_text_tenants",
                )
                if result.startswith("Message sent"):
                    unit_sent += 1
                else:
                    failures.append(f"{prop} Unit {unit} ({phone}): {result}")

            if unit_sent:
                recipient_desc = ", ".join(names)
                results.append(
                    f"{prop} Unit {unit} ({unit_sent} recipient{'s' if unit_sent != 1 else ''}: {recipient_desc})"
                )

        parts: list[str] = []
        if results:
            parts.append(f"Sent {len(results)} message{'s' if len(results) != 1 else ''}: {'; '.join(results)}.")
        if failures:
            parts.append(f"Failed: {'; '.join(failures)}.")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # list_active_sms_threads — mirrors the Quo active inbox
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def list_active_sms_threads(
        ctx: RunContext[SerniaDeps],
        max_results: int = 20,
        updated_after_days: int | None = None,
    ) -> str:
        """List active SMS conversation threads on the shared team number.

        Mirrors the Quo active inbox — returns all non-done threads, enriched
        with contact names and sorted by most recent activity.

        Args:
            max_results: Max threads to return (default 20).
            updated_after_days: Optional — only include threads updated within
                this many days. Omit for all active threads (matches Quo inbox).
        """
        return await list_active_threads_impl(client, max_results, updated_after_days)

    # ------------------------------------------------------------------
    # get_thread_messages — enriched listMessages for a phone number
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def get_thread_messages(
        ctx: RunContext[SerniaDeps],
        phone_number: str,
        max_results: int = 20,
    ) -> str:
        """Get recent SMS messages with a specific phone number on the shared team line.

        Returns messages in chronological order with contact names enriched.
        Use this to review the conversation thread with a specific contact.

        Args:
            phone_number: The contact's phone number in E.164 format (e.g. "+14125551234").
            max_results: Max messages to return (default 20, most recent).
        """
        return await get_thread_messages_impl(client, phone_number, max_results)

    # ------------------------------------------------------------------
    # create_contact — replaces MCP createContact_v1
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def create_contact(
        ctx: RunContext[SerniaDeps],
        first_name: str,
        last_name: str,
        company: str | None = None,
        role: str | None = None,
        phone_numbers: list[PhoneNumber] | None = None,
        emails: list[Email] | None = None,
        tags: list[str] | None = None,
        custom_fields: list[CustomField] | None = None,
    ) -> str:
        """Create a new Quo contact.

        Args:
            first_name: Contact's first name.
            last_name: Contact's last name.
            company: Company name.
            role: Role or type (e.g. "Tenant", "Lead", "Vendor").
            phone_numbers: Phone numbers. Each needs a value in E.164 format (e.g. "+14125551234").
            emails: Email addresses.
            tags: Tags to apply (e.g. ["Insurance", "Vendor"]). Maps to the Tags multi-select field.
            custom_fields: Additional custom fields as [{key, value}] objects.
                Use getContactCustomFields_v1 to look up field keys.
        """

        payload = _build_contact_payload(
            first_name=first_name,
            last_name=last_name,
            company=company,
            role=role,
            phone_numbers=phone_numbers,
            emails=emails,
            tags=tags,
            custom_fields=custom_fields,
        )
        resp = await client.post("/v1/contacts", json=payload)

        if resp.status_code in (200, 201):
            invalidate_contact_cache()
            created = resp.json().get("data", {})
            return f"Contact created: {first_name} {last_name} (id: {created.get('id', '?')})"

        return f"Failed to create contact (HTTP {resp.status_code}): {resp.text[:500]}"

    # ------------------------------------------------------------------
    # update_contact — safe read-merge-write (replaces MCP updateContactById_v1)
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def update_contact(
        ctx: RunContext[SerniaDeps],
        id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        company: str | None = None,
        role: str | None = None,
        phone_numbers: list[PhoneNumber] | None = None,
        emails: list[Email] | None = None,
        tags: list[str] | None = None,
        custom_fields: list[CustomField] | None = None,
    ) -> str:
        """Update a Quo contact. Only the fields you provide are changed; all
        other fields are preserved (safe read-merge-write). Requires approval.

        Args:
            id: The Quo contact ID to update.
            first_name: New first name, or omit to keep existing.
            last_name: New last name, or omit to keep existing.
            company: New company, or omit to keep existing.
            role: New role, or omit to keep existing.
            phone_numbers: Full list of phone numbers. Replaces all when provided.
            emails: Full list of emails. Replaces all when provided.
            tags: Tags to set (e.g. ["Insurance"]). Replaces all tags when provided.
            custom_fields: Additional custom fields as [{key, value}] objects.
                Only the fields you include are updated; others are preserved.
        """
        if not ctx.tool_call_approved:
            raise ApprovalRequired()

        # Fetch current contact (read-merge-write)
        try:
            existing = await _get_contact_by_id(client, id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Contact {id} not found."
            raise

        payload = _build_contact_payload(
            first_name=first_name,
            last_name=last_name,
            company=company,
            role=role,
            phone_numbers=phone_numbers,
            emails=emails,
            tags=tags,
            custom_fields=custom_fields,
            existing=existing,
        )
        resp = await client.patch(f"/v1/contacts/{id}", json=payload)

        if resp.status_code in (200, 201):
            invalidate_contact_cache()
            updated = resp.json().get("data", {})
            df = updated.get("defaultFields", {})
            name = f"{df.get('firstName', '')} {df.get('lastName', '')}".strip()
            return f"Contact updated: {name} ({id})"

        return f"Failed to update contact (HTTP {resp.status_code}): {resp.text[:500]}"

    return CombinedToolset(toolsets=[mcp_toolset, custom_toolset])


quo_toolset = _build_quo_toolset()
