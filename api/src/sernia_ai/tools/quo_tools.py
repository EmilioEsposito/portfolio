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

- ``send_internal_sms``: deterministic gate — all recipients must be internal.
- ``send_external_sms``: deterministic gate — ALL recipients must be external (no internal numbers in external threads).
- ``search_contacts``: fuzzy search against a TTL-cached contact list (avoids
  dumping 50-item pages into the context window).
"""

import json
import os
import time

import httpx
from api.src.open_phone.service import (
    get_all_contacts,
    find_contact_by_phone,
    invalidate_contact_cache,
)
import re

import logfire
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from pydantic_ai import FunctionToolset, RunContext
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
    # Contacts (search is custom; keep write ops)
    "createContact_v1",
    "updateContactById_v1",
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
    # Shared SMS helpers
    # ------------------------------------------------------------------

    async def _resolve_recipient(
        tool_name: str,
        phone: str,
        conversation_id: str,
    ) -> dict | str:
        """Look up a phone number in Quo contacts.

        Returns a contact dict on success, or an error string if the number
        is not found or the API call fails.
        """
        try:
            contact = await find_contact_by_phone(phone, client)
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
        return contact

    def _contact_display_name(contact: dict, phone: str) -> str:
        first = contact.get("defaultFields", {}).get("firstName") or ""
        last = contact.get("defaultFields", {}).get("lastName") or ""
        return f"{first} {last}".strip() or phone

    async def _send_sms(
        tool_name: str,
        phone: str,
        message: str,
        from_phone_id: str,
        line_name: str,
        conversation_id: str,
    ) -> str:
        """Send a single SMS via Quo API and return a result string."""
        try:
            resp = await client.post(
                "/v1/messages",
                json={"content": message, "from": from_phone_id, "to": [phone]},
            )
        except httpx.HTTPError as exc:
            log_tool_error(tool_name, exc, conversation_id=conversation_id)
            return f"Error sending message: {exc}"

        if resp.status_code in (200, 201, 202):
            logfire.info(f"{tool_name} success", to=phone)
            return f"Message sent to {phone} from {line_name}."

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
        to: str,
        message: str,
        context: str = "",
    ) -> str:
        """Send an SMS to a Sernia Capital team member (internal only, no approval needed).

        The recipient must be a Sernia Capital LLC employee. If the recipient is
        external, the tool blocks and you must use send_external_sms instead.
        To message multiple people, call this tool once per recipient.

        Args:
            to: Recipient phone number in E.164 format (e.g. "+14125551234").
            message: The text message body to send.
            context: Optional hidden context about why this message is being sent.
                Not included in the SMS — saved to the recipient's conversation
                history so the AI has context if they reply later.
        """
        logfire.info("send_internal_sms called", to=to, message_length=len(message))

        # Gate: resolve recipient.
        result = await _resolve_recipient(
            "send_internal_sms", to, ctx.deps.conversation_id,
        )
        if isinstance(result, str):
            return result
        contact = result

        # Gate: must be internal.
        if not _is_internal_contact(contact):
            name = _contact_display_name(contact, to)
            company = contact.get("defaultFields", {}).get("company") or "(none)"
            logfire.warn(
                "send_internal_sms blocked: external recipient",
                to=to,
                company=company,
            )
            return (
                f"Blocked: {name} ({to}) is not a Sernia Capital LLC contact "
                f"(company={company!r}). Use send_external_sms for external recipients."
            )

        name = _contact_display_name(contact, to)
        logfire.info(
            "send_internal_sms sending",
            to=to,
            name=name,
            from_phone_id=QUO_SERNIA_AI_PHONE_ID,
        )

        send_result = await _send_sms(
            "send_internal_sms",
            to,
            message,
            QUO_SERNIA_AI_PHONE_ID,
            "Sernia AI",
            ctx.deps.conversation_id,
        )

        # Seed recipient's AI SMS conversation with hidden context
        if context and "Failed" not in send_result:
            try:
                await _seed_sms_conversation(to, message, context)
            except Exception:
                logfire.exception(
                    "send_internal_sms: failed to seed conversation",
                    to=to,
                )

        return send_result

    # ------------------------------------------------------------------
    # send_external_sms — requires HITL, external contacts
    # ------------------------------------------------------------------

    @custom_toolset.tool(requires_approval=True)
    async def send_external_sms(
        ctx: RunContext[SerniaDeps],
        to: str,
        message: str,
        context: str = "",
    ) -> str:
        """Send an SMS to an external contact (requires approval).

        The recipient must be external (not Sernia Capital LLC). If the
        recipient is internal, the tool blocks — use send_internal_sms instead.
        To message multiple people, call this tool once per recipient.

        Args:
            to: Recipient phone number in E.164 format (e.g. "+14125551234").
            message: The text message body to send.
            context: Optional hidden context about why this message is being sent.
                Not included in the SMS — saved to the recipient's conversation
                history so the AI has context if they reply later.
        """
        logfire.info("send_external_sms called", to=to, message_length=len(message))

        # Gate: resolve recipient.
        result = await _resolve_recipient(
            "send_external_sms", to, ctx.deps.conversation_id,
        )
        if isinstance(result, str):
            return result
        contact = result

        # Gate: NO internal contacts — protects internal phone numbers.
        if _is_internal_contact(contact):
            name = _contact_display_name(contact, to)
            logfire.warn(
                "send_external_sms blocked: internal recipient in external thread",
                to=to,
                name=name,
            )
            return (
                f"Blocked: {name} ({to}) is a Sernia Capital LLC contact. "
                "External messages must NOT include internal team members — "
                "this would expose internal phone numbers. "
                "Use send_internal_sms for internal recipients."
            )

        name = _contact_display_name(contact, to)
        logfire.info(
            "send_external_sms sending",
            to=to,
            name=name,
            from_phone_id=QUO_SHARED_EXTERNAL_PHONE_ID,
        )

        send_result = await _send_sms(
            "send_external_sms",
            to,
            message,
            QUO_SHARED_EXTERNAL_PHONE_ID,
            "Sernia Capital Team",
            ctx.deps.conversation_id,
        )

        # Seed recipient's AI SMS conversation with hidden context
        if context and "Failed" not in send_result:
            try:
                await _seed_sms_conversation(to, message, context)
            except Exception:
                logfire.exception(
                    "send_external_sms: failed to seed conversation",
                    to=to,
                )

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
                result = await _send_sms(
                    "mass_text_tenants",
                    phone,
                    message,
                    QUO_SHARED_EXTERNAL_PHONE_ID,
                    "Sernia Capital Team",
                    ctx.deps.conversation_id,
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

    return CombinedToolset(toolsets=[mcp_toolset, custom_toolset])


quo_toolset = _build_quo_toolset()
