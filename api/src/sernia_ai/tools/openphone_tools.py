"""
OpenPhone (Quo) tools — full API via FastMCP OpenAPI bridge + guarded send.

Fetches the public OpenPhone spec, patches known schema issues, trims
verbose descriptions to save tokens, and exposes a curated set of MCP tools
(messages, contacts, calls, recordings, transcripts, conversations).

The native ``sendMessage_v1`` and ``listContacts_v1`` are filtered out and
replaced by custom tools:

- ``send_message``: deterministic guards (contact must exist, correct from-phone).
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
# sendMessage_v1 → custom send_message; listContacts_v1 → custom search_contacts.
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
# Contact-lookup helper (used by send_message guard)
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

    # --- Custom tools (search_contacts + send_message) ---
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

    @custom_toolset.tool(requires_approval=True)
    async def send_message(
        ctx: RunContext[SerniaDeps],
        to: str,
        message: str,
    ) -> str:
        """Send an SMS/MMS message via Quo (OpenPhone).

        Before sending, the system verifies the recipient exists as a Quo
        contact and automatically selects the correct sending phone line.

        Args:
            to: Recipient phone number in E.164 format (e.g. +14125551234).
            message: The text message body to send.
        """
        logfire.info("send_message called", to=to, message_length=len(message))

        # Guard 1: recipient must be a Quo contact.
        try:
            contact = await _find_contact_by_phone(client, to)
        except httpx.HTTPStatusError as exc:
            log_tool_error("send_message", exc, conversation_id=ctx.deps.conversation_id)
            return f"Error looking up contact: HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            log_tool_error("send_message", exc, conversation_id=ctx.deps.conversation_id)
            return f"Error looking up contact: {exc}"

        if contact is None:
            logfire.warn("send_message blocked: recipient not in Quo", to=to)
            return (
                f"Blocked: {to} is not a Quo contact. "
                "Messages can only be sent to numbers stored in Quo."
            )

        # Guard 2: pick the correct from-phone based on company.
        company = contact.get("defaultFields", {}).get("company") or ""
        is_internal = company == QUO_INTERNAL_COMPANY
        from_phone_id = QUO_SERNIA_AI_PHONE_ID if is_internal else QUO_SHARED_EXTERNAL_PHONE_ID

        first = contact.get("defaultFields", {}).get("firstName") or ""
        last = contact.get("defaultFields", {}).get("lastName") or ""
        contact_name = f"{first} {last}".strip() or to
        line_name = "Sernia AI" if is_internal else "Sernia Capital Team"

        logfire.info(
            "send_message sending",
            to=to,
            contact_name=contact_name,
            company=company,
            from_phone_id=from_phone_id,
            line_name=line_name,
        )

        # Send via OpenPhone API.
        try:
            resp = await client.post(
                "/v1/messages",
                json={"content": message, "from": from_phone_id, "to": [to]},
            )
        except httpx.HTTPError as exc:
            log_tool_error("send_message", exc, conversation_id=ctx.deps.conversation_id)
            return f"Error sending message: {exc}"

        if resp.status_code in (200, 201, 202):
            logfire.info("send_message success", to=to, contact_name=contact_name)
            return f"Message sent to {contact_name} from {line_name}."

        logfire.error(
            "sernia tool error: {tool_name}",
            tool_name="send_message",
            status=resp.status_code,
            body=resp.text[:500],
            conversation_id=ctx.deps.conversation_id,
            slack_alert=True,
        )
        return f"Failed to send message (HTTP {resp.status_code}): {resp.text}"

    return CombinedToolset(toolsets=[mcp_toolset, custom_toolset])


quo_toolset = _build_quo_toolset()
