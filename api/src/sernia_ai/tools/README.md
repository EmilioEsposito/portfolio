# Sernia AI — Tools

Agent tools for the Sernia AI assistant. Each module exposes a toolset consumed by the main agent.

## Quo (`quo_tools.py`)

SMS and contact management via the OpenPhone API, bridged through FastMCP.

### SMS Tools

| Tool | Line | Approval | Description |
|------|------|----------|-------------|
| `send_internal_sms` | Sernia AI | No | Team-only messages. Single phone number per call. Recipient must be a "Sernia Capital LLC" contact. Supports optional `context` param for reply context seeding. |
| `send_external_sms` | Shared External | Yes (HITL) | Tenant/vendor messages. Single phone number per call. Recipient must be an external contact. Supports optional `context` param for reply context seeding. |
| `mass_text_tenants` | Shared External | Yes (HITL) | Send the same message to all tenants in one or more properties, with optional unit filter. Auto-groups by unit and sends one SMS per unit. |

### Deterministic Gates

Every SMS goes through a chain of gates before sending:

1. **Message length** — Messages over 1000 chars are rejected at the tool level with feedback telling the LLM to shorten/summarize. This prevents carrier rejections (AT&T rejects around 670 chars).
2. **Contact resolution** — The recipient must exist as a Quo contact. Unknown numbers are blocked.
3. **Internal/external separation** — `send_internal_sms` blocks external contacts; `send_external_sms` blocks internal contacts. This prevents exposing internal phone numbers in tenant threads.

### Auto-Splitting

Messages between 500–1000 chars are auto-split into multiple SMS at sentence/newline boundaries by `split_sms()` in `config.py`. The splitting logic tries to break at (in priority order): sentence-ending punctuation, newlines, spaces, then hard cut. This applies to all SMS paths: tool calls, AI SMS replies, and post-approval replies.

### Hidden Context Seeding

Both `send_internal_sms` and `send_external_sms` accept an optional `context` parameter. This context is **not** included in the SMS — it's saved to the recipient's `ai_sms_from_{digits}` conversation in the DB as a `ModelRequest`/`ModelResponse` pair. When the recipient replies via SMS, the AI SMS event trigger loads this conversation history, giving the agent context about why the original message was sent.

Example: The agent texts Anna "Is the faucet fixed?" with `context="Emilio asked to follow up on maintenance ticket"`. When Anna replies, the agent sees the hidden context and knows to update Emilio.

### Mass-Texting Pattern (Per-Unit Sharding)

> **Note:** The Quo (OpenPhone) API currently only supports 1 recipient per `POST /v1/messages` call. `mass_text_tenants` loops internally to send one SMS per unit. If the API expands to support multiple recipients in the future, the per-unit sharding logic (grouping by `(Property, Unit #)`) would become relevant for privacy — roommates in the same unit could share a group text, while different units must be isolated to prevent sharing contact info.

**Use `mass_text_tenants`** for building-wide notices. It automatically:
1. Finds matching tenants from the cached contact list by `(Property, Unit #)`
2. Skips internal contacts and contacts without phone numbers
3. Groups by unit and sends one SMS per unit group

### Contact Custom Fields

Tenant contacts store unit info in `customFields`:
```json
[
  {"name": "Property", "value": "320"},
  {"name": "Unit #", "value": "02"}
]
```

The `_get_contact_unit()` helper extracts this as a `(property, unit)` tuple, returning `None` for non-tenant contacts.

### Other Tools

- **search_contacts** — Fuzzy search by name, phone, or company against a TTL-cached (5 min) contact list.
- **MCP-bridged tools** — `createContact_v1`, `updateContactById_v1`, `deleteContact_v1`, `getContactCustomFields_v1`, `listCalls_v1`, `getCallById_v1`, `getCallSummary_v1`, `getCallTranscript_v1`. Contact writes require HITL approval.

## Other Tool Modules

| Module | Description |
|--------|-------------|
| `google_tools.py` | Gmail (search, read, send), Calendar (list, create), Drive (search, read docs/sheets/PDFs) |
| `clickup_tools.py` | Task management (list, search, create, update, delete) |
| `db_search_tools.py` | Search past agent conversations and SMS history |
| `code_tools.py` | Python sandbox (pydantic-monty) for math, formatting, data manipulation |
| `_logging.py` | Shared error logging wrapper for tool failures |
