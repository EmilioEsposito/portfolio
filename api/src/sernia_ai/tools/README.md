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

General SMS tools go through a chain of gates before sending (fixed templates
have their separate eligibility policy documented below):

1. **Message length** — Messages over 1000 chars are rejected at the tool level with feedback telling the LLM to shorten/summarize. This prevents carrier rejections (AT&T rejects around 670 chars).
2. **Contact resolution** — Every recipient must exist as a Quo contact. Unknown numbers are blocked (a group send is blocked entirely if any member is unknown).
3. **Internal/external routing** — Internal contacts use `QUO_SERNIA_AI_PHONE_ID` (AI direct line); external contacts use `QUO_SHARED_EXTERNAL_PHONE_ID` (shared team number). This prevents exposing internal phone numbers in tenant threads.
4. **Group size** — Group texts are capped at `GROUP_SMS_MAX_RECIPIENTS` (10) unique recipients, matching the Quo API's `to` array limit on `POST /v1/messages`.
5. **Unit isolation (group sends)** — Tenants from **different units** can never share a group thread: `resolve_group_sms_routing` reads each external recipient's `Property` / `Unit #` custom fields and hard-blocks the send when more than one distinct unit appears in the group. This gate runs **before** the approval gate, so a human approval cannot override it. Roommates in the same unit are fine; contacts without unit fields (vendors, team) don't trigger it.

### Group Texts (sending)

The Quo API supports up to **10 recipients** per `POST /v1/messages` (`to` array, spec `maxItems: 10`) — the message lands in **one shared group thread** where all recipients see each other's numbers and replies. Verified live 2026-08-13 (HTTP 202, single `conversationId`, status `delivered`).

Routing for group sends (`resolve_group_sms_routing`):

- **All internal** → AI line, no approval (team group chat). Replies are handled **in the group thread** by the AI SMS trigger — see [`triggers/README.md`](../triggers/README.md).
- **All external** → shared team number + HITL approval.
- **Mixed internal + external** → hard-blocked deterministically — a mixed group would expose internal team numbers to external recipients. Approval cannot override; a group is always all-internal or all-external.
- **Cross-unit tenants** → hard-blocked deterministically (gate 5 above); approval cannot override.

The same group support (same tool name, same gates) is mirrored in the standalone MCP server at `apps/sernia_mcp` — `quo_send_sms` accepts `str | list[str]` with the group approval card, backed by `resolve_group_sms_routing_core` / `send_group_sms_core`.

To message multiple people *privately*, the agent calls `send_sms` once per recipient instead of passing a list.

> **Group replies stay in the group:** the AI SMS event trigger detects group messages on the AI line (the webhook's `to` contains recipients besides the AI's own number), keys the conversation to the Quo group conversation (`ai_sms_group_{CN...}`), and sends the AI's answer to **all** human participants so it lands in the same group thread. Context seeding for group sends (`send_sms`'s `context` param) also lands in that group conversation. See [`triggers/README.md`](../triggers/README.md) for the flow.

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

Quo added group message listing on June 18, 2026. Send repeated **unbracketed**
`participants` keys (`participants=A&participants=B`) and the owning `phoneNumberId`.
Every returned `conversationId` must match the selected group; mismatches are rejected.
Both the agent and standalone MCP reader use this API. The agent falls back to webhook
history during API outages/indexing delays; partial last-activity/1:1 context is labeled.

Conversation listing likewise requires `phoneNumbers`, **not** `phoneNumbers[]`.
Live verification on September 10 found that Quo silently ignores the bracketed filter
and returns conversations across unrelated lines. Fetch the bounded page set before
sorting by last activity; results are not reliably ordered by recent activity.
A capped scan is explicitly labeled partial, so it cannot justify an all-clear.

References: [message listing](https://www.quo.com/docs/mdx/api-reference/messages/list-messages),
[API changelog](https://www.quo.com/docs/mdx/api-reference/changelog).

### Automatic SMS replies

The SMS trigger owns delivery to the current thread. It supplies `sms_reply_recipients`
on the run dependencies; `send_sms` refuses an extra send to that same recipient set
and directs the model to put its reply in the final response. Approval resumptions
restore those recipients too. Sending to a different thread retains ordinary routing
and approval requirements. This prevents the observed tool-send plus final-answer
send duplication; it is not a general provider idempotency ledger.

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
| `code_tools.py` | `run_python`: Python sandbox (pydantic-monty 1.x) for math, formatting, data manipulation. See [Python sandbox](#python-sandbox-code_toolspy). |
| `workspace_tools.py` | Workspace file tools (`workspace_read_file`, `_write_file`, `_edit_file`, `_list_directory`, `_find_files`, `_search_files`, `_delete_file`). The harness `FileSystem` tools behind an adapter: `/workspace/...` and relative paths both work, only `.md`/`.txt`/`.json` files, `.git` denied, parent directories created on write, and recoverable errors raised as `WorkspaceToolError` instead of tool retries. Contributed by the `SerniaWorkspace` capability, which also makes each run's `deps.workspace_path` its workspace; the harness refuses its file tools from plain `toolsets=`. |
| `rental_listing_tools.py` | `rental_listings_read`, `rental_listing_update`, `showing_rules_update`, `rental_listing_history`, `rental_listing_zillow_ad_read`: read and change each rental unit's listing, meaning the next lease on offer (`asking_rent`, `next_lease_start_*`, `off_market_on`, `showing_booking_url`, `zillow_listing_url_*`, `preferred_showing_times`, `public_facts`, `internal_notes`) and the showing rules, in the [`rental_listings`](../../rental_listings/README.md) tables. The Zillow lead agent's case is built from this. A deferred capability (`rental-listing-data`), so the tools appear after `load_capability`. Every change must pass the checks in `rental_listings/checks.py`; a new listing, an address, a public fact, an internal note, a booking link or a Zillow ad link also needs HITL approval, and typed fields are saved at once. `rental_listing_zillow_ad_read` fetches a unit's live Zillow ad (`rental_listings/zillow_ad.py`) and returns it beside the listing, so the agent can propose changes from it. Every save is recorded in `rental_listing_changes` with the user and the conversation. |
| `_logging.py` | Shared error logging wrapper for tool failures. Workspace file-tool errors (`WorkspaceToolError`) log at warn (not error) so they don't page; a missed edit (`WorkspaceEditMiss`) gets a "re-read the file, don't guess whitespace" hint, and repeated identical recoverable failures escalate with explicit STOP guidance. |

## Python sandbox (`code_tools.py`)

`run_python` runs model-written Python in [Monty](https://pydantic.dev/docs/monty/) 1.x. The interpreter never runs in the API process: `AsyncMonty` owns a pool of `monty` worker subprocesses, and the binary ships in the `pydantic-monty-runtime` wheel (it lands in `.venv/bin/monty`; the path is pinned so `PATH` and `MONTY_BIN` cannot swap it).

**Contract with the model.** Captured `print()` output, then the value of the last expression; `(no return value)` when there is neither; cut at `_OUTPUT_CAP` (5,000 characters) with `...(truncated)`. Every failure comes back as a `Code execution error: ...` string and nothing is raised into the run. Snippet errors read `ErrorType: message (line N)`.

**What the code can reach.** Nothing is mounted and no `os=` handler is passed, so there is no filesystem (`open()` raises `PermissionError`), no environment variables, and no network or subprocess modules. Monty's standard-library subset is importable (`json`, `re`, `math`, `datetime`, `decimal`, `csv`, `collections`, `itertools`, `functools`, `random`, `hashlib`, `base64` and a few more); third-party packages, `zoneinfo` and `statistics` are not. The sandbox clock is UTC. The only host code it can call is the helper functions in `_EXTERNAL_FUNCTIONS` (`now_iso`, `hours_between`, `json_loads`, `re_findall`, `math_fn`, ...). Those date from 0.0.9 and are kept so existing skills keep working; they run in the API process, so keep them pure and never evaluate their arguments.

**Limits** (`_LIMITS`, per call):

| Limit | Value | Why |
|-------|-------|-----|
| Execution time | 10 s | Snippets are millisecond jobs; a runaway loop ends in seconds. Time inside helpers and sleeps is not counted. |
| Memory | 64 MiB heap | Far above what reshaping tool output needs; at most 4 workers, so 256 MiB worst case. |
| Recursion depth | 1000 | Monty's default. |
| Host round trips | 10,000 | Each helper call is one. Monty's default of 1000 would stop a per-row helper loop. |
| Total sleep | 5 s | `time.sleep()` is importable; a tool call has no reason to wait. |

Host-side backstops on the pool: a call waits at most 10 s for a free worker, and a worker that has not answered for 30 s is killed.

**Lifecycle.** One pool per process, created on the first call (1 warm worker, up to 4). Each call checks out a fresh session, so no state carries between calls and a session that hit a limit is never reused. The pool replaces a worker that crashes or is killed, so one bad snippet cannot break later calls. `api/index.py` stops the pool on shutdown and, on Railway, calls `warm_code_sandbox()` at startup so an image that cannot run the binary fails its health check instead of going live.

Do not replace this with the harness `CodeMode` capability: it folds the agent's other tools into code execution, which would route approval-gated tools around their gates.

## Repeat-call loop breaker (`_logging.py`)

`ErrorLoggingToolset` counts *recoverable* tool failures per run, keyed by tool name + arguments. Once the same call has failed identically `REPEAT_ERROR_THRESHOLD` (2) times, the returned error string gains a "STOP — change approach" instruction with concrete recovery steps.

Why: a model that re-sends byte-identical arguments after an identical error learns nothing from the repeat, so it loops — and each attempt spends one of the run's ~50 model requests. On 2026-08-08 a scheduled check died with `UsageLimitExceeded` after seven `workspace_edit_file` EditErrors on `MEMORY.md`, four of them byte-for-byte identical. The first failure still returns the bare error (plus the EditError re-read hint when applicable), because state can legitimately change between calls; only the repeat escalates.

Counts live on `SerniaDeps.recoverable_tool_error_counts`, so they are scoped to one agent run — a module-level cache would leak across runs. Deps objects without the attribute simply never escalate.

The matching instruction lives in `instructions.py`: MEMORY.md's injected copy is a snapshot taken before the first tool call, so after the agent edits MEMORY.md the snapshot is stale — the agent is told to re-read the file rather than guess at whitespace.

## Fixed external message templates

`list_message_templates` discovers code-owned reminders. `send_templated_message` sends
one to a known, eligible Quo contact by SMS, email, or both without HITL. The initial
USPS reminder checks active lease dates within 14 days. No message text or sender is
accepted from the model. See [policy and operation](../messaging/README.md).

### Quo inbox scope and internal history

Inbox sweeps scan both the shared tenant-facing line and the AI Intern line,
label each conversation with its inbox, and fetch snippets from that same line.
Direct history uses the sending convention: contacts whose company is
`Sernia Capital LLC` use the AI line; other contacts use the shared line.
Thus Peppino task replies and AI-to-shared-team notifications are visible.
Pass `inbox="team"` or `inbox="ai"` to inspect a listed conversation on that
specific line, including older history on a contact’s other line.
The shared team number is a single participant even though multiple staff can
access it. A true group requires multiple recipient phone numbers. Done threads
are excluded from active lists; check direct history before reporting a gap.


Emergency escalation delivery uses the durable outbox documented in
[`open_phone/README.md`](../../open_phone/README.md#durable-emergency-delivery).
`trigger_escalation` reports per-recipient acceptance, pending, blocked, or unknown
status; provider acceptance does not establish human receipt. Queued/uncertain
outcomes must not prompt another tool call or duplicate SMS/email fallback.

## Conversation analytics (`duckdb_tools.py`)

DuckDB tables persist per conversation under `/tmp/sernia_duckdb`. The agent can
run one analytics statement at a time (SELECT, CREATE, INSERT, UPDATE, DELETE,
ALTER or DROP), including joins, CTEs and window functions. Use
`load_dataset` for CSVs produced by data-fetching tools; SQL cannot import files
or access the network. Custom table names are quoted as identifiers and CSV
paths are bound parameters. Imports atomically replace the named table.

Each SQL/import/describe operation runs in a fresh `python -I` subprocess with
no inherited API credentials or dotenv loading. Every connection disables external
access, extension installation/autoloading, persistent secrets and spill files,
then locks its configuration. Only an import worker may access its one validated
conversation CSV; it never runs model SQL. DuckDB implicitly permits its own
DB/WAL paths, so the SQL parser also rejects COPY and other non-analytics statement
types, including EXPLAIN ANALYZE wrappers. Persisted views/macros execute under the same restrictions on later turns.

Limits: two concurrent workers per API process, a 30-second wall deadline,
20 CPU seconds, 128 MiB DuckDB memory, 128 MiB per database/WAL file, and a
1 GiB address-space ceiling on Linux production workers. macOS development has
the other limits but no OS address-space ceiling. CSV imports are limited to
10 MiB and SQL to 64,000 characters. Query results fetch at most 101 rows to show
100; truncated results say “more than 100” rather than reporting an exact total.
A failed or timed-out worker is reaped and returns a tool error. Concurrent access
to the same conversation database may return a lock error and can be retried.

These controls close the supported SQL file/network APIs and isolate resource
failures from the API. The worker still runs as the API's OS user: DuckDB engine
vulnerabilities are outside this boundary; this is not an OS filesystem/network
sandbox. See [DuckDB's security model](https://duckdb.org/docs/1.5/operations_manual/securing_duckdb/overview).
The startup cleanup removes old conversation files; there is no aggregate quota
across all conversations.
