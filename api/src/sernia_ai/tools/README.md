# Sernia AI — Tools

Agent tools for the Sernia AI assistant. Each module exposes a toolset consumed by the main agent.

## Quo (`quo_tools.py`)

SMS and contact management via the OpenPhone API, bridged through FastMCP.

### SMS Tools

| Tool | Approval | Description |
|------|----------|-------------|
| `send_sms` | Conditional | Unified SMS — auto-detects internal vs external. Internal (Sernia Capital LLC) → AI line, no approval. External → shared team number, requires HITL. Single phone number per call. Supports optional `context` param for reply context seeding. |
| `mass_text_tenants` | Yes (HITL) | Send the same message to all tenants in one or more properties, with optional unit filter. Auto-groups by unit and sends one SMS per unit. |

### Core SMS Logic (module-level, reused by scheduling)

| Function | Purpose |
|----------|---------|
| `SmsRouting` | Dataclass — resolved routing (contact, phone ID, line name, is_internal) |
| `resolve_sms_routing(phone, client)` | Resolves phone → contact, determines internal/external, selects phone ID |
| `execute_sms(client, phone, message, ...)` | Sends a single SMS via Quo API |

### Deterministic Gates

Every SMS goes through a chain of gates before sending:

1. **Message length** — Messages over 1000 chars are rejected at the tool level with feedback telling the LLM to shorten/summarize. This prevents carrier rejections (AT&T rejects around 670 chars).
2. **Contact resolution** — The recipient must exist as a Quo contact. Unknown numbers are blocked.
3. **Internal/external routing** — Internal contacts use `QUO_SERNIA_AI_PHONE_ID` (AI direct line); external contacts use `QUO_SHARED_EXTERNAL_PHONE_ID` (shared team number). This prevents exposing internal phone numbers in tenant threads.

### Auto-Splitting

Messages between 500–1000 chars are auto-split into multiple SMS at sentence/newline boundaries by `split_sms()` in `quo_tools.py`. The splitting logic tries to break at (in priority order): sentence-ending punctuation, newlines, spaces, then hard cut. This applies to all SMS paths: tool calls, AI SMS replies, and post-approval replies.

### Hidden Context Seeding

`send_sms` accepts an optional `context` parameter. This context is **not** included in the SMS — it's saved to the recipient's `ai_sms_from_{digits}` conversation in the DB as a `ModelRequest`/`ModelResponse` pair. When the recipient replies via SMS, the AI SMS event trigger loads this conversation history, giving the agent context about why the original message was sent.

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

## Scheduling (`scheduling_tools.py`)

One-time scheduled SMS and email delivery via APScheduler date trigger.

| Tool | Approval | Description |
|------|----------|-------------|
| `schedule_sms` | Conditional | Schedule an SMS for future delivery. Same routing/gating as `send_sms`. |
| `schedule_email` | Conditional | Schedule an email for future delivery. Same routing/gating as `send_email`. |
| `list_scheduled_messages` | No | List pending scheduled messages (filters APScheduler jobs by prefix). |
| `cancel_scheduled_message` | No | Cancel a pending scheduled message by job ID. |

### Architecture

- **Routing resolved at schedule time** — `resolve_sms_routing()` / `resolve_email_routing()` runs when the tool is called, not when the message sends. Phone ID, mailbox, and approval are determined up front.
- **Executor functions** (`_execute_scheduled_sms`, `_execute_scheduled_email`) run outside agent context at the scheduled time. They create fresh API clients/credentials.
- **Job IDs** use `scheduled_sms_` / `scheduled_email_` prefixes to distinguish from system jobs.
- **Timezone handling** — `send_at` is a naive datetime interpreted in the given `timezone` (default `America/New_York`).

## Other Tool Modules

| Module | Description |
|--------|-------------|
| `google_tools.py` | Gmail (search, read, send), Calendar (list, create), Drive (search, read docs/sheets/PDFs). Core email routing (`EmailRouting`, `resolve_email_routing`) exported for scheduling. |
| `clickup_tools.py` | Task management (list, search, create, update, delete) |
| `db_search_tools.py` | Search past agent conversations and SMS history |
| `code_tools.py` | Python sandbox (pydantic-monty) for math, formatting, data manipulation |
| `_logging.py` | Shared error logging wrapper for tool failures |
