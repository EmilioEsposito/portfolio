# Sernia AI — Tools

Agent tools for the Sernia AI assistant. Each module exposes a toolset consumed by the main agent.

## Quo (`quo_tools.py`)

SMS and contact management via the OpenPhone API, bridged through FastMCP.

### SMS Tools

| Tool | Approval | Description |
|------|----------|-------------|
| `send_sms` | Conditional | Unified SMS — auto-detects internal vs external. Internal (Sernia Capital LLC) → AI line, no approval. External → shared team number, requires HITL. Takes a single phone number **or a list of 2–10 numbers for a group text** (one shared thread — see _Group Texts_ below). Supports optional `context` param for reply context seeding (seeds every recipient on group sends). |
| `mass_text_tenants` | Yes (HITL) | Send the same message to all **current** tenants in one or more properties, with optional unit filter. Defaults to active-lease tenants only (excludes leads + future/past tenants); pass `include_inactive=True` to override. Auto-groups by unit — roommates in a unit share **one group text thread**; different units are isolated. |

### Core SMS Logic (module-level, reused by scheduling)

| Function | Purpose |
|----------|---------|
| `SmsRouting` | Dataclass — resolved routing (contact, phone ID, line name, is_internal) |
| `resolve_sms_routing(phone, client)` | Resolves phone → contact, determines internal/external, selects phone ID |
| `GroupSmsRouting` | Dataclass — resolved group routing (per-recipient routings, all_internal / has_external / is_mixed flags, phone ID, line name) |
| `resolve_group_sms_routing(phones, client)` | Resolves a recipient list for a group text: dedupes, enforces 2–10 unique recipients, blocks non-contacts, selects the line (all-internal → AI line; any external → shared team number) |
| `execute_sms(client, phone, message, ...)` | Sends an SMS via Quo API — `phone` may be a single number or a list (group text, one shared thread) |

### Deterministic Gates

Every SMS goes through a chain of gates before sending:

1. **Message length** — Messages over 1000 chars are rejected at the tool level with feedback telling the LLM to shorten/summarize. This prevents carrier rejections (AT&T rejects around 670 chars).
2. **Contact resolution** — Every recipient must exist as a Quo contact. Unknown numbers are blocked (a group send is blocked entirely if any member is unknown).
3. **Internal/external routing** — Internal contacts use `QUO_SERNIA_AI_PHONE_ID` (AI direct line); external contacts use `QUO_SHARED_EXTERNAL_PHONE_ID` (shared team number). This prevents exposing internal phone numbers in tenant threads.
4. **Group size** — Group texts are capped at `GROUP_SMS_MAX_RECIPIENTS` (10) unique recipients, matching the Quo API's `to` array limit on `POST /v1/messages`.

### Group Texts (sending)

The Quo API supports up to **10 recipients** per `POST /v1/messages` (`to` array, spec `maxItems: 10`) — the message lands in **one shared group thread** where all recipients see each other's numbers and replies. Verified live 2026-08-13 (HTTP 202, single `conversationId`, status `delivered`).

Routing for group sends (`resolve_group_sms_routing`):

- **All internal** → AI line, no approval (team group chat).
- **Any external** → shared team number + HITL approval.
- **Mixed internal + external** → allowed but flagged (`is_mixed`) — sending exposes the internal members' numbers to the external recipients, so the HITL approval is the safeguard. The agent is instructed to only do this deliberately.

To message multiple people *privately*, the agent calls `send_sms` once per recipient instead of passing a list.

> **Known limitation — replies to all-internal groups:** the AI SMS event trigger handles inbound messages on the AI line keyed by sender only (`from_number`), so a reply to an all-internal group text is loaded as the sender's 1:1 history and the AI's answer is sent back 1:1 — it does not land in the group thread. Propagating the webhook's `conversation_id`/participants through the trigger (history loading + reply send) is tracked as follow-up work. The agent is instructed to prefer 1:1 sends for conversational exchanges and reserve internal group texts for announcements.

The group-conversation lookup used by the read tools (`_find_group_conversation`) searches **both** lines — the shared team number and the AI line — since all-internal groups are created on the AI line.

### Auto-Splitting

Messages between 500–1000 chars are auto-split into multiple SMS at sentence/newline boundaries by `split_sms()` in `quo_tools.py`. The splitting logic tries to break at (in priority order): sentence-ending punctuation, newlines, spaces, then hard cut. This applies to all SMS paths: tool calls, AI SMS replies, and post-approval replies.

### Hidden Context Seeding

`send_sms` accepts an optional `context` parameter. This context is **not** included in the SMS — it's saved to the recipient's `ai_sms_from_{digits}` conversation in the DB as a `ModelRequest`/`ModelResponse` pair. When the recipient replies via SMS, the AI SMS event trigger loads this conversation history, giving the agent context about why the original message was sent.

Example: The agent texts Anna "Is the faucet fixed?" with `context="Emilio asked to follow up on maintenance ticket"`. When Anna replies, the agent sees the hidden context and knows to update Emilio.

### Mass-Texting Pattern (Per-Unit Sharding)

> **Note:** The Quo API now supports multiple recipients per `POST /v1/messages` (up to 10 — see _Group Texts_ above), so the per-unit sharding delivers on its privacy purpose: roommates in the same unit share **one group text thread**, while different units are isolated so tenants never see another unit's contact info. A solo tenant still gets a plain 1:1 send; a unit with more than 10 phones (never expected) falls back to individual sends.

**Use `mass_text_tenants`** for building-wide notices. It automatically:
1. Finds matching tenants from the cached contact list by `(Property, Unit #)`
2. Skips internal contacts and contacts without phone numbers
3. **Skips anyone without a currently-active lease** — leads, prospects, and
   future/past tenants are excluded by default (`Lease Start Date <= today <=
   Lease End Date`). This is the safe default for building-wide notices so we
   never text someone who isn't a current tenant. Pass `include_inactive=True`
   to reach non-active contacts on purpose.
4. Groups by unit and sends one group SMS per unit (1:1 for solo tenants)

> **Active-lease filtering** is enforced in `_filter_tenants_by_property_unit`
> via the `_has_active_lease()` helper, which reads the `Lease Start Date` /
> `Lease End Date` contact custom fields. A contact missing either date (or
> with unparseable dates) is treated as **not** active.

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

OpenPhone supports multi-participant conversations (e.g. two roommates sharing one Quo thread), and **sending** into a group thread works first-class via the `to` array on `POST /v1/messages` (see _Group Texts_ above). **Reading** is the limited side: the public API does **not** let you list a group thread's messages by participant: `/v1/messages?participants[]=A&participants[]=B` silently filters to the 1:1 conversation with the *first* participant, regardless of how many are passed.

> **Gotcha — send `participants`, not `participants[]`, from Python.** The bracketed form above is *curl* syntax, where the brackets go over the wire literally. httpx percent-encodes them to `participants%5B%5D`, which OpenPhone does not recognise and answers with a **400**. Every call site here uses the plain `participants` key; `test_triggers.py::TestFetchSmsThreadRequest` guards the one that regressed. The group conversation is real (it appears in `/v1/conversations`), and individual messages can be fetched by ID via `/v1/messages/{id}`, but you can't enumerate them.

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

## Emergency Escalation (`escalation_tools.py`)

| Tool | Approval | Description |
|------|----------|-------------|
| `trigger_escalation` | No | Trigger the Twilio Studio Flow escalation: an immediate phone call to the escalation contacts (Emilio + Peppino, resolved from the contacts DB) from the dedicated Twilio number that bypasses Do Not Disturb. Registered on the agent with the `emergency` prefix, so the model sees it as `emergency_trigger_escalation`. |

Deliberately **not** behind HITL approval — the tool exists to reach a human
immediately when nobody is watching, so an approval gate would defeat it. The
tool docstring carries strict use criteria (fires/floods/break-ins yes;
lockouts/outages/drips no), the message is capped at 500 chars, and every call
logs the conversation ID + message to Logfire. The underlying flow execution is
`trigger_twilio_escalation()` in `api/src/open_phone/escalate.py`, shared with
the automatic SMS-webhook escalation pipeline (`analyze_for_twilio_escalation`).

## Other Tool Modules

| Module | Description |
|--------|-------------|
| `google_tools.py` | Gmail (search, read, send), Calendar (list, create), Drive (search, read docs/sheets/PDFs). Core email routing (`EmailRouting`, `resolve_email_routing`) exported for scheduling. Email tools include: Zillow email boilerplate cleanup (`_clean_zillow_email`), and LLM summarization fallback (`_summarize_if_long`) that replaces hard truncation with Haiku-based summarization. Each email/thread message includes its Message ID for daisy-chaining with `send_email`'s `reply_to_message_id`. |
| `clickup_tools.py` | Task management (list, search, create, update, delete) |
| `db_search_tools.py` | Search past agent conversations and SMS history; chronological contact SMS history |
| `code_tools.py` | Python sandbox (pydantic-monty) for math, formatting, data manipulation |
| `_logging.py` | Shared error logging wrapper for tool failures. Sandbox file-tool errors log at warn (not error) so they don't page; `EditError` gets a "re-read the file, don't guess whitespace" hint, and repeated identical recoverable failures escalate with explicit STOP guidance. |

## Repeat-call loop breaker (`_logging.py`)

`ErrorLoggingToolset` counts *recoverable* tool failures per run, keyed by tool name + arguments. Once the same call has failed identically `REPEAT_ERROR_THRESHOLD` (2) times, the returned error string gains a "STOP — change approach" instruction with concrete recovery steps.

Why: a model that re-sends byte-identical arguments after an identical error learns nothing from the repeat, so it loops — and each attempt spends one of the run's ~50 model requests. On 2026-08-08 a scheduled check died with `UsageLimitExceeded` after seven `workspace_edit_file` EditErrors on `MEMORY.md`, four of them byte-for-byte identical. The first failure still returns the bare error (plus the EditError re-read hint when applicable), because state can legitimately change between calls; only the repeat escalates.

Counts live on `SerniaDeps.recoverable_tool_error_counts`, so they are scoped to one agent run — a module-level cache would leak across runs. Deps objects without the attribute simply never escalate.

The matching instruction lives in `instructions.py`: MEMORY.md's injected copy is a snapshot taken before the first tool call, so after the agent edits MEMORY.md the snapshot is stale — the agent is told to re-read the file rather than guess at whitespace.
