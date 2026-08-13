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
  team number (requires HITL). Accepts a single phone or a list of 2-10
  phones for a group text (one shared thread — supported by the Quo API's
  ``to`` array on ``POST /v1/messages``, verified live 2026-08-13).
- ``search_contacts``: fuzzy search against a TTL-cached contact list (avoids
  dumping 50-item pages into the context window).

Core routing and sending logic (``SmsRouting``, ``resolve_sms_routing``,
``GroupSmsRouting``, ``resolve_group_sms_routing``, ``execute_sms``) is
exposed at module level for reuse by scheduling tools.
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
import logfire
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from pydantic import BaseModel, Field
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

from api.src.open_phone.service import (
    find_contact_by_phone,
    get_all_contacts,
    invalidate_contact_cache,
)
from api.src.sernia_ai.config import (
    GROUP_SMS_MAX_RECIPIENTS,
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

# OpenPhone's list endpoints (``/v1/messages``, ``/v1/calls``) cap ``maxResults``
# at 100 per page; anything higher is rejected with an HTTP 400. Tool callers
# (the agent) pick ``max_results`` freely, so clamp before building the request
# to avoid an error-level 400 span (which trips the "Error-level records" alert)
# followed by a self-correcting retry.
OPENPHONE_MAX_RESULTS_PER_PAGE = 100

# MCP-generated tools that mutate data and require human approval.
# createContact_v1 / updateContactById_v1 replaced by custom tools with
# proper Pydantic types, first-class tags, and read-merge-write safety.
_MCP_WRITE_TOOLS = frozenset(
    {
        "deleteContact_v1",
    }
)

# Tools to keep from the MCP toolset (the rest are filtered out to save tokens).
# Contact create/update/search → custom tools; keep delete + field defs.
# getCallSummary_v1 / getCallTranscript_v1 are intentionally NOT kept — the
# custom ``get_call_details`` tool wraps both into a single curated response.
_KEEP_TOOLS = frozenset(
    {
        # Contacts
        "deleteContact_v1",
        "getContactCustomFields_v1",
        # Calls
        "listCalls_v1",
        "getCallById_v1",
    }
)


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


_PYTHON_KEYWORDS = frozenset(
    {"from", "import", "class", "return", "global", "pass", "raise", "yield", "del", "assert"}
)


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
        method_val["parameters"] = [p for p in params if not p.get("deprecated")]


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


def _parse_lease_date(value) -> date | None:
    """Parse a Quo 'date' custom field value to a ``date``.

    Quo stores lease dates as ISO strings (e.g. ``2025-03-30T16:00:00.000+0000``
    or ``2025-03-30``). We only care about the calendar day, so we read the
    leading ``YYYY-MM-DD``. Returns None for missing/unparseable values.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _has_active_lease(contact: dict, today: date | None = None) -> bool:
    """True only when the contact has a *currently active* lease.

    Reads the ``Lease Start Date`` / ``Lease End Date`` custom fields and
    returns True when ``start <= today <= end``. Contacts that are missing
    either date or whose dates don't parse — leads, prospects, and anyone
    whose lease hasn't started yet or has already ended (future / past
    tenants) — are treated as NOT active.
    """
    today = today or date.today()
    start = end = None
    for field in contact.get("customFields", []):
        name = field.get("name")
        if name == "Lease Start Date":
            start = _parse_lease_date(field.get("value"))
        elif name == "Lease End Date":
            end = _parse_lease_date(field.get("value"))
    if start is None or end is None:
        return False
    return start <= today <= end


def _filter_tenants_by_property_unit(
    contacts: list[dict],
    properties: list[str],
    units: list[str] | None,
    *,
    active_only: bool = True,
) -> dict[tuple[str, str], list[dict]]:
    """Group tenant contacts by (property, unit), filtered by selectors.

    Returns dict mapping (property, unit) -> list of matching contacts.
    Skips contacts without Property/Unit # fields and internal contacts.

    When ``active_only`` is True (the default), only contacts with a
    currently-active lease are included — leads, prospects, and future /
    past tenants (no active lease on today's date) are excluded. This is the
    safe default for building-wide notices so we never text someone who
    isn't actually a current tenant. Pass ``active_only=False`` to include
    every matching contact regardless of lease status.
    """
    from collections import defaultdict

    prop_set = {p.strip() for p in properties}
    unit_set = {u.strip() for u in units} if units is not None else None
    today = date.today()

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for contact in contacts:
        cu = _get_contact_unit(contact)
        if cu is None:
            continue
        if _is_internal_contact(contact):
            continue
        if active_only and not _has_active_lease(contact, today):
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


@dataclass
class GroupSmsRouting:
    """Resolved routing for a group SMS (one shared thread, 2+ recipients).

    Line selection: an all-internal group sends from the AI line (no
    approval); any group containing an external contact sends from the
    shared team number and requires HITL approval. A mixed group
    (internal + external members) is allowed but flagged — sending it
    exposes the internal members' numbers to the external recipients, so
    the HITL approval is the safeguard.
    """

    routings: list[SmsRouting]
    phones: list[str]  # deduped, original order preserved
    all_internal: bool
    has_external: bool
    is_mixed: bool
    from_phone_id: str
    line_name: str

    @property
    def recipient_names(self) -> list[str]:
        return [r.contact_name for r in self.routings]


async def resolve_group_sms_routing(
    phones: list[str],
    client: httpx.AsyncClient,
    conversation_id: str = "",
) -> GroupSmsRouting | str:
    """Resolve a list of phone numbers to group-SMS routing parameters.

    Returns GroupSmsRouting on success, or an error string when the group
    is invalid: fewer than 2 unique recipients, more than
    ``GROUP_SMS_MAX_RECIPIENTS`` (the Quo API's per-message ``to`` limit),
    or any recipient that is not a Quo contact.
    """
    unique_phones = list(dict.fromkeys(phones))  # dedupe, keep order
    if len(unique_phones) < 2:
        return (
            "Blocked: a group SMS needs at least 2 unique recipients. "
            "For a single recipient, pass a plain phone number string."
        )
    if len(unique_phones) > GROUP_SMS_MAX_RECIPIENTS:
        return (
            f"Blocked: group SMS supports at most {GROUP_SMS_MAX_RECIPIENTS} "
            f"recipients per message (got {len(unique_phones)}). Split into "
            "smaller groups or use mass_text_tenants for building-wide notices."
        )

    routings: list[SmsRouting] = []
    for phone in unique_phones:
        routing = await resolve_sms_routing(phone, client, conversation_id)
        if isinstance(routing, str):
            return routing
        routings.append(routing)

    all_internal = all(r.is_internal for r in routings)
    has_external = any(not r.is_internal for r in routings)
    return GroupSmsRouting(
        routings=routings,
        phones=unique_phones,
        all_internal=all_internal,
        has_external=has_external,
        is_mixed=has_external and any(r.is_internal for r in routings),
        from_phone_id=QUO_SERNIA_AI_PHONE_ID if all_internal else QUO_SHARED_EXTERNAL_PHONE_ID,
        line_name="Sernia AI" if all_internal else "Sernia Capital Team",
    )


async def execute_sms(
    client: httpx.AsyncClient,
    phone: str | list[str],
    message: str,
    from_phone_id: str,
    line_name: str,
    conversation_id: str = "",
    tool_name: str = "send_sms",
) -> str:
    """Send an SMS via Quo API, auto-splitting if over SMS_SPLIT_THRESHOLD.

    ``phone`` may be a single number (1:1 thread) or a list of numbers —
    the Quo API supports up to ``GROUP_SMS_MAX_RECIPIENTS`` recipients per
    message, which land in one shared group thread.

    Public API — used by send_sms tool and scheduling executor.
    Delegates to ``_send_sms`` which handles chunking.
    """
    return await _send_sms(
        client,
        tool_name,
        phone,
        message,
        from_phone_id,
        line_name,
        conversation_id,
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


# Threads whose most recent activity is older than this are flagged as stale in
# ``list_active_threads_impl``. Quo only auto-archives a thread when someone
# marks it "done" (snoozed 100+ years); an un-actioned thread stays "active"
# forever. Without an explicit age signal the agent has read a year-old message
# as a fresh report — see the ``_relative_age`` docstring.
STALE_THREAD_DAYS = 30


def _parse_iso(created: str | None) -> datetime | None:
    """Parse a Quo ISO-8601 timestamp to an aware UTC datetime, or None."""
    if not created or not isinstance(created, str):
        return None
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def _relative_age(created: str | None, now: datetime | None = None) -> str:
    """Human-readable age of an ISO-8601 timestamp, e.g. "3 hours ago",
    "2 days ago", "1 year ago". Returns "" when ``created`` can't be parsed.

    Every activity timestamp the Quo read tools render is annotated with this
    so the agent never has to diff raw ISO dates against "today" in its head.
    That date math is exactly where it has failed before: a message dated
    ``2025-07-23`` that says "leak this morning", surfaced on ``2026-07-23``
    (an *exact* one-year collision), got logged as a brand-new maintenance
    report. A literal "1 year ago" on the line removes that ambiguity.
    """
    ts = _parse_iso(created)
    if ts is None:
        return ""
    now = now or datetime.now(UTC)
    secs = (now - ts).total_seconds()
    if secs < -60:
        return "in the future"
    minutes, hours = secs / 60, secs / 3600
    days = hours / 24
    if secs < 90:
        return "just now"
    if minutes < 90:
        return f"{round(minutes)} minutes ago"
    if hours < 36:
        return f"{round(hours)} hours ago"
    if days < 45:
        return f"{round(days)} days ago"
    if days < 365:
        return f"{round(days / 30)} months ago"
    years = round(days / 365)
    return f"{years} year{'s' if years != 1 else ''} ago"


def _ts(created: str | None, now: datetime | None = None) -> str:
    """Render a timestamp with its relative age for a thread/activity line:
    ``2025-07-23T08:58:00Z · 1 year ago``. Falls back to the raw value (or
    ``?``) when the timestamp is missing or unparseable."""
    label = created or "?"
    age = _relative_age(created, now)
    return f"{label} · {age}" if age else label


async def search_contacts_impl(
    client: httpx.AsyncClient,
    query: str,
) -> str:
    """Core implementation of contact search (no RunContext dependency)."""
    contacts = await get_all_contacts(client)
    return fuzzy_filter_json(contacts, query, top_n=5)


def _format_call_snippet(call: dict) -> str:
    """One-line snippet for a call activity, including the call ID so the agent
    can pass it to ``get_call_details`` for the summary + transcript."""
    direction = call.get("direction") or "?"
    duration = call.get("duration")
    status = call.get("status") or ""
    dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
    bits = [direction, dur_str]
    if status and status != "completed":
        bits.append(status)
    return f"Call ({', '.join(bits)}) — Call ID {call.get('id', '?')}"


async def _fetch_latest_message(
    client: httpx.AsyncClient,
    phone: str,
) -> dict | None:
    """Fetch the most recent SMS for a phone on the shared team line."""
    try:
        resp = await client.get(
            "/v1/messages",
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone,
                "maxResults": "1",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    msgs = resp.json().get("data", [])
    return msgs[0] if msgs else None


async def _fetch_latest_call(
    client: httpx.AsyncClient,
    phone: str,
) -> dict | None:
    """Fetch the most recent call for a phone on the shared team line."""
    try:
        resp = await client.get(
            "/v1/calls",
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone,
                "maxResults": "1",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    calls = resp.json().get("data", [])
    return calls[0] if calls else None


async def _fetch_message_by_id(
    client: httpx.AsyncClient,
    activity_id: str,
) -> dict | None:
    # Suppress instrumentation: this is a probe call that may return 404/400
    # when the ID is a call, not a message. Expected failures shouldn't log.
    with logfire.suppress_instrumentation():
        try:
            resp = await client.get(f"/v1/messages/{activity_id}")
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    return resp.json().get("data")


async def _fetch_call_by_id(
    client: httpx.AsyncClient,
    activity_id: str,
) -> dict | None:
    # Suppress instrumentation: this is a probe call that may return 404/400
    # when the ID is a message, not a call. Expected failures shouldn't log.
    with logfire.suppress_instrumentation():
        try:
            resp = await client.get(f"/v1/calls/{activity_id}")
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    return resp.json().get("data")


async def _fetch_activity_by_id(
    client: httpx.AsyncClient,
    activity_id: str,
) -> dict | None:
    """Fetch a Quo activity (message or call) by its ``AC...`` ID.

    Both share the same ID prefix and the conversation object exposes only
    ``lastActivityId`` (no type marker), so we probe both endpoints in
    parallel and return whichever succeeds. Group threads can ONLY be read
    this way — OpenPhone's ``/v1/messages?participants[]=…`` silently filters
    to 1:1 threads regardless of how many participants are passed.
    """
    import asyncio

    msg, call = await asyncio.gather(
        _fetch_message_by_id(client, activity_id),
        _fetch_call_by_id(client, activity_id),
    )
    if call is not None:
        # Calls have a ``duration`` field; messages don't. Be defensive in
        # case both endpoints accidentally accept the same ID.
        return call | {"_kind": "call"}
    if msg is not None:
        return msg | {"_kind": "message"}
    return None


# ---------------------------------------------------------------------------
# Group thread workaround — read from open_phone_events webhook table
# ---------------------------------------------------------------------------
# OpenPhone's public API silently filters /v1/messages?participants[]=… to 1:1
# threads — group thread history is unreachable. The local ``open_phone_events``
# table (populated by webhooks) DOES capture group msgs with a comma-separated
# ``to_number`` and the right ``conversation_id``. We use it ONLY for group
# threads; 1:1 threads still go straight to the public API. Easy to rip out
# when OpenPhone exposes a ``conversationId`` filter or per-conv messages
# endpoint.


async def _fetch_group_thread_from_events_table(
    conv_id: str,
    max_results: int,
) -> list[dict]:
    """Pull group-thread activities for a conversation from our local
    ``open_phone_events`` table (populated by OpenPhone webhooks).

    Returns a list of activity dicts with the same shape used by the rest of
    this module (``_kind`` ∈ {"message", "call"}, ``createdAt``, ``text``,
    ``from``, ``to``, ``direction``, ``id``). Items are oldest → newest, with
    duplicate webhook deliveries deduped by underlying message/call ID.
    """
    from sqlalchemy import select

    from api.src.database.database import AsyncSessionFactory
    from api.src.open_phone.models import OpenPhoneEvent

    async with AsyncSessionFactory() as session:
        rows = (
            (
                await session.execute(
                    select(OpenPhoneEvent)
                    .where(OpenPhoneEvent.conversation_id == conv_id)
                    .where(
                        OpenPhoneEvent.event_type.in_(
                            [
                                "message.received",
                                "message.delivered",
                                "call.completed",
                            ]
                        )
                    )
                    .order_by(OpenPhoneEvent.event_timestamp.asc())
                    # Over-fetch: webhooks deliver duplicates and a single call/msg
                    # often lands as multiple rows. We dedup below.
                    .limit(max_results * 4)
                )
            )
            .scalars()
            .all()
        )

    seen: set[str] = set()
    activities: list[dict] = []
    for r in rows:
        obj = (r.event_data or {}).get("data") or {}
        obj = obj.get("object") or {}
        msg_id = obj.get("id") or r.event_id
        if msg_id in seen:
            continue
        seen.add(msg_id)
        is_call = r.event_type.startswith("call.")
        to_phones = [p for p in (r.to_number or "").split(",") if p]
        activities.append(
            {
                "_kind": "call" if is_call else "message",
                "id": msg_id,
                "createdAt": r.event_timestamp.isoformat() if r.event_timestamp else "",
                "text": r.message_text,
                "from": r.from_number,
                "to": to_phones,
                "direction": obj.get("direction"),
                "duration": obj.get("duration"),
                "status": obj.get("status"),
            }
        )
    # Most recent N
    return activities[-max_results:]


def _render_group_thread_from_db(
    activities: list[dict],
    participants: list[str],
    phone_map: dict[str, str],
    now: datetime | None = None,
) -> str:
    """Format a sequence of group-thread activities (from the events table)
    as a single chronological thread, similar to ``_render_thread`` but
    aware that messages can have multiple recipients."""
    now = now or datetime.now(UTC)
    lines: list[str] = []
    msg_count = sum(1 for a in activities if a.get("_kind") == "message")
    call_count = sum(1 for a in activities if a.get("_kind") == "call")
    label = ", ".join(
        f"{phone_map.get(p, p)} ({p})" if phone_map.get(p, p) != p else p for p in participants
    )
    lines.append(
        f"Group thread with {label} — "
        f"{msg_count} message{'s' if msg_count != 1 else ''}, "
        f"{call_count} call{'s' if call_count != 1 else ''}\n"
    )

    for item in activities:
        created = _ts(item.get("createdAt"), now)
        if item.get("_kind") == "call":
            direction = item.get("direction") or "?"
            dur = item.get("duration")
            dur_str = f"{dur}s" if isinstance(dur, int) else "?s"
            lines.append(
                f"[{created}] CALL ({direction}, {dur_str}) — Call ID {item.get('id', '?')}"
            )
        else:
            sender_phone = item.get("from") or ""
            sender_name = phone_map.get(sender_phone, sender_phone) if sender_phone else "?"
            to_phones = item.get("to") or []
            to_names = ", ".join(
                phone_map.get(p, p) if phone_map.get(p, p) != p else p for p in to_phones
            )
            text = (item.get("text") or "(no text)")[:500]
            lines.append(f"[{created}] {sender_name} → {to_names}: {text}")

    return "\n".join(lines)


async def _find_group_conversation(
    client: httpx.AsyncClient,
    participants: list[str],
) -> dict | None:
    """Find the OpenPhone conversation whose participants exactly match
    the given set (regardless of ordering). Returns None if none found.
    Pages through up to 5 pages of conversations (~500) — enough for an
    active inbox of any realistic size.
    """
    target = frozenset(participants)
    page_token: str | None = None
    for _ in range(5):
        params: list[tuple[str, str]] = [
            ("phoneNumbers[]", QUO_SHARED_EXTERNAL_PHONE_ID),
            ("maxResults", "100"),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        try:
            resp = await client.get("/v1/conversations", params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        data = resp.json()
        for conv in data.get("data", []):
            if frozenset(conv.get("participants") or []) == target:
                return conv
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None


async def list_active_threads_impl(
    client: httpx.AsyncClient,
    max_results: int = 20,
    updated_after_days: int | None = None,
) -> str:
    """Core implementation of active threads listing (no RunContext dependency).

    Mimics the Quo active inbox: returns all non-done conversations, sorted by
    most recent activity.  An optional ``updated_after_days`` narrows the window.

    For each thread, the snippet line shows whichever activity is more recent —
    SMS or call. Call snippets include the call ID so the agent can pass it to
    ``get_call_details`` for the summary + transcript.
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
            cutoff = (datetime.now(UTC) - timedelta(days=updated_after_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
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

    # Build the snippet for each thread by picking whichever of the latest
    # message / latest call has the more recent ``createdAt``. Group threads
    # (>1 participant) require a different path: OpenPhone's
    # ``/v1/messages?participants[]=…`` filter silently narrows to 1:1 even
    # when both participants are passed, so per-participant fetches return
    # the wrong thread. Instead, follow the conversation's ``lastActivityId``
    # — that always points at the actual most-recent activity for the thread.
    import asyncio

    async def _fetch_snippet_1to1(phone: str) -> dict | None:
        msg, call = await asyncio.gather(
            _fetch_latest_message(client, phone),
            _fetch_latest_call(client, phone),
            return_exceptions=False,
        )
        if msg and call:
            return (
                msg | {"_kind": "message"}
                if msg.get("createdAt", "") >= call.get("createdAt", "")
                else call | {"_kind": "call"}
            )
        if call:
            return call | {"_kind": "call"}
        if msg:
            return msg | {"_kind": "message"}
        return None

    async def _fetch_snippet_for_conv(conv: dict) -> dict | None:
        participants = conv.get("participants") or []
        if len(participants) > 1:
            last_id = conv.get("lastActivityId")
            if last_id:
                return await _fetch_activity_by_id(client, last_id)
            return None
        if participants:
            return await _fetch_snippet_1to1(participants[0])
        return None

    snippet_results = await asyncio.gather(
        *(_fetch_snippet_for_conv(c) for c in conversations),
        return_exceptions=True,
    )
    # Index by conversation id so we don't lose group-thread snippets to
    # phone-key collisions when two conversations share a participant.
    snippet_map: dict[str, dict] = {}
    for conv, result in zip(conversations, snippet_results, strict=False):
        if isinstance(result, dict):
            snippet_map[conv.get("id", "")] = result

    now = datetime.now(UTC)
    lines: list[str] = []
    stale_count = 0
    for conv in conversations:
        participants = conv.get("participants", [])
        last_activity = conv.get("lastActivityAt", "?")
        conv_id = conv.get("id", "?")

        # A thread only leaves the Quo "active" set when someone marks it
        # "done"; nothing ages out on its own. Flag threads whose newest
        # activity is older than STALE_THREAD_DAYS so the agent doesn't treat
        # a long-dormant thread as something that needs attention right now.
        last_ts = _parse_iso(last_activity if last_activity != "?" else None)
        is_stale = last_ts is not None and (now - last_ts) > timedelta(days=STALE_THREAD_DAYS)
        if is_stale:
            stale_count += 1
        stale_prefix = "⚠️ STALE — " if is_stale else ""

        enriched = []
        for phone in participants:
            name = phone_map.get(phone, phone)
            enriched.append(f"{name} ({phone})" if name != phone else phone)

        snippet_line = ""
        latest = snippet_map.get(conv_id)
        if latest:
            if latest.get("_kind") == "call":
                snippet_line = f"\n  Snippet: {_format_call_snippet(latest)}"
            else:
                direction = latest.get("direction", "")
                text = latest.get("text") or latest.get("body") or ""
                if text:
                    preview = text[:80] + "..." if len(text) > 80 else text
                    if direction == "outgoing":
                        snippet_line = f"\n  Snippet: You: {preview}"
                    else:
                        sender_phone = latest.get("from_") or latest.get("from") or ""
                        sender_label = (
                            phone_map.get(sender_phone, sender_phone).split(" (")[0]
                            if sender_phone
                            else "Them"
                        )
                        snippet_line = f"\n  Snippet: {sender_label}: {preview}"

        lines.append(
            f"{stale_prefix}Thread: {', '.join(enriched)}{snippet_line}\n"
            f"  Last activity: {_ts(last_activity if last_activity != '?' else None, now)}\n"
            f"  Conversation ID: {conv_id}"
        )

    header = f"Active threads ({len(conversations)}):"
    if stale_count:
        header += (
            f"\n\n⚠️ {stale_count} of these thread{'s' if stale_count != 1 else ''} "
            f"had no activity in over {STALE_THREAD_DAYS} days (marked STALE below). "
            "Their newest message is old — do NOT treat it as a new report. Check the "
            "'Last activity' age before acting, and open the thread only to follow up "
            "on something genuinely unresolved."
        )
    return f"{header}\n\n" + "\n\n".join(lines)


async def _fetch_one_to_one_thread(
    client: httpx.AsyncClient,
    phone_number: str,
    max_results: int,
) -> tuple[list[dict], list[dict]] | str:
    """Fetch SMS + call list for a single phone (1:1 thread).

    Returns ``(messages, calls)`` on success, or an error string on failure.
    """
    import asyncio

    # OpenPhone rejects maxResults > 100 with a 400; clamp so any caller-chosen
    # value stays within the API's per-page limit.
    page_size = max(1, min(max_results, OPENPHONE_MAX_RESULTS_PER_PAGE))

    async def _fetch(path: str) -> dict:
        resp = await client.get(
            path,
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone_number,
                "maxResults": str(page_size),
            },
        )
        resp.raise_for_status()
        return resp.json()

    try:
        msg_data, call_data = await asyncio.gather(
            _fetch("/v1/messages"),
            _fetch("/v1/calls"),
        )
    except httpx.HTTPError as exc:
        return f"Error fetching thread for {phone_number}: {exc}"

    return msg_data.get("data", []), call_data.get("data", [])


def _render_thread(
    messages: list[dict],
    calls: list[dict],
    contact_name: str,
    phone_number: str,
    phone_map: dict[str, str],
    *,
    header_prefix: str = "Thread with",
    now: datetime | None = None,
) -> str:
    """Render a chronological thread (SMS + calls interleaved)."""
    now = now or datetime.now(UTC)
    items: list[tuple[str, dict]] = [("message", m) for m in messages] + [
        ("call", c) for c in calls
    ]
    items.sort(key=lambda kv: kv[1].get("createdAt", ""))

    lines: list[str] = [
        f"{header_prefix} {contact_name} ({phone_number}) — "
        f"{len(messages)} message{'s' if len(messages) != 1 else ''}, "
        f"{len(calls)} call{'s' if len(calls) != 1 else ''}\n"
    ]

    for kind, item in items:
        created = _ts(item.get("createdAt"), now)
        if kind == "call":
            direction = item.get("direction", "?")
            duration = item.get("duration")
            dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
            status = item.get("status") or ""
            status_str = f", {status}" if status and status != "completed" else ""
            arrow = (
                f"{contact_name} → Sernia Capital"
                if direction == "incoming"
                else f"Sernia Capital → {contact_name}"
            )
            lines.append(
                f"[{created}] CALL {arrow} ({direction}, {dur_str}{status_str}) "
                f"— Call ID {item.get('id', '?')}"
            )
        else:
            direction = item.get("direction", "?")
            text = item.get("text") or item.get("body") or "(no text)"
            sender_phone = item.get("from_") or item.get("from", "?")

            if direction == "outgoing":
                sender_name = "Sernia Capital"
                recipient_name = contact_name
            else:
                sender_name = (
                    phone_map.get(sender_phone, sender_phone)
                    if isinstance(sender_phone, str)
                    else "?"
                )
                recipient_name = "Sernia Capital"

            if len(text) > 500:
                text = text[:500] + "..."

            lines.append(f"[{created}] {sender_name} → {recipient_name}: {text}")

    return "\n".join(lines)


def _format_group_activity_line(
    item: dict,
    phone_map: dict[str, str],
    now: datetime | None = None,
) -> str:
    """Render a single group-thread activity (message or call) as one line."""
    created = _ts(item.get("createdAt"), now)
    if item.get("_kind") == "call":
        direction = item.get("direction", "?")
        duration = item.get("duration")
        dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
        return f"[{created}] CALL ({direction}, {dur_str}) — Call ID {item.get('id', '?')}"
    text = (item.get("text") or item.get("body") or "(no text)")[:500]
    sender_phone = item.get("from_") or item.get("from") or ""
    sender_name = (
        phone_map.get(sender_phone, sender_phone) if isinstance(sender_phone, str) else "?"
    )
    to_phones = item.get("to") or []
    to_names = ", ".join(phone_map.get(p, p) if phone_map.get(p, p) != p else p for p in to_phones)
    return f"[{created}] {sender_name} → {to_names}: {text}"


async def get_thread_messages_impl(
    client: httpx.AsyncClient,
    phone_number: str | list[str],
    max_results: int = 20,
) -> str:
    """Core implementation of thread retrieval (no RunContext dependency).

    Returns SMS messages **and calls** interleaved in chronological order.

    Accepts either a single phone (1:1 thread) or a list of phones (group
    thread). Group threads are an OpenPhone API limitation: the
    ``/v1/messages?participants[]=…`` filter silently narrows to 1:1 even
    when multiple participants are passed. So for group threads we surface
    the most recent group activity via the conversation's ``lastActivityId``
    and supplement with each participant's 1:1 history (clearly labeled).

    Call entries include the call ID — pass it to ``get_call_details`` for
    the summary + transcript.
    """
    import asyncio

    contacts = await get_all_contacts(client)
    phone_map = _build_phone_map(contacts)

    participants_in: list[str] = (
        [phone_number] if isinstance(phone_number, str) else list(phone_number)
    )
    if len(participants_in) == 0:
        return "No phone numbers provided."

    # Deduplicate + sort so that [A, B] and [B, A] produce byte-identical
    # output. The OpenPhone conversation lookup is already order-insensitive
    # (frozenset match), but the rendered labels and per-participant sections
    # would otherwise vary by input order.
    participants_in = sorted(set(participants_in))

    if len(participants_in) == 1:
        only_phone = participants_in[0]
        result = await _fetch_one_to_one_thread(client, only_phone, max_results)
        if isinstance(result, str):
            return result
        messages, calls = result
        if not messages and not calls:
            return f"No messages or calls found with {only_phone}."
        return _render_thread(
            messages,
            calls,
            phone_map.get(only_phone, only_phone),
            only_phone,
            phone_map,
        )

    # ---- Group thread path ----
    conv = await _find_group_conversation(client, participants_in)
    conv_id = "?"
    db_activities: list[dict] = []
    if conv is not None:
        conv_id = conv.get("id", "?")
        # PRIMARY: pull from our webhook-ingested events table — the only
        # path that yields full group-thread history.
        try:
            db_activities = await _fetch_group_thread_from_events_table(
                conv_id,
                max_results=max_results,
            )
        except Exception:
            logfire.exception(
                "group thread: db fetch failed, falling back to lastActivityId",
                conv_id=conv_id,
            )

    if db_activities:
        # Happy path: full group thread history from DB.
        return _render_group_thread_from_db(
            db_activities,
            participants_in,
            phone_map,
        )

    # Fallback path: DB empty for this conv (older than webhook ingestion,
    # or unconfigured env). Surface what we can — last activity via API +
    # per-participant 1:1 history.
    last_activity: dict | None = None
    if conv is not None:
        last_id = conv.get("lastActivityId")
        if last_id:
            last_activity = await _fetch_activity_by_id(client, last_id)

    per_participant = await asyncio.gather(
        *(_fetch_one_to_one_thread(client, p, max_results) for p in participants_in),
    )

    participant_labels = ", ".join(
        f"{phone_map.get(p, p)} ({p})" if phone_map.get(p, p) != p else p for p in participants_in
    )
    out: list[str] = [
        f"Group thread: {participant_labels}",
        f"Conversation ID: {conv_id}",
        "",
        "**Caveat:** This conversation has no entries in the local webhook "
        "events table, so we're falling back to OpenPhone's public API. "
        "OpenPhone's API does not expose group-thread message history by "
        "participant filter — only 1:1 messages can be listed. What follows "
        "is (1) the most recent group activity (via the conversation's "
        "lastActivityId), and (2) each participant's 1:1 thread for context. "
        "For full group-thread history, view it in the OpenPhone app.",
        "",
        "## Most recent group activity",
    ]
    if last_activity is not None:
        out.append(_format_group_activity_line(last_activity, phone_map))
    else:
        out.append("_(no recent group activity available)_")

    out.append("")
    for phone, result in zip(participants_in, per_participant, strict=False):
        contact_name = phone_map.get(phone, phone)
        out.append(f"## 1:1 thread with {contact_name}")
        if isinstance(result, str):
            out.append(result)
        else:
            messages, calls = result
            if not messages and not calls:
                out.append(f"_(no 1:1 messages or calls with {phone})_")
            else:
                out.append(
                    _render_thread(
                        messages,
                        calls,
                        contact_name,
                        phone,
                        phone_map,
                        header_prefix="1:1 with",
                    )
                )
        out.append("")

    return "\n".join(out).rstrip()


def _format_call_timestamp(seconds: float | int | None) -> str:
    """Render dialogue offsets as ``M:SS`` (or ``H:MM:SS`` for long calls)."""
    if not isinstance(seconds, (int, float)):
        return "?:??"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def get_call_details_impl(
    client: httpx.AsyncClient,
    call_id: str,
    transcript_max_chars: int = 4000,
) -> str:
    """Fetch a Quo call's summary AND transcript in one call, rendered as
    markdown. Pairs with the ``Call ID`` surfaced by ``list_active_sms_threads``
    and ``get_thread_messages``.

    Summary at the top, transcript below. Transcript is truncated at
    ``transcript_max_chars`` (default 4000 chars) — the caller can pass a
    larger value when they need the full text.
    """
    import asyncio

    async def _fetch_call() -> dict | None:
        # Suppress instrumentation: may return 404/400 for invalid call IDs
        # or when the agent passes a message ID by mistake.
        with logfire.suppress_instrumentation():
            try:
                resp = await client.get(f"/v1/calls/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

    async def _fetch_summary() -> dict | None:
        # Suppress instrumentation: summary may not exist yet (Quo generates
        # it async) — 404/400 is expected for recent or short calls.
        with logfire.suppress_instrumentation():
            try:
                resp = await client.get(f"/v1/call-summaries/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

    async def _fetch_transcript() -> dict | None:
        # Suppress instrumentation: transcript may not exist yet (Quo generates
        # it async) — 404/400 is expected for recent or short calls.
        with logfire.suppress_instrumentation():
            try:
                resp = await client.get(f"/v1/call-transcripts/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

    call, summary, transcript = await asyncio.gather(
        _fetch_call(),
        _fetch_summary(),
        _fetch_transcript(),
    )

    if call is None and summary is None and transcript is None:
        return f"No call found with ID {call_id} (or transcript/summary not yet ready)."

    # Build phone → name map for speaker attribution.
    try:
        contacts = await get_all_contacts(client)
        phone_map = _build_phone_map(contacts)
    except httpx.HTTPError:
        phone_map = {}

    parts: list[str] = [f"# Call {call_id}\n"]

    # --- Call metadata ---
    if call:
        meta_bits = []
        direction = call.get("direction")
        if direction:
            meta_bits.append(f"**Direction:** {direction}")
        duration = call.get("duration")
        if isinstance(duration, int):
            meta_bits.append(f"**Duration:** {duration}s")
        status = call.get("status")
        if status:
            meta_bits.append(f"**Status:** {status}")
        created = call.get("createdAt")
        if created:
            meta_bits.append(f"**Created:** {_ts(created)}")
        participants = call.get("participants") or []
        if participants:
            named = [
                f"{phone_map.get(p, p)} ({p})" if phone_map.get(p, p) != p else p
                for p in participants
            ]
            meta_bits.append(f"**Participants:** {', '.join(named)}")
        if meta_bits:
            parts.append("\n".join(meta_bits) + "\n")

    # --- Summary section ---
    parts.append("## Summary\n")
    if summary:
        bullets = summary.get("summary") or []
        if bullets:
            parts.append("\n".join(f"- {b}" for b in bullets))
        else:
            parts.append("_(no summary text available)_")

        next_steps = summary.get("nextSteps") or []
        if next_steps:
            parts.append("\n\n### Next Steps\n")
            parts.append("\n".join(f"- {s}" for s in next_steps))
        parts.append("\n")
    else:
        parts.append("_(summary not available — Quo may still be generating it)_\n")

    # --- Transcript section ---
    parts.append("## Transcript\n")
    if transcript:
        dialogue = transcript.get("dialogue") or []
        if not dialogue:
            parts.append("_(transcript empty)_")
        else:
            lines: list[str] = []
            running = 0
            truncated = False
            for turn in dialogue:
                ts = _format_call_timestamp(turn.get("start"))
                speaker_phone = turn.get("identifier") or "?"
                speaker_name = phone_map.get(speaker_phone, speaker_phone)
                team_tag = " (team)" if turn.get("userId") else ""
                content = (turn.get("content") or "").strip()
                line = f"[{ts}] {speaker_name}{team_tag}: {content}"
                if running + len(line) + 1 > transcript_max_chars:
                    truncated = True
                    break
                lines.append(line)
                running += len(line) + 1
            parts.append("\n".join(lines))
            if truncated:
                parts.append(
                    f"\n\n_(transcript truncated at {transcript_max_chars} chars; "
                    f"call ``get_call_details`` with a larger ``transcript_max_chars`` "
                    f"to see more)_"
                )
    else:
        parts.append("_(transcript not available — Quo may still be generating it)_")

    return "\n".join(parts)


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
            conv_id,
            clerk_user_id=None,
            session=session,
        )

        seed: list[ModelMessage] = [
            ModelRequest(
                parts=[
                    UserPromptPart(content=f"[Context — not visible to SMS recipient: {context}]"),
                ]
            ),
            ModelResponse(
                parts=[
                    TextPart(content=outbound_text),
                ]
            ),
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

    name: str = Field(
        default="Phone Number", description='Label, e.g. "Phone Number", "Work", "mobile".'
    )
    value: str = Field(description="Phone number in E.164 format, e.g. +14125551234.")


class Email(BaseModel):
    """An email entry on a Quo contact."""

    name: str = Field(default="Email", description='Label, e.g. "Email", "Work".')
    value: str = Field(description="Email address.")


class CustomField(BaseModel):
    """A custom field entry. Use getContactCustomFields_v1 to look up keys."""

    key: str = Field(description="The 24-char hex custom field key.")
    value: str | list[str] | None = Field(
        description="Value — string for text/date fields, list of strings for multi-select (e.g. Tags)."
    )


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
            pn.model_dump() if isinstance(pn, PhoneNumber) else pn for pn in phone_numbers
        ]
    if emails is not None:
        df["emails"] = [em.model_dump() if isinstance(em, Email) else em for em in emails]

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
    # Shared rate-limited transport keeps the agent's bursty parallel reads
    # (contact cache + per-conversation message/call fan-out) under OpenPhone's
    # per-key rate limit. This client also backs the FastMCP-bridged tools.
    from api.src.open_phone.rate_limit import build_rate_limited_transport

    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        logfire.warn("OPEN_PHONE_API_KEY not set — Quo tools will fail at runtime")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": api_key},
        timeout=30,
        transport=build_rate_limited_transport(),
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


async def _send_single_sms(
    client: httpx.AsyncClient,
    tool_name: str,
    recipients: list[str],
    message: str,
    from_phone_id: str,
    conversation_id: str,
) -> tuple[bool, str]:
    """Send one SMS via Quo API (1:1 or group). Returns (success, detail)."""
    payload = {"content": message, "from": from_phone_id, "to": recipients}
    logfire.info(
        "{tool_name} request",
        tool_name=tool_name,
        to=recipients,
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
            to=recipients,
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
    phone: str | list[str],
    message: str,
    from_phone_id: str,
    line_name: str,
    conversation_id: str,
) -> str:
    """Send an SMS via Quo API, auto-splitting if over SMS_SPLIT_THRESHOLD.

    ``phone`` may be a single number or a list — a list sends every chunk
    to all recipients in one shared group thread.
    """
    recipients = [phone] if isinstance(phone, str) else list(phone)
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
            client,
            tool_name,
            recipients,
            chunk,
            from_phone_id,
            conversation_id,
        )
        if not ok:
            part_info = f" (part {i + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            return f"{err}{part_info}"
    parts_note = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
    if len(recipients) > 1:
        return f"Group message sent to {', '.join(recipients)} from {line_name}.{parts_note}"
    return f"Message sent to {recipients[0]} from {line_name}.{parts_note}"


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
        to: str | list[str],
        message: str,
        context: str = "",
    ) -> str:
        """Send an SMS to one Quo contact, or a GROUP text to several.

        Automatically determines routing based on the recipient(s):
        - Internal (Sernia Capital LLC) → sends from AI direct line, no approval.
        - External (tenants, vendors) → sends from shared team number, requires approval.

        **Group texts**: pass a list of 2-10 phone numbers to start ONE shared
        thread — all recipients see the message and each other's replies (and
        each other's phone numbers). An all-internal group sends from the AI
        line with no approval; a group containing ANY external contact sends
        from the shared team number and requires approval. A mixed
        internal+external group exposes the internal members' numbers to the
        external recipients — only do this when that's clearly intended.
        To message multiple people SEPARATELY (private 1:1 threads), call
        this tool once per recipient instead.

        Max 1000 chars — messages over this limit are rejected (shorten/summarize
        and retry). Messages over 500 chars are auto-split into multiple texts.

        Args:
            to: Recipient phone number in E.164 format (e.g. "+14125551234"),
                or a list of 2-10 numbers for a group text (one shared thread).
            message: The text message body to send (max 1000 chars).
            context: Optional hidden context about why this message is being sent.
                Not included in the SMS — saved to each recipient's conversation
                history so the AI has context if they reply later.
        """
        logfire.info("send_sms called", to=to, message_length=len(message))

        # Gate: message length — carriers (e.g. AT&T) reject long messages.
        if len(message) > SMS_MAX_LENGTH:
            return (
                f"Blocked: message is {len(message)} chars (max {SMS_MAX_LENGTH}). "
                "Please shorten or summarize the message and try again."
            )

        # Normalize: a 1-element list is just a 1:1 send.
        if isinstance(to, list) and len(to) == 1:
            to = to[0]

        # ---- Group send path ----
        if isinstance(to, list):
            group = await resolve_group_sms_routing(to, client, ctx.deps.conversation_id)
            if isinstance(group, str):
                return group

            # Conditional approval: any external recipient requires HITL.
            if group.has_external and not ctx.tool_call_approved:
                raise ApprovalRequired()

            logfire.info(
                "send_sms sending group",
                to=group.phones,
                names=group.recipient_names,
                all_internal=group.all_internal,
                is_mixed=group.is_mixed,
                from_phone_id=group.from_phone_id,
            )

            send_result = await execute_sms(
                client,
                group.phones,
                message,
                group.from_phone_id,
                group.line_name,
                ctx.deps.conversation_id,
            )

            # Seed each recipient's AI SMS conversation with hidden context
            if context and "Failed" not in send_result:
                for phone in group.phones:
                    try:
                        await _seed_sms_conversation(phone, message, context)
                    except Exception:
                        logfire.exception("send_sms: failed to seed conversation", to=phone)

            if send_result.startswith("Group message sent"):
                named = ", ".join(
                    f"{name} ({phone})"
                    for name, phone in zip(group.recipient_names, group.phones, strict=False)
                )
                return f"Group message sent to {named} from {group.line_name} (one shared thread)."
            return send_result

        # ---- Single-recipient path ----
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
            client,
            to,
            message,
            routing.from_phone_id,
            routing.line_name,
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
        include_inactive: bool = False,
    ) -> str:
        """Send the same SMS to all CURRENT tenants in one or more properties.

        Automatically finds matching tenants from the contact list, groups
        by (Property, Unit #), and sends one SMS per unit group. Roommates
        in the same unit share ONE group text thread (they see each other's
        replies); different units are isolated from each other so tenants
        never see another unit's contact info.

        By default this targets only tenants with a **currently-active lease**
        (Lease Start Date <= today <= Lease End Date). Leads, prospects, and
        future / past tenants are excluded — they should never receive
        building-wide tenant notices. Set ``include_inactive=True`` only when
        you explicitly intend to reach non-active contacts too (e.g. texting
        incoming tenants whose lease hasn't started yet).

        Max 1000 chars — messages over this limit are rejected (shorten/summarize
        and retry). Messages over 500 chars are auto-split into multiple texts.

        Args:
            message: The text message body to send to all matching tenants (max 1000 chars).
            properties: Property names to target (e.g. ["320"] or ["320", "400"]).
            units: Optional unit filter within those properties. None = all units.
            include_inactive: When True, also message contacts without a
                currently-active lease (leads, future/past tenants). Defaults
                to False — active-lease tenants only.
        """
        logfire.info(
            "mass_text_tenants called",
            properties=properties,
            units=units,
            include_inactive=include_inactive,
            message_length=len(message),
        )

        # Gate: message length — carriers (e.g. AT&T) reject long messages.
        if len(message) > SMS_MAX_LENGTH:
            return (
                f"Blocked: message is {len(message)} chars (max {SMS_MAX_LENGTH}). "
                "Please shorten or summarize the message and try again."
            )

        contacts = await get_all_contacts(client)
        grouped = _filter_tenants_by_property_unit(
            contacts, properties, units, active_only=not include_inactive
        )

        if not grouped:
            unit_desc = f" units {units}" if units else ""
            lease_desc = "" if include_inactive else " with an active lease"
            return f"No tenants{lease_desc} found matching properties {properties}{unit_desc}."

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

            # Roommates in a unit share one group thread; a solo tenant gets a
            # 1:1 send. Units larger than the API's per-message recipient cap
            # (never expected in practice) fall back to individual sends.
            if 1 < len(phones) <= GROUP_SMS_MAX_RECIPIENTS:
                batches: list[str | list[str]] = [phones]
            else:
                batches = list(phones)

            unit_sent = 0
            for batch in batches:
                result = await execute_sms(
                    client,
                    batch,
                    message,
                    QUO_SHARED_EXTERNAL_PHONE_ID,
                    "Sernia Capital Team",
                    ctx.deps.conversation_id,
                    tool_name="mass_text_tenants",
                )
                if result.startswith(("Message sent", "Group message sent")):
                    unit_sent += len(batch) if isinstance(batch, list) else 1
                else:
                    batch_desc = ", ".join(batch) if isinstance(batch, list) else batch
                    failures.append(f"{prop} Unit {unit} ({batch_desc}): {result}")

            if unit_sent:
                recipient_desc = ", ".join(names)
                thread_note = " — one group thread" if unit_sent > 1 else ""
                results.append(
                    f"{prop} Unit {unit} ({unit_sent} recipient{'s' if unit_sent != 1 else ''}: {recipient_desc}{thread_note})"
                )

        parts: list[str] = []
        if results:
            parts.append(
                f"Sent {len(results)} message{'s' if len(results) != 1 else ''}: {'; '.join(results)}."
            )
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
        """List active conversation threads on the shared team number.

        Mirrors the Quo active inbox — returns all non-done threads, enriched
        with contact names and sorted by most recent activity. Each thread's
        snippet shows whichever activity is most recent: an SMS or a call.
        Call snippets include the Call ID — pass it to ``get_call_details`` to
        read the summary + transcript.

        Every "Last activity" timestamp is annotated with its age (e.g.
        ``· 3 days ago``). Threads with no activity for over 30 days are
        prefixed ``⚠️ STALE`` — a thread stays "active" until someone marks it
        done in Quo, so an old dormant thread can still appear here. Treat a
        STALE thread's newest message as history, NOT a new report.

        For recurring / scheduled inbox sweeps, pass ``updated_after_days``
        (e.g. 7) so you only see recently-active threads and never re-surface a
        year-old message as if it just came in.

        Args:
            max_results: Max threads to return (default 20).
            updated_after_days: Optional — only include threads updated within
                this many days. Omit for all active threads (matches Quo inbox);
                set it for scheduled checks to exclude dormant threads.
        """
        return await list_active_threads_impl(client, max_results, updated_after_days)

    # ------------------------------------------------------------------
    # get_thread_messages — enriched listMessages for a phone number
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def get_call_details(
        ctx: RunContext[SerniaDeps],
        call_id: str,
        transcript_max_chars: int = 4000,
    ) -> str:
        """Fetch a Quo call's summary AND transcript in one call, rendered as
        markdown (summary on top, transcript below). Use this with the Call ID
        surfaced by ``list_active_sms_threads`` or ``get_thread_messages``.

        Args:
            call_id: The Quo call ID (``AC...``).
            transcript_max_chars: Max characters of transcript dialogue to
                include (default 4000). Pass a larger value when you need the
                full transcript of a long call.
        """
        return await get_call_details_impl(client, call_id, transcript_max_chars)

    @custom_toolset.tool
    async def get_thread_messages(
        ctx: RunContext[SerniaDeps],
        phone_number: str | list[str],
        max_results: int = 20,
    ) -> str:
        """Get the recent thread (SMS + calls) with a specific phone number, OR
        a group thread by passing a list of phone numbers.

        Returns SMS messages and calls interleaved in chronological order with
        contact names enriched. Each line's timestamp is annotated with its age
        (e.g. ``· 1 year ago``) — always check it before acting: an old message
        is history, not a new report, even when its wording says "this morning".
        Call entries include the Call ID — pass it to ``get_call_details`` to
        read the call's summary + transcript.

        **Group threads** (multiple phones): full group-thread history is
        served from our local webhook events table when available. If the
        conversation predates webhook ingestion or the lookup fails, the tool
        falls back to OpenPhone's public API (which only exposes the
        conversation's most recent activity plus each participant's 1:1
        history) and includes a caveat block in the output so you can tell
        which path you got. Use ``list_active_sms_threads`` to discover the
        participant set for a group conversation.

        Args:
            phone_number: A single phone in E.164 (1:1 thread) OR a list of
                phones (group thread).
            max_results: Max items per type to return per participant
                (default 20 messages + 20 calls). Capped at 100 — OpenPhone's
                per-page limit; higher values are clamped to 100.
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
