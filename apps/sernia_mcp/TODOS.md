# TODOS — Tools Not Yet Lifted From `api/src/sernia_ai/tools/`

> **Purpose**: track every tool the `sernia_ai` PydanticAI agent has but the
> `sernia_mcp` HTTP server does not, with classification by lift difficulty
> so we can pull the easy ones in batches and design carefully for the hard
> ones. As tools land in `sernia_mcp`, delete their entry here.
>
> **Last reviewed**: 2026-04-26.

---

## 🌙 Closing thoughts (2026-04-26 EOD) — pickup notes for next session

**Tentative direction on the "Hard — needs new infra" tier**: tools that
need DB access, APScheduler jobstores, conversation-scoped CSV storage,
or other stateful infra **stay on sernia_ai as bolted-on tools** rather
than migrating to MCP. Rationale:

  - Standing up Postgres / migrations / a scheduler on `sernia_mcp` just
    to mirror tools that already work fine on sernia_ai is ~all cost,
    little benefit. The MCP server's value prop is "remote AI harnesses
    can call the same tools" — DB-bound tools (history search, conversation
    analytics) are sernia_ai-specific and not interesting to Claude.ai /
    ChatGPT clients anyway.
  - When sernia_ai eventually calls sernia_mcp for *most* tools (per the
    "End state" section below), it'll still keep its DB-bound + sandbox +
    scheduler tools as native PydanticAI tools. The dual-toolset isn't
    ugly — it's just "MCP toolset for portable stuff, native toolset for
    sernia_ai-only stuff."

This effectively answers Open Questions #2 (DB sharing) and #3 (scheduler
ownership) — the answer is "neither; those tools just don't migrate."
The TODOs below have been updated to reflect this. Re-open if Claude.ai /
ChatGPT users start asking for SMS-history search through MCP.

**🔴 Highest-priority next-session item — audit "ported" tools for true
parity.** Several tools that the prior commits marked as "shipped" are
in fact *partial* ports — they're behaviorally divergent from sernia_ai
in ways that aren't called out in the docstrings or the TODO checklist.
Treat the existing parity claims as suspect until each tool is audited
side-by-side.

**Concrete example caught 2026-04-26 — `send_email`:**

| Aspect | sernia_ai `send_email` | sernia_mcp `google_send_email` |
|---|---|---|
| Signature | `(to, subject, body, body_html="", reply_to_message_id="")` | `(to, subject, body)` |
| HTML body | ✅ multipart/alternative via `body_html` | ❌ plain-text only |
| Reply threading | ✅ `In-Reply-To` + `References` headers via `reply_to_message_id`; cross-mailbox fallback | ❌ every send is a fresh thread |
| Internal/external routing | ✅ external sends from `SHARED_EXTERNAL_EMAIL` (`all@serniacapital.com`); internal from user's own mailbox | ❌ always sends from whatever mailbox `user_email` resolves to (no shared-mailbox split) |
| HITL approval | Conditional — internal-only skips, external requires | Unconditional — card every time, even all-internal |
| Sender display | Always sets display From to `user_email` even when authenticated as shared mailbox (so replies come back to the human) | Just uses `user_email` or `sender_override` |

These are real functional regressions, not stylistic differences. If we
sent a Zillow draft via the MCP `google_send_email` today, it would (a)
lose HTML formatting, (b) not thread the reply (recipient sees a fresh
message instead of a continuation), (c) come from the wrong mailbox, and
(d) still ask Emilio for approval even when sending to himself.

**Audit task** — go through every tool TODOS marks ✅, read the sernia_ai
version line-by-line, and document the real gap. Then either close the
gap (port the missing functionality) OR mark the tool clearly as
"partial port" with the divergence list, so we don't accidentally cut
sernia_ai over to a behaviorally weaker MCP version. Likely candidates
to look at first:

- `google_send_email` (gaps above — work this first)
- `quo_send_sms` (does it have the same routing / context-seeding /
  auto-split logic as sernia_ai's `send_sms`?)
- `clickup_create_task` (does it accept and forward all the optional
  fields sernia_ai accepts?)
- `quo_create_contact` (does the payload builder produce identical
  output for the same args?)
- `google_search_emails` / `google_read_email` (do they cap output the
  same way? Same field selection?)
- `clickup_search_tasks` (does the fuzzy-match and pagination behave
  identically?)

**Next session priority** (after the audit): test the HITL approval-card
mechanism (the existing `quo_send_sms` / `google_send_email` Apps
approval flow) to understand the migration path for the **Medium —
approval-gated tools** batch (`update_contact`, `delete_task`,
`delete_contact`, calendar create/delete, `mass_text_tenants`). Concretely:

  1. Use the running dev MCP server with Claude.ai or `fastmcp dev apps`
     to actually trigger an approval card and walk through approve/reject.
  2. Watch for any rough edges — does the card render right? Is the
     `decided` reactive state preventing double-clicks? What happens if
     the user closes the card without clicking?
  3. Use what we learn to inform whether `register_approval_flow()` should
     be extracted before adding 6 more flows, or after.

That investigation unblocks the entire approval-gated batch.

---

## End state — sernia_ai eventually points at sernia_mcp

The current state is **dual-implementation**: `api/src/sernia_ai/tools/*.py`
and `apps/sernia_mcp/src/sernia_mcp/core/*` both have their own copies of
each tool's logic, often vendored from each other. This is intentional
short-term scaffolding while we validate the MCP server in production.

**The end state is**: `sernia_ai` calls `sernia_mcp` over HTTP (using the
internal-bearer auth path) for every tool that this server exposes, and
the bolted-on Python implementations in `api/src/sernia_ai/tools/` are
deleted. Sernia_ai keeps only the orchestration layer (PydanticAI agent,
modality routing, triggers, instructions, conversation history) — its
"tools" become a single MCP-client toolset pointing at `mcp.sernia.ai`.

Migration sequence (rough — adjust as we learn):

1. **Stand up sernia_mcp parity** (this doc tracks the gap). Each tool
   ships on MCP first, with tests, before we touch sernia_ai.
2. **Wire sernia_ai to use the MCP server as a toolset.** PydanticAI has
   `MCPServerHTTP` toolset support — point it at `https://mcp.sernia.ai/mcp`
   with `Authorization: Bearer ${SERNIA_MCP_INTERNAL_BEARER_TOKEN}`.
3. **Per-tool cutover under a feature flag** so we can A/B compare. For
   each tool, sernia_ai exposes both versions; the flag picks one. Once
   the MCP version has run for ~1 week without regressions, delete the
   bolted-on Python version from `api/src/sernia_ai/tools/`.
4. **Delete** `api/src/sernia_ai/tools/*` files entirely once empty, plus
   any helpers (`_clickup_request`, `_build_contact_payload`, etc.) that
   only existed to back them.

**Why not flip everything at once**: failure modes differ. A pure-Python
tool fails by raising; an MCP tool fails by network-timing-out, returning
a `ToolError`, hitting a transient deploy mid-call, etc. We want to debug
those one tool at a time, not all at once. The feature flag gives us a
clean rollback per tool.

**What sernia_ai keeps** (these stay in `api/` permanently — they don't
migrate to MCP per the closing-thoughts decision above):
- The PydanticAI agent itself (`agent.py`), graph/router, modality glue
- `triggers/` — webhook + scheduler entrypoints
- `db_search_tools.py` / DB-touching helpers — DB stays on sernia_ai
- `code_tools.py` Python sandbox — heavy dep, stays on sernia_ai
- `data_export.py` + `duckdb_tools.py` — conversation-scoped, stays
- `scheduling_tools.py` — APScheduler-bound, stays
- Conversation history / approval persistence (DB-bound)
- `instructions.py` — prompts, dynamic context, memory injection
- `routes.py` — FastAPI endpoints

Everything else (Quo, Google, ClickUp, workspace memory, send tools)
collapses onto `sernia_mcp` over time. The end-state agent has TWO
toolsets: an `MCPServerHTTP` toolset pointing at `mcp.sernia.ai` for the
portable surface, plus a native PydanticAI toolset for the DB/sandbox/
scheduler tools that stay sernia_ai-only.

---

## Current MCP tool surface (for context)

Visible tools currently registered on `sernia_mcp`:

- Memory/skills: `sernia_context`, `read_resource`, `edit_resource`, `write_resource`
- Quo: `quo_search_contacts`, `quo_get_thread_messages`, `quo_list_active_sms_threads`, `quo_create_contact`, `quo_send_sms` (HITL)
- Google (Gmail): `google_search_emails`, `google_read_email`, `google_read_email_thread`, `google_send_email` (HITL)
- Google (Drive/Docs/Sheets): `google_search_drive`, `google_read_sheet`, `google_read_doc`, `google_read_pdf`
- Google (Calendar): `google_list_calendar_events`
- ClickUp: `clickup_search_tasks`, `clickup_get_tasks`, `clickup_list_lists`, `clickup_get_maintenance_field_options`, `clickup_create_task`, `clickup_update_task`, `clickup_set_task_custom_field`

Hidden Apps backend tools (model can't reach): `_confirm_send_sms`, `_confirm_send_email`.

---

## ~~Easy lifts — read-only Google + ClickUp~~ ✅ DONE (2026-04-26)

All shipped: `google_read_doc`, `google_read_pdf`, `google_read_email_thread`,
`google_list_calendar_events`, `clickup_list_lists`, `clickup_get_tasks`,
`clickup_get_maintenance_field_options`. New deps: `pypdf` (PDF text),
`beautifulsoup4` + `markdownify` (HTML email → markdown).

`google_read_email_thread` runs the same cleanup pipeline sernia_ai does:
HTML→markdown → Zillow boilerplate stripping (`[Name] says:` anchor +
tail patterns) → 3+-line quoted-reply + attribution collapsing. The only
divergence is the oversized-message handler: sernia_ai calls a Haiku
summarizer, MCP hard-truncates at 3000 chars/message and 15000 chars total
(no LLM dep on the MCP server). All cleanup helpers vendored to
`core/google/_email_cleanup.py`; if Zillow ever changes their template,
update both copies until the sernia_ai → MCP migration completes.

---

## ~~Medium — write tools without HITL approval~~ ✅ DONE (2026-04-26)

ClickUp `create_task`, `update_task`, `set_task_custom_field`, and Quo
`create_contact` are all live as `clickup_create_task`, `clickup_update_task`,
`clickup_set_task_custom_field`, and `quo_create_contact`.

**Decision recorded**: bearer-token callers may invoke writes — same
authorization scope as Clerk-OAuth users. The bearer is treated as an
authenticated identity (`service:sernia-ai`), not a reduced-privilege
mode. If we ever want bearer-only-reads in the future, a per-tool gate
in `auth.py` inspecting `ctx.token.claims["client_id"]` is the place
to add it (currently no such gate).

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

## Stays on sernia_ai (per 2026-04-26 closing-thoughts decision)

These tools all need infra (DB, sandbox, scheduler, conversation-scoped
storage) that doesn't belong on a stateless MCP server. The decision
recorded in the closing-thoughts section is **don't migrate them** — they
stay as bolted-on PydanticAI tools on sernia_ai forever, and sernia_ai
keeps them in its native toolset alongside the MCP toolset for portable
tools. Re-open this section only if a remote MCP client (Claude.ai,
ChatGPT) explicitly needs one of these.

| sernia_ai module | Tools | Why it stays |
|---|---|---|
| `db_search_tools.py` | `db_get_contact_sms_history`, `db_search_sms_history`, `db_search_conversations` | Needs Postgres + alembic awareness; sernia_ai-internal value, not interesting to remote clients. |
| `code_tools.py` | `run_python` | Heavy `pydantic-monty` dep + RestrictedPython sandbox; trust model is murkier when the caller is a remote MCP client. |
| `data_export.py` + `duckdb_tools.py` | `list_datasets`, `load_dataset`, `describe_table`, `run_sql` | Per-conversation CSV storage; MCP is stateless across requests. Remote clients can do data analysis themselves. |
| `scheduling_tools.py` | `schedule_sms`, `schedule_email`, `list_scheduled_messages`, `cancel_scheduled_message` | Needs APScheduler with persistent jobstore (DB-backed); not worth standing up on MCP. |

---

## Hard — needs new infra (the genuinely-hard ones)

### Group-thread message history for `quo_get_thread_messages`

- **Status**: deliberately partial. The tool currently returns the most
  recent group activity (via `lastActivityId`) plus each participant's
  1:1 history, with a self-explaining caveat in the output. Older group
  messages are not retrievable.
- **Why partial**: OpenPhone's `/v1/messages?participants[]=…` filter
  silently narrows to 1:1 conversations. Verified across ~14 different
  serializations and 3 alternate endpoints — this is a server-side
  limitation of the public API, not a client-side bug. The internal
  `https://communication.openphoneapi.com/v2/activity` endpoint that the
  Quo web UI uses *can* fetch by `conversationId`, but it requires an
  Auth0 user JWT (not the API key), is undocumented, and is almost
  certainly outside ToS for programmatic agent traffic.
- **What sernia_ai does**: reads from the webhook-ingested
  `open_phone_events` Postgres table to serve full group history. See
  `api/src/sernia_ai/tools/quo_tools.py::_fetch_group_thread_from_events_table`.
- **What it'd take here**: SQLAlchemy + asyncpg + the `OpenPhoneEvent`
  model + a session factory, all of which violate the lean-deps rule in
  this service's CLAUDE.md.
- **Cleaner path when we want to close it**: expose a service-internal
  HTTP endpoint on the FastAPI backend (e.g.
  `GET /api/open-phone/conversations/{id}/messages`) gated by the
  existing `SERNIA_MCP_INTERNAL_BEARER_TOKEN`, and call it from
  `core/quo/contacts.py::get_thread_messages_core`. Keeps DB access in
  the backend, keeps the MCP lean, gives us a clean cross-service contract.
- **Marker**: search `TODO(group-thread-db)` in `core/quo/contacts.py`.

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
- **`mass_text_tenants` per-unit sharding logic before the approval flow
  refactor** (see Medium section). Don't lift this without the helper.

### Internal helpers — already lifted alongside their tools (2026-04-26)

The cleanup helpers from sernia_ai's `google_tools.py` were vendored into
`apps/sernia_mcp/src/sernia_mcp/core/google/_email_cleanup.py` along with
`google_read_email_thread`:

- `clean_zillow_email` (+ `_strip_zillow_tail`, `_ZILLOW_BOILERPLATE_RE`)
  — boilerplate stripping via `[Name] says:` anchor + tail patterns.
- `html_to_markdown` — HTML email → readable markdown via BeautifulSoup
  + markdownify, layout tables flattened.
- `is_zillow_content` — sender + body sniff.
- `strip_quoted_replies` — collapse 3+-line `>` blocks and the
  `On ... wrote:` attribution.

The only sernia_ai helper that did **not** port is `_summarize_if_long`
(LLM-based) — MCP hard-truncates instead since there's no LLM dep on the
server. Live parity tested at `tests/test_email_thread_live.py` against
the real Samantha + Nelson Chang Zillow threads.

**Drift caveat**: until the sernia_ai → MCP migration completes, both
copies need to be updated together if Zillow ever changes their email
template (the regex patterns are the only volatile bit — sender / boiler-
plate strings).

---

## Open questions

1. ~~**Bearer-vs-OAuth tool gating**~~ — **answered 2026-04-26**: bearer
   auth has full scope, no read-only gate.
2. ~~**DB sharing**~~ — **answered 2026-04-26**: MCP stays DB-less.
   DB-bound tools stay on sernia_ai forever (see "Stays on sernia_ai").
3. ~~**Scheduler ownership**~~ — **answered 2026-04-26**: same as DB.
   `scheduling_tools.py` stays on sernia_ai.
4. **Approval-flow boilerplate**: extract a `register_approval_flow()`
   registration helper now, or wait for 4+ approval flows in the door
   before refactoring? — *To be informed by the next-session investigation
   of the existing HITL approval-card mechanism (see closing thoughts at
   the top).*
5. **Workspace tools — native or via MCP?** sernia_ai today has its own
   native workspace toolset (`workspace_read`, `workspace_write`,
   `workspace_edit`, `workspace_list_files`, `search_files`,
   `workspace_delete`) operating directly on the local clone of the
   `sernia-knowledge` repo. The MCP server has its own equivalents
   (`read_resource` / `edit_resource` / `write_resource` plus the
   `sernia_context` doorway). Both write to the same git-backed repo, so
   correctness is fine either way; the question is whether sernia_ai
   should keep its native toolset or route through the MCP server's.
   - **Pro keep native**: lower latency (no HTTP roundtrip), no
     dependency on MCP being up for sernia_ai to remember things, simpler
     debug story, tools mature and well-tested. The agent's "memory" is
     load-bearing for every conversation — coupling it to a separate
     service availability is a real risk.
   - **Pro route via MCP**: single source of truth for workspace surface
     (no behavioral drift between the two implementations), cleaner
     end-state (sernia_ai becomes thinner), MCP server already does the
     git pull/commit/push lifecycle so we'd stop having two services
     racing on the same repo, and the MCP doorway pattern
     (`sernia_context` → memory + skill list) is more curated than
     sernia_ai's filetree dump.
   - If we **keep native** for sernia_ai, we should filter the workspace
     tools out of the MCP surface that sernia_ai sees (otherwise the
     agent gets duplicate tools and may pick the wrong one). The bearer-
     auth `client_id` claim is the natural filter point.
   - **Decision deferred** — revisit in the coming days alongside the
     HITL-mechanism investigation, since both questions inform what the
     final sernia_ai-as-MCP-client wiring looks like.
