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
- **list_active_sms_threads** — Mirrors the Quo active inbox. Each thread's snippet shows whichever activity is most recent — SMS or call. Call snippets surface the Call ID so the agent can chain to `get_call_details`. For multi-participant (group) conversations, the snippet is fetched via the conversation's `lastActivityId` rather than per-participant — see _Group Threads_ below.
- **get_thread_messages** — Returns SMS messages and calls interleaved chronologically. Accepts a single phone (1:1 thread) **or a list of phones (group thread)**. Call entries include the Call ID for `get_call_details` chaining.
- **get_call_details** — Fetches a Quo call's summary + transcript in one shot, rendered as markdown (`# Call <id>` → metadata → `## Summary` → `### Next Steps` → `## Transcript`). Speaker turns are attributed by phone→contact lookup, with a `(team)` tag when the speaker is on Sernia's side. Transcript truncates at `transcript_max_chars` (default 4000) and tells the caller how to extend.
- **update_contact** — Safe read-merge-write contact update. Fetches the full contact first, merges only the provided fields, then sends the complete payload to Quo. Works around Quo's PATCH bug that clears omitted fields. Requires HITL approval.
- **create_contact** — Create a new Quo contact. No approval required.
- **MCP-bridged tools** — `deleteContact_v1`, `getContactCustomFields_v1`, `listCalls_v1`, `getCallById_v1`. Contact deletes require HITL approval. The native `getCallSummary_v1` and `getCallTranscript_v1` tools are intentionally **not** kept — `get_call_details` subsumes them with a curated, lower-token output.

### Calls in Conversation Threads

Quo conversations contain both SMS messages and calls. The conversation object only exposes `lastActivityId` (an `AC...` ID that's indistinguishable between calls and messages by prefix), so the listing tools fetch from `/v1/messages` *and* `/v1/calls` in parallel and merge by `createdAt`. This guarantees:

- `list_active_sms_threads` snippets correctly reflect the latest activity even when it's a call (otherwise the call is invisible and the snippet falls back to a stale message).
- `get_thread_messages` shows the full picture of a thread, not just the texts.

Whenever a call appears in either tool's output, the Call ID (`AC...`) is included on the same line. Pass it to `get_call_details` to read the call's summary, next steps, and full transcript.

### Group Threads

OpenPhone supports multi-participant conversations (e.g. two roommates sharing one Quo thread), but the public API does **not** let you list a group thread's messages by participant: `/v1/messages?participants[]=A&participants[]=B` silently filters to the 1:1 conversation with the *first* participant, regardless of how many are passed. The group conversation is real (it appears in `/v1/conversations`), and individual messages can be fetched by ID via `/v1/messages/{id}`, but you can't enumerate them.

Workarounds in this codebase:

- **`list_active_sms_threads`**: when a conversation has more than one participant, the snippet is built from the conversation's `lastActivityId` (probing both `/v1/messages/{id}` and `/v1/calls/{id}` in parallel since both share the `AC...` prefix). This guarantees the inbox snippet reflects the actual most-recent group activity instead of falling back to a stale 1:1 thread.
- **`get_thread_messages`**: accepts `phone_number: str | list[str]`. When given a list, it locates the matching group conversation, surfaces the most-recent group activity via `lastActivityId`, and renders each participant's 1:1 history below for context. The output explicitly states the API limitation so the agent doesn't pretend it has the full group history.

For full group-thread history, the OpenPhone web/mobile UI is the source of truth.

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
| `google_tools.py` | Gmail (search, read, send), Calendar (list, create), Drive (search, read docs/sheets/PDFs). Core email routing (`EmailRouting`, `resolve_email_routing`) exported for scheduling. Email tools include: Zillow email boilerplate cleanup (`_clean_zillow_email`), and LLM summarization fallback (`_summarize_if_long`) that replaces hard truncation with Haiku-based summarization. Each email/thread message includes its Message ID for daisy-chaining with `send_email`'s `reply_to_message_id`. |
| `clickup_tools.py` | Task management (list, search, create, update, delete) |
| `db_search_tools.py` | Search past agent conversations and SMS history; chronological contact SMS history |
| `code_tools.py` | Python sandbox (pydantic-monty) for math, formatting, data manipulation |
| `_logging.py` | Shared error logging wrapper for tool failures |
