# TODOS — Tools Not Yet Lifted From `api/src/sernia_ai/tools/`

> **Purpose**: track every tool the `sernia_ai` PydanticAI agent has but the
> `sernia_mcp` HTTP server does not, with classification by lift difficulty
> so we can pull the easy ones in batches and design carefully for the hard
> ones. As tools land in `sernia_mcp`, delete their entry here.
>
> **Last reviewed**: 2026-04-26.

---

## Current MCP tool surface (for context)

Visible tools currently registered on `sernia_mcp`:

- Memory/skills: `sernia_context`, `read_resource`, `edit_resource`, `write_resource`
- Quo: `quo_search_contacts`, `quo_get_thread_messages`, `quo_list_active_sms_threads`, `quo_send_sms` (HITL)
- Google: `google_search_emails`, `google_read_email`, `google_search_drive`, `google_read_sheet`, `google_send_email` (HITL)
- ClickUp: `clickup_search_tasks`

Hidden Apps backend tools (model can't reach): `_confirm_send_sms`, `_confirm_send_email`.

---

## Easy lifts — read-only Google API calls

These are pure HTTP calls through `googleapiclient.discovery.build` with no
DB / scheduler / conversation-scoped state. Pattern is identical to
`google_search_drive` / `google_read_sheet` (already lifted): add a core
function in `core/google/`, wrap in `tools/google.py`, mock-test the API.

| Tool (sernia_ai name) | Why easy | Notes |
|---|---|---|
| `read_google_doc` | Same scope set as Sheets, returns plain text. | Sernia_ai version is in `google_tools.py:read_google_doc`. |
| `read_drive_pdf` | Drive download → `pypdf` extract → cap. | Adds `pypdf` dep. |
| `read_email_thread` | Gmail API `threads().get()`. | Returns N messages with IDs for chaining. |
| `list_calendar_events` | Calendar API list, time-window args. | Read-only; no approval needed. |

**Estimated effort**: 30 min each, mostly mechanical. Bundle into one PR.

---

## Easy lifts — read-only ClickUp

| Tool | Why easy |
|---|---|
| `clickup_list_lists` | One GET `/team/{id}/list`. |
| `clickup_get_tasks` | List with filter; already similar to existing search_tasks. |
| `clickup_get_maintenance_field_options` | Read custom-field options for a known field. |

The `_clickup_request` helper in sernia_ai is one function — port as-is.

---

## Medium — write tools without HITL approval

ClickUp `create_task`, `update_task`, `set_task_custom_field` and Quo
`create_contact` don't require approval in sernia_ai — the agent just calls
them. In MCP we can expose them the same way (no Apps approval card).
**But** before doing so, decide: should write tools require Clerk OAuth
(human user) and reject the bearer-token path? Otherwise a compromised
internal bearer = silent ClickUp/Quo writes.

Relevant decision: add a per-tool gate in `auth.py` that inspects
`ctx.token.claims["client_id"]` — if it's `service:sernia-ai`, reject
write tools. Lift `update_contact` once that gate is in place.

---

## Medium — approval-gated tools (Apps card needed)

Each of these adds a new Prefab Apps approval flow mirroring the existing
`quo_send_sms` / `google_send_email` pattern. Mostly copy-paste from
`tools/approvals.py`, but each card needs its own UI + tests.

| Tool | Card content | Notes |
|---|---|---|
| `quo_update_contact` | name/email/customFields diff | Sernia_ai uses HITL for this; MCP must too. |
| `clickup_delete_task` | task title + status | Destructive. |
| `quo_delete_contact` | contact name + phone | Destructive. |
| `create_calendar_event` | attendees, title, time, location | External attendees should mark destructive. |
| `delete_calendar_event_tool` | event title + time | Destructive. |
| `mass_text_tenants` | per-unit shard list | The per-unit grouping logic is non-trivial; the approval card needs to show ALL shards before sending. |

**Approval flow refactor consideration**: today every approval handler is
a fresh `@app.ui()` + `@app.tool()` pair with bespoke pending state. With
6+ flows incoming, it's worth extracting a `register_approval_flow()`
helper that takes the entry tool name, the card-render callable, and the
core function — to remove the boilerplate.

---

## Hard — needs new infra

These tools have non-trivial dependencies that don't exist in `sernia_mcp` yet.

### `db_search_tools.py` — DB access

- `db_get_contact_sms_history`, `db_search_sms_history`, `db_search_conversations`
- **Blocker**: `sernia_mcp` has no DB connection. Adding one means: shared
  Postgres conn, alembic migrations awareness, env config for `DATABASE_URL_*`.
- **Decision needed**: should MCP read sernia_ai's DB, or have its own?
  Cross-harness search is the "self-improving" story; isolated DB defeats
  it. But coupling deploys is bad. Probably resolve by giving MCP read-only
  access to sernia_ai's DB via env-injected URL.

### `code_tools.py` — pydantic-monty Python sandbox

- `run_python` — RestrictedPython-based eval.
- **Blocker**: heavy `pydantic-monty` dep + sandbox infrastructure.
- **Decision needed**: do we trust an MCP caller (with bearer or human OAuth)
  to run arbitrary Python? Probably yes for human OAuth, no for bearer.
  Same gate as the write-tools section above.

### `data_export.py` + `duckdb_tools.py` — conversation-scoped CSV + DuckDB

- `list_datasets`, `load_dataset`, `describe_table`, `run_sql`.
- **Blocker**: relies on per-conversation CSV storage. MCP servers are
  stateless across requests — there's no "conversation" the way sernia_ai
  has one (that's PydanticAI agent state). Would need per-session storage
  keyed on `client_id` from the token, with TTL cleanup.
- **Likely resolution**: skip. The Claude.ai client doesn't really benefit;
  it can do data analysis itself. This was a sernia_ai-internal optimization.

### `scheduling_tools.py` — APScheduler-backed scheduled sends

- `schedule_sms`, `schedule_email`, `list_scheduled_messages`, `cancel_scheduled_message`.
- **Blocker**: needs APScheduler with persistent jobstore. `sernia_mcp` has
  no scheduler, no DB, no jobstore.
- **Decision needed**: should MCP get its own scheduler, or call into
  sernia_ai's `/api/sernia-ai/admin/schedule` endpoint as a thin proxy?
  Proxy is simpler but couples deploys. Own scheduler means another DB.

### MCP-bridged Quo tools (FastMCPToolset → OpenPhone OpenAPI)

- `deleteContact_v1`, `getContactCustomFields_v1`, `listCalls_v1`,
  `getCallById_v1`, `getCallSummary_v1`, `getCallTranscript_v1`.
- **Note**: these are auto-generated by sernia_ai's `FastMCPToolset` from
  OpenPhone's OpenAPI spec at agent startup. To lift, we'd either:
  (a) call OpenPhone OpenAPI directly via the same FastMCP bridge inside
  this server (clean, but means an HTTP-server-inside-an-HTTP-server), or
  (b) rewrite each as a thin httpx call (mechanical, less clever).
- **Recommendation**: (b) for the few we actually need (probably just
  call summaries + transcripts); skip the rest until something asks for them.

---

## Tools that should NOT be lifted

- **History trimming + bootstrap helpers in `ai_sms_event_trigger.py`** —
  they're trigger-specific glue, not callable surface.
- **Internal helpers** (`_clean_zillow_email`, `_html_to_markdown`,
  `_summarize_if_long`, `_strip_quoted_replies`) — port them along WITH the
  tool that needs them, not as standalone tools.
- **`mass_text_tenants` per-unit sharding logic before the approval flow
  refactor** (see Medium section). Don't lift this without the helper.

---

## Open questions to answer before next batch

1. **Bearer-vs-OAuth tool gating**: should the bearer auth path be
   read-only by default, with writes requiring human OAuth?
2. **DB sharing**: does `sernia_mcp` get its own DB, share sernia_ai's, or
   stay DB-less and proxy DB-needing operations into the FastAPI service?
3. **Scheduler ownership**: same question, for APScheduler.
4. **Approval-flow boilerplate**: extract a registration helper now, or
   wait for 4+ approval flows in the door before refactoring?

These should be agreed on before the medium/hard batches start so we don't
make divergent choices per-tool.
