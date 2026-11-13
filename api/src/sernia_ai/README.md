# Sernia AI

Production AI assistant for Sernia Capital LLC's rental real estate business.

Built with **PydanticAI** (Graph Beta API), **FastAPI**, and integrated with OpenPhone, Gmail, Google Calendar/Drive, and ClickUp.

## Architecture

- **Agent** (`agent.py`) — Main PydanticAI agent with tool use, sub-agents, and persistent memory
- **Instructions** (`instructions.py`) — Static system prompt + dynamic context injection (memory, filetree, modality, then per-run context: datetime, user, deeplink). See [Prompt stability](#prompt-stability).
- **Config** (`config.py`) — Phone IDs, rate limits, and other tunables
- **Model config** (`model_config.py`) — Runtime-switchable main-agent model (GPT-5.6 Luna / Sonnet 4.6 / Opus 4.7) and reasoning effort (Low / Medium / High / X-High / Max — Max is GPT-5.6's top tier, above X-High), resolved per run via the `model_config` app_setting. Web search/fetch live on the agent as provider-adaptive `WebSearch`/`WebFetch` capabilities (native web fetch is Anthropic-only and is dropped automatically on OpenRouter runs). See [Model Gateways](#model-gateways) below.
- **Routes** (`routes.py`) — FastAPI endpoints for chat, conversations, approvals, and admin
- **Run pipeline** (`runs.py`) — What happens after the model stops, for every run. See [Run pipeline](#run-pipeline).

## Documentation

| Document | Description |
|----------|-------------|
| [`tools/README.md`](tools/README.md) | SMS gates, mass-texting pattern, tool inventory |
| [`triggers/README.md`](triggers/README.md) | Trigger flows (team SMS, AI SMS, email) with diagrams |
| [`push/README.md`](push/README.md) | W3C Web Push + VAPID implementation |
| [Open work](../../../docs/ROADMAP.md) | Exploratory follow-ups and service backlog |

## Key Concepts

### Model Gateways

| Model | Gateway | Key | Model string |
|-------|---------|-----|--------------|
| GPT-6 Luna (default) / GPT-5.6 Luna | **OpenRouter** | `PORTFOLIO_OPENROUTER_API_KEY` → bridged to `OPENROUTER_API_KEY` in `api/__init__.py` | `openrouter:openai/gpt-6-luna` / `openrouter:openai/gpt-5.6-luna` |
| Claude Sonnet 4.6 / Opus 4.7 | **OpenRouter** | same | `openrouter:anthropic/claude-sonnet-4.6` / `openrouter:anthropic/claude-opus-4.7` |
| Sub-agents (summarize / compact) | **OpenRouter** | same | `openrouter:anthropic/claude-haiku-4.5` |
| Default escalation assessor (Luna, low) | **OpenRouter** | `PORTFOLIO_OPENROUTER_API_KEY` | `openai/gpt-5.6-luna` |
| Optional Jev escalation classifier | **OpenRouter Decisions** | `PORTFOLIO_OPENROUTER_API_KEY` | `typesafe/jev-1.13` |

All runtime inference uses OpenRouter, including demo, email classification,
and escalation calls. Claude selections keep their model identities; direct
Claude instruction/tool/message caching is preserved using OpenRouter cache
controls; native web fetch is unavailable. Public chat/email use the separate
`PUBLIC_PORTFOLIO_OPENROUTER_API_KEY` exclusively.
Sonnet xhigh maps to high because that route does not support xhigh.

The optional Jev classifier uses the TypeSafe SDK and a small endpoint adapter; see
[OpenPhone escalation](../open_phone/README.md) for its provider prerequisite
and assessment-only tests. The following chat-model settings do not apply to Jev.

Things worth knowing about the OpenRouter path (all handled in
`model_config.py` — see `SerniaOpenRouterModel`):

- **Upstream is pinned to OpenAI** (`config.OPENROUTER_ALLOWED_PROVIDERS`).
  OpenRouter otherwise load-balances GPT-5.6 Luna across OpenAI, Azure and
  Amazon Bedrock, which differ by up to ~10x in price and in supported
  parameters (Bedrock drops `response_format`/`structured_outputs`). Widen the
  list to opt into failover.
- **Reasoning effort passes through verbatim**, including `max`. OpenRouter
  validates the enum server-side (`max|xhigh|high|medium|low|minimal|none`).
  Routing it through pydantic-ai's unified `thinking` setting instead would
  silently collapse both `xhigh` and `max` to `high`.
- **Web search** is OpenRouter's `openrouter:web_search` server tool.
  pydantic-ai sends `WEB_SEARCH_ALLOWED_DOMAINS` as the tool's
  `allowed_domains`; without it the agent could search the whole web. On
  pydantic-ai 1.x this was the `web` plugin, and `SerniaOpenRouterModel`
  re-attached the domains itself. `test_model_config.py` pins the request.
- **Streamed encrypted reasoning items are kept distinct.** A streamed
  response can hold two encrypted reasoning items (OpenAI emits them when
  reasoning interleaves with several tool calls in one turn). If both land on
  one key they merge into one ThinkingPart with the first item's id and the
  last item's payload, and replaying it fails the whole run with a 400
  `invalid_encrypted_content`. pydantic-ai 1.x merged them on every stream;
  2.x keys them by OpenRouter's `index` and still merges them when `index` is
  absent or repeated. `SerniaOpenRouterStreamedResponse` keys each encrypted
  item by its own id, and a canary
  (`test_upstream_still_merges_encrypted_reasoning_items_without_an_index`)
  fails when upstream stops merging so the subclass can be deleted.
- **Cost tracking** comes from OpenRouter, not genai-prices. We enable
  OpenRouter usage accounting (`usage.include`) and stamp the exact billed
  amount, which includes web-search charges. genai-prices (>= 0.1.5) also
  prices these routes, and pydantic-ai writes that list estimate to
  `operation.cost` after the model returns, so the billed amount also goes on
  `operation.billed_cost` and `BilledCostSpanProcessor`
  (`utils/llm_cost_breakdown.py`) restores it as the span ends. Known gap: the
  per-token-type breakdown looks models up without a provider, so OpenRouter
  runs show total cost but not the v2 by-bucket panel (the v1 panel covers
  them via hard-coded tiers). `test_model_pricing.py` guards both halves.
- **The GPT route is BYOK.** OpenRouter calls OpenAI with our own key, so
  OpenAI bills list price and OpenRouter's own `cost` is $0. The spend arrives
  as `upstream_inference_cost`, and `gateway_billed_cost`
  (`utils/llm_cost_breakdown.py`) adds it to OpenRouter's fee. Before
  2026-11-12 only `cost` was read, so main-agent spend went unrecorded from
  late July, and the dashboard priced these routes at 50% of list. Production
  billing matches full list. BYOK responses also report 0 input and output
  tokens (the prompt shows up as `cache_write_tokens`), so token-based
  estimates for this route are unreliable; trust the billed cost.

### A note on reported LLM cost

Reported spend for GPT-5.6 Luna was **5x too high** until 2026-08. genai-prices
`0.0.71` (the old pin) priced it at $1.00/$6.00 per MTok when OpenAI actually
charges $0.20/$1.20, and pydantic-ai stamps `operation.cost` from that data —
so ~$67 of reported production spend over 13 days was really ~$13.38. The same
wrong rates were hard-coded in the dashboard's v1 panel, and `claude-opus` had
no branch there at all (an Opus run costed as $0).

Fixed by pinning `genai-prices>=0.1.1` and correcting the v1 tiers, with
`api/src/tests/test_dashboard_pricing.py` re-deriving every hard-coded rate from
genai-prices so the two can't silently drift again. Note that already-emitted
telemetry keeps its wrong `operation.cost` — panels that read that attribute
(cost by env / by trigger source) stay 5x high for historical Luna spans, while
the v1 by-token-type panel recomputes from token counts and so corrects
retroactively.
- **Prompt-cache retention is shorter.** OpenRouter reaches OpenAI over Chat
  Completions, where caching is automatic but the Responses-only
  `prompt_cache_retention="24h"` extension isn't available — infrequent
  scheduled runs fall back to OpenAI's default cache window.

### Run pipeline

A run reaches the agent five ways: web chat (streaming), an approval resume, a
1:1 SMS, a group SMS, and a background trigger (Zillow email, scheduled check).
They differ in how the run **starts** — gates, history loading, prompt, deps —
and that stays with each entry point. What happens **after** the model stops is
identical and lives in `runs.py`:

```
sync the workspace -> save the conversation -> deliver the outcome
```

| Outcome | Delivered |
|---------|-----------|
| Paused for approval | Approval push. No reply. |
| `NoAction` | Nothing. |
| Text | SMS reply when the run owns an SMS thread (`deps.sms_reply_recipients`); web push to the person who asked (`notify_user_id`). |

Pushes deep-link to the saved conversation, so they are sent only once the save
is confirmed. An SMS reply does not wait on the save. The save always uses a
fresh DB session (the caller's may have sat idle through a long run) and records
the run's tokens and cost.

| Entry point | Uses |
|-------------|------|
| `routes.chat_sernia` (streaming) | `finalize_run` from the stream's `on_complete` |
| `routes.approve_conversation` | `finalize_run` after `resume_with_approvals`; `save_failed_run` on error |
| SMS triggers, `run_agent_for_trigger` | `run_and_finalize` |

Add a new entry point by building its deps and calling one of these — not by
copying the steps. `test_run_pipeline.py::TestOnePipeline` fails if an entry
point starts calling the pipeline's steps directly.

### Prompt stability

pydantic-ai re-evaluates every instruction function before **each model
request**, not once per run. Providers cache prompts by exact prefix, and the
system prompt precedes the whole message history — so a section that changes
mid-run (the clock ticking over, `MEMORY.md` after the agent edits it) would
re-bill every earlier tool call and result at the uncached rate on each
remaining request. Two rules in `instructions.py` prevent that:

- **Snapshot per run** — `@once_per_run` evaluates a section once and replays
  it for the rest of the run (stored on `SerniaDeps.instruction_snapshots`).
  The injected `MEMORY.md` is therefore a true start-of-run snapshot, which is
  what the static prompt tells the agent.
- **Stable sections first** — `DYNAMIC_INSTRUCTIONS` is ordered from what every
  conversation shares (memory, filetree) to what differs per run (datetime,
  user, deeplink), so the shared part stays a cacheable prefix.

`test_sernia_agent_wiring.py::TestInstructionsStableWithinRun` fails if a
section starts changing between requests. Wrap any new volatile section in
`@once_per_run` and place it after the stable ones.

### Conversation metadata

`save_agent_conversation` **merges** `metadata` onto what is stored (`None`
leaves it untouched). A conversation is saved by several paths over its life —
the trigger that created it, then web-chat turns and approvals — and the
trigger's keys must survive those: `trigger_phone` /
`trigger_group_participants` route post-approval SMS replies, and
`trigger_source` identifies how a conversation started. The failure flags
(`partial`, `error`, `llm_error`) describe a single save and are dropped on the
next one. Per-run cost comes from the amount OpenRouter reports on each
response (`extract_run_usage`), since genai-prices has no entries for
OpenRouter model slugs.

### Modalities

The agent operates in three modalities, each with distinct behavior:

| Modality | Trigger | Tone | Response Channel |
|----------|---------|------|-----------------|
| `web_chat` | User message in web UI | Conversational, markdown | Web chat |
| `sms` | Inbound SMS to AI number | Short, direct, no markdown | SMS reply |
| `email` | Scheduled email processing | Professional, structured | Web chat (team alert) |

### Safety Gates

- **Internal/external SMS separation** — Routing keeps internal phone numbers out of external threads; mixed internal+external **group texts** are hard-blocked deterministically — a group is always all-internal or all-external (see [`tools/README.md`](tools/README.md))
- **Unit isolation** — Cross-unit tenant group texts blocked; roommates within a unit share one group thread (see [`tools/README.md`](tools/README.md))
- **HITL approval** — External SMS, emails, task deletion, contact updates/deletes, and calendar writes (create or delete) for events with external attendees require human approval. Internal-only calendar writes are not gated. The one opt-out is the Zillow auto-reply toggle, and it covers only threaded replies to Zillow relay addresses, at most one automatic reply per original message (see [`triggers/README.md`](triggers/README.md#key-design-choices)).
- **Universal kill switch** — DB-backed toggle disables all automated triggers
- **Rate limiting** — Per-source cooldowns prevent runaway trigger loops
- **Repeat-call loop breaker** — A tool call that fails identically twice in one run gets a "STOP, change approach" instruction appended to the error, so the agent can't spend its whole request budget re-sending arguments that already failed. See [`tools/README.md`](tools/README.md#repeat-call-loop-breaker-_loggingpy)

### Memory System

Git-backed persistent workspace at `/workspace/`, read and written with the `workspace_*` file tools (the harness `FileSystem` tools behind an adapter; see [`tools/README.md`](tools/README.md)). Changes are committed and pushed once at the end of each run (`runs.py`):
- `MEMORY.md` — Long-term memory (injected into every conversation)
- `daily_notes/` — Date-stamped notes per topic
- `areas/` — Deep knowledge by domain (properties, tenants, etc.)
- `.claude/skills/` — Playbooks and procedures. The harness `Skills` capability (`pydantic_ai_harness.Skills`) reads them at the start of every run: the catalog (name + description) is injected into the instructions, and the agent loads a skill's body with the `load_capability` tool. It reads `SKILL.md` only; a skill's other files are ordinary workspace files, and nothing runs scripts. Workspace file tools (`workspace_write_file` / `workspace_edit_file`) are reserved for **editing** skills. Path mirrors Claude Code's convention so the workspace is interoperable with `cd workspace && claude` runs. One skill is not in the workspace: `zillow-auto-reply` is built in code from `zillow_leads/prompts/policy.md` (see `zillow_leads/skill.py`) as a deferred capability in the same catalog. It ships with the deploy, and a workspace skill of the same name is left out of the catalog.

### Server-Side vs Knowledge-Repo Content

The agent's behavior comes from two sources with different error boundaries:

| Source | Location | Edited by | Error handling |
|--------|----------|-----------|----------------|
| **Server-side** | `api/src/sernia_ai/` (Python) | Developers (code deploys) | Bugs crash the app — standard software quality applies |
| **Knowledge repo** | `.workspace/` (`sernia-knowledge` git repo) | Agent + humans at runtime | Must **never** crash the server — all reads are error-wrapped |

This distinction matters most for **skills** (`/workspace/.claude/skills/<name>/SKILL.md`). Skills are runtime-editable YAML+markdown files that the agent itself can create and modify via `workspace_edit_file`. A malformed SKILL.md (bad YAML frontmatter, broken encoding, etc.) must degrade gracefully:

- **One bad skill**: the harness skips a `SKILL.md` it cannot parse (bad frontmatter, no description, invalid UTF-8, a name that does not match its directory) and the other skills still load. `WorkspaceSkills` in `agent.py` logs each skipped skill to Logfire as a warning.
- **A missing skills directory**: skipped (`missing_directories="skip"`); the agent runs with the deployed skill only.
- **Anything else** (for example a file the process cannot read): `WorkspaceSkills.for_run` catches it, logs an error, and the agent runs without workspace skills rather than not at all.
- **Names that clash**: a skill shares one id space with the agent's other capabilities, and a clash fails the run. `RESERVED_SKILL_NAMES` in `agent.py` keeps those names out of the catalog; a test fails when a new capability needs adding to it.

The same principle applies to all knowledge-repo content: `MEMORY.md` reads are capped and wrapped, filetree generation catches `OSError`, and workspace file tools return error strings rather than raising.


### Fixed external reminders

The agent can call `list_message_templates` / `send_templated_message` to send exact,
code-owned reminders without HITL. SMS, email and dual delivery are supported.
The USPS forwarding template validates fresh lease dates in Quo; general send tools
retain approval gates. See [fixed-message policy](messaging/README.md).

SMS reply delivery and current Quo group-history support are documented in
[tools/README.md](tools/README.md#automatic-sms-replies) and
[triggers/README.md](triggers/README.md#sms-delivery-reliability-september-2026).
