"""
OpenPhone (Quo) tools — full API via FastMCP OpenAPI bridge + guarded send.

Fetches the public OpenPhone spec, patches known schema issues, trims
verbose descriptions to save tokens, and exposes a curated set of MCP tools
(messages, contacts, calls, recordings, transcripts, conversations).

The native ``sendMessage_v1`` and ``listContacts_v1`` are filtered out and
replaced by custom tools:

- ``send_internal_sms``: deterministic gate — all recipients must be internal.
- ``send_external_sms``: deterministic gate — ALL recipients must be external (no internal numbers in external threads).
- ``search_contacts``: fuzzy search against a TTL-cached contact list (avoids
  dumping 50-item pages into the context window).
"""

import json
import os
import time

import httpx
import logfire
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.toolsets import CombinedToolset
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

from api.src.sernia_ai.config import (
    QUO_INTERNAL_COMPANY,
    QUO_SERNIA_AI_PHONE_ID,
    QUO_SHARED_EXTERNAL_PHONE_ID,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools._logging import log_tool_error
from api.src.utils.fuzzy_json import fuzzy_filter_json

OPENPHONE_SPEC_URL = (
    "https://openphone-public-api-prod.s3.us-west-2.amazonaws.com"
    "/public/openphone-public-api-v1-prod.json"
)

# MCP-generated tools that mutate data and require human approval.
_MCP_WRITE_TOOLS = frozenset({
    "createContact_v1",
    "updateContactById_v1",
    "deleteContact_v1",
})

# Tools to keep from the MCP toolset (the rest are filtered out to save tokens).
# sendMessage_v1 → custom send_internal_sms / send_external_sms; listContacts_v1 → custom search_contacts.
_KEEP_TOOLS = frozenset({
    # Messages (read-only — send is custom)
    "listMessages_v1",
    "getMessageById_v1",
    # Contacts (search is custom; keep detail + write ops)
    "getContactById_v1",
    "createContact_v1",
    "updateContactById_v1",
    "deleteContact_v1",
    "getContactCustomFields_v1",
    # Calls
    "listCalls_v1",
    "getCallById_v1",
    "getCallSummary_v1",
    "getCallTranscript_v1",
    # Conversations
    "listConversations_v1",
})


# ---------------------------------------------------------------------------
# OpenAPI spec helpers
# ---------------------------------------------------------------------------

def _fetch_and_patch_spec() -> dict:
    """Fetch the OpenPhone OpenAPI spec, patch schema issues, and trim for tokens."""
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

    # Strip examples and verbose fields from the spec to reduce token usage.
    # These are documentation-only and don't affect API behavior.
    _strip_examples(spec)

    return spec


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
# TTL-cached contact store
# ---------------------------------------------------------------------------

_CONTACT_CACHE_TTL = 300  # 5 minutes
_contact_cache: list[dict] = []
_cache_ts: float = 0


async def _get_all_contacts(client: httpx.AsyncClient) -> list[dict]:
    """Return all Quo contacts, fetching from API at most once per TTL window."""
    global _contact_cache, _cache_ts

    if _contact_cache and (time.monotonic() - _cache_ts) < _CONTACT_CACHE_TTL:
        return _contact_cache

    contacts: list[dict] = []
    page_token: str | None = None
    while True:
        params: dict = {"maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get("/v1/contacts", params=params)
        resp.raise_for_status()
        data = resp.json()
        contacts.extend(data.get("data", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    _contact_cache = contacts
    _cache_ts = time.monotonic()
    logfire.info("contact cache refreshed", count=len(contacts))
    return contacts


def _invalidate_contact_cache() -> None:
    """Force a cache refresh on next access (e.g. after contact create/update)."""
    global _cache_ts
    _cache_ts = 0



# ---------------------------------------------------------------------------
# Contact-lookup helper (used by SMS tool guards)
# ---------------------------------------------------------------------------

async def _find_contact_by_phone(
    client: httpx.AsyncClient,
    phone: str,
) -> dict | None:
    """Look up a Quo contact by phone number using the cached contact list."""
    contacts = await _get_all_contacts(client)
    for contact in contacts:
        for pn in contact.get("defaultFields", {}).get("phoneNumbers", []):
            if pn.get("value") == phone:
                return contact
    return None


def _get_contact_unit(contact: dict) -> tuple[str, str] | None:
    """Extract (property, unit) from an OpenPhone contact's custom fields.

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
# Build the toolset
# ---------------------------------------------------------------------------

def _build_openphone_client() -> httpx.AsyncClient:
    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        logfire.warn("OPEN_PHONE_API_KEY not set — OpenPhone tools will fail at runtime")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": api_key},
        timeout=30,
    )


def _build_quo_toolset():
    spec = _fetch_and_patch_spec()
    client = _build_openphone_client()

    # --- MCP toolset (read ops + contact writes) ---
    mcp_server = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="openphone",
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
        contacts = await _get_all_contacts(client)
        return fuzzy_filter_json(contacts, query, top_n=5)

    # ------------------------------------------------------------------
    # Shared SMS helpers
    # ------------------------------------------------------------------

    async def _resolve_recipients(
        tool_name: str,
        to: list[str],
        conversation_id: str,
    ) -> list[dict] | str:
        """Look up each phone number in Quo contacts.

        Returns a list of contact dicts on success, or an error string if any
        number is not found or the API call fails.
        """
        contacts: list[dict] = []
        for phone in to:
            try:
                contact = await _find_contact_by_phone(client, phone)
            except httpx.HTTPStatusError as exc:
                log_tool_error(tool_name, exc, conversation_id=conversation_id)
                return f"Error looking up contact for {phone}: HTTP {exc.response.status_code}"
            except httpx.HTTPError as exc:
                log_tool_error(tool_name, exc, conversation_id=conversation_id)
                return f"Error looking up contact for {phone}: {exc}"

            if contact is None:
                logfire.warn(f"{tool_name} blocked: recipient not in Quo", to=phone)
                return (
                    f"Blocked: {phone} is not a Quo contact. "
                    "Messages can only be sent to numbers stored in Quo."
                )
            contacts.append(contact)
        return contacts

    def _contact_display_name(contact: dict, phone: str) -> str:
        first = contact.get("defaultFields", {}).get("firstName") or ""
        last = contact.get("defaultFields", {}).get("lastName") or ""
        return f"{first} {last}".strip() or phone

    async def _send_sms(
        tool_name: str,
        to: list[str],
        message: str,
        from_phone_id: str,
        line_name: str,
        conversation_id: str,
    ) -> str:
        """Send the SMS via OpenPhone API and return a result string."""
        try:
            resp = await client.post(
                "/v1/messages",
                json={"content": message, "from": from_phone_id, "to": to},
            )
        except httpx.HTTPError as exc:
            log_tool_error(tool_name, exc, conversation_id=conversation_id)
            return f"Error sending message: {exc}"

        if resp.status_code in (200, 201, 202):
            logfire.info(f"{tool_name} success", to=to)
            recipient_label = ", ".join(to) if len(to) > 1 else to[0]
            return f"Message sent to {recipient_label} from {line_name}."

        logfire.error(
            "sernia tool error: {tool_name}",
            tool_name=tool_name,
            status=resp.status_code,
            body=resp.text[:500],
            conversation_id=conversation_id,
        )
        return f"Failed to send message (HTTP {resp.status_code}): {resp.text}"

    # ------------------------------------------------------------------
    # send_internal_sms — no HITL, internal contacts only
    # ------------------------------------------------------------------

    @custom_toolset.tool
    async def send_internal_sms(
        ctx: RunContext[SerniaDeps],
        to: list[str],
        message: str,
    ) -> str:
        """Send an SMS to Sernia Capital team members (internal only, no approval needed).

        Use this tool ONLY when ALL recipients are Sernia Capital LLC employees.
        The system verifies every recipient is an internal contact. If any
        recipient is external, the tool blocks and you must use
        send_external_sms instead.

        Supports group texts — pass multiple phone numbers.

        Args:
            to: Recipient phone numbers in E.164 format (e.g. ["+14125551234"]).
            message: The text message body to send.
        """
        logfire.info("send_internal_sms called", to=to, message_length=len(message))

        # Gate: resolve all recipients.
        result = await _resolve_recipients(
            "send_internal_sms", to, ctx.deps.conversation_id,
        )
        if isinstance(result, str):
            return result
        contacts = result

        # Gate: ALL must be internal.
        for contact, phone in zip(contacts, to):
            if not _is_internal_contact(contact):
                name = _contact_display_name(contact, phone)
                company = contact.get("defaultFields", {}).get("company") or "(none)"
                logfire.warn(
                    "send_internal_sms blocked: external recipient",
                    to=phone,
                    company=company,
                )
                return (
                    f"Blocked: {name} ({phone}) is not a Sernia Capital LLC contact "
                    f"(company={company!r}). Use send_external_sms for external recipients."
                )

        names = [_contact_display_name(c, p) for c, p in zip(contacts, to)]
        logfire.info(
            "send_internal_sms sending",
            to=to,
            names=names,
            from_phone_id=QUO_SERNIA_AI_PHONE_ID,
        )

        return await _send_sms(
            "send_internal_sms",
            to,
            message,
            QUO_SERNIA_AI_PHONE_ID,
            "Sernia AI",
            ctx.deps.conversation_id,
        )

    # ------------------------------------------------------------------
    # send_external_sms — requires HITL, external contacts
    # ------------------------------------------------------------------

    @custom_toolset.tool(requires_approval=True)
    async def send_external_sms(
        ctx: RunContext[SerniaDeps],
        to: list[str],
        message: str,
    ) -> str:
        """Send an SMS to external contacts (requires approval).

        Use this tool when ALL recipients are external (not Sernia Capital LLC).
        The system rejects any message that includes a Sernia Capital LLC
        contact — internal numbers must never be exposed in external threads.
        Use send_internal_sms for internal-only messages.

        Supports group texts — pass multiple phone numbers. However, group
        texts to tenants in DIFFERENT units are blocked to prevent sharing
        contact info between unrelated tenants. Same-unit groups (e.g.
        roommates) are allowed.

        Args:
            to: Recipient phone numbers in E.164 format (e.g. ["+14125551234"]).
            message: The text message body to send.
        """
        logfire.info("send_external_sms called", to=to, message_length=len(message))

        # Gate: resolve all recipients.
        result = await _resolve_recipients(
            "send_external_sms", to, ctx.deps.conversation_id,
        )
        if isinstance(result, str):
            return result
        contacts = result

        # Gate: NO internal contacts allowed — protects internal phone numbers.
        for contact, phone in zip(contacts, to):
            if _is_internal_contact(contact):
                name = _contact_display_name(contact, phone)
                logfire.warn(
                    "send_external_sms blocked: internal recipient in external thread",
                    to=phone,
                    name=name,
                )
                return (
                    f"Blocked: {name} ({phone}) is a Sernia Capital LLC contact. "
                    "External messages must NOT include internal team members — "
                    "this would expose internal phone numbers. "
                    "Use send_internal_sms for internal recipients."
                )

        # Gate: recipients with unit info must all share the same unit.
        if len(contacts) > 1:
            units: dict[str, tuple[str, str]] = {}
            for contact, phone in zip(contacts, to):
                cu = _get_contact_unit(contact)
                if cu is not None:
                    units[phone] = cu

            if units:
                unique_units = set(units.values())
                if len(unique_units) > 1:
                    details = ", ".join(
                        f"{_contact_display_name(c, p)} → {units[p][0]} Unit {units[p][1]}"
                        for c, p in zip(contacts, to) if p in units
                    )
                    logfire.warn(
                        "send_external_sms blocked: cross-unit group text",
                        to=to, units=str(unique_units),
                    )
                    return (
                        f"Blocked: recipients span multiple units ({details}). "
                        "Tenants in different units must NEVER be on the same group SMS. "
                        "Send separate messages per unit."
                    )

        names = [_contact_display_name(c, p) for c, p in zip(contacts, to)]
        logfire.info(
            "send_external_sms sending",
            to=to,
            names=names,
            from_phone_id=QUO_SHARED_EXTERNAL_PHONE_ID,
        )

        return await _send_sms(
            "send_external_sms",
            to,
            message,
            QUO_SHARED_EXTERNAL_PHONE_ID,
            "Sernia Capital Team",
            ctx.deps.conversation_id,
        )

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

        Args:
            message: The text message body to send to all matching tenants.
            properties: Property names to target (e.g. ["320"] or ["320", "400"]).
            units: Optional unit filter within those properties. None = all units.
        """
        logfire.info(
            "mass_text_tenants called",
            properties=properties,
            units=units,
            message_length=len(message),
        )

        contacts = await _get_all_contacts(client)
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

            result = await _send_sms(
                "mass_text_tenants",
                phones,
                message,
                QUO_SHARED_EXTERNAL_PHONE_ID,
                "Sernia Capital Team",
                ctx.deps.conversation_id,
            )

            if result.startswith("Message sent"):
                recipient_desc = ", ".join(names)
                results.append(
                    f"{prop} Unit {unit} ({len(phones)} recipient{'s' if len(phones) != 1 else ''}: {recipient_desc})"
                )
            else:
                failures.append(f"{prop} Unit {unit}: {result}")

        parts: list[str] = []
        if results:
            parts.append(f"Sent {len(results)} message{'s' if len(results) != 1 else ''}: {'; '.join(results)}.")
        if failures:
            parts.append(f"Failed: {'; '.join(failures)}.")
        return " ".join(parts)

    return CombinedToolset(toolsets=[mcp_toolset, custom_toolset])


quo_toolset = _build_quo_toolset()
