# Sernia AI

Production AI assistant for Sernia Capital LLC's rental real estate business.

Built with **PydanticAI** (Graph Beta API), **FastAPI**, and integrated with OpenPhone, Gmail, Google Calendar/Drive, and ClickUp.

## Architecture

- **Agent** (`agent.py`) — Main PydanticAI agent with tool use, sub-agents, and persistent memory
- **Instructions** (`instructions.py`) — Static system prompt + dynamic context injection (datetime, memory, filetree, modality, triggers)
- **Config** (`config.py`) — Phone IDs, rate limits, and other tunables
- **Model config** (`model_config.py`) — Runtime-switchable main-agent model (GPT-5.6 Luna / Sonnet 4.6 / Opus 4.7) and reasoning effort (Low / Medium / High / X-High / Max — Max is GPT-5.6's top tier, above X-High), resolved per run via the `model_config` app_setting. Web search/fetch live on the agent as provider-adaptive `WebSearch`/`WebFetch` capabilities (native web fetch is Anthropic-only and is dropped automatically on OpenRouter runs). See [Model Gateways](#model-gateways) below.
- **Routes** (`routes.py`) — FastAPI endpoints for chat, conversations, approvals, and admin

## Documentation

| Document | Description |
|----------|-------------|
| [`tools/README.md`](tools/README.md) | SMS gates, mass-texting pattern, tool inventory |
| [`triggers/README.md`](triggers/README.md) | Trigger flows (team SMS, AI SMS, email) with diagrams |
| [`push/README.md`](push/README.md) | W3C Web Push + VAPID implementation |
| [`PLAN.md`](PLAN.md) | Master architecture document (design decisions, phases) |

## Key Concepts

### Model Gateways

| Model | Gateway | Key | Model string |
|-------|---------|-----|--------------|
| GPT-5.6 Luna | **OpenRouter** | `PORTFOLIO_OPENROUTER_API_KEY` → bridged to `OPENROUTER_API_KEY` in `api/__init__.py` | `openrouter:openai/gpt-5.6-luna` |
| Claude Sonnet 4.6 / Opus 4.7 | Anthropic (direct) | `SERNIA_ANTHROPIC_API_KEY` → bridged to `ANTHROPIC_API_KEY` | `anthropic:claude-sonnet-4-6` |
| Sub-agents (summarize / compact) | Anthropic (direct) | same | `anthropic:claude-haiku-4-5-…` |

GPT is reached through OpenRouter rather than the OpenAI API directly, so
billing, rate limits, and model availability all sit behind one gateway. Claude
stays direct — Anthropic's own API exposes richer cache-control and
adaptive-thinking knobs than OpenRouter's pass-through.

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
- **Web search** maps to OpenRouter's `web` plugin. pydantic-ai drops the
  tool's `allowed_domains`, so `SerniaOpenRouterModel` re-attaches
  `WEB_SEARCH_ALLOWED_DOMAINS` as the plugin's `include_domains` — without it
  the agent could search the whole web.
- **Streamed encrypted reasoning items are kept distinct.** pydantic-ai's
  OpenRouter streaming keys `reasoning_details` deltas by type + position, not
  by reasoning item id — so a streamed response containing two encrypted
  reasoning items (which OpenAI emits when reasoning interleaves with several
  tool calls in one turn) merges them into a single ThinkingPart carrying the
  first item's id and the last item's encrypted payload. Replaying that part
  fails the whole run with a 400 `invalid_encrypted_content` ("Encrypted
  content item_id did not match the target item id"). Bug confirmed present
  upstream through pydantic-ai 2.24.0; `SerniaOpenRouterStreamedResponse`
  fixes the keying, and a canary test
  (`test_upstream_still_merges_encrypted_reasoning_items`) fails when upstream
  fixes it so the subclass can be deleted.
- **Cost tracking** comes from OpenRouter, not genai-prices. genai-prices has
  no entry for the `openai/gpt-5.6-luna` OpenRouter route, so pydantic-ai's own
  pricing raises `LookupError` and never stamps `operation.cost`. We enable
  OpenRouter usage accounting (`usage.include`) and stamp the exact billed
  amount instead. Known gap: the per-token-type breakdown
  (`utils/llm_cost_breakdown.py`) still needs genai-prices to split a total
  across buckets, so OpenRouter runs show total cost but not the v2 by-bucket
  panel (the v1 panel covers them via hard-coded tiers).
  `test_model_pricing.py` guards both halves.
- **OpenRouter bills 50% of OpenAI list** on this route today — a standing
  discount, verified against real billed amounts. The dashboard's `openai/...`
  tiers encode that; `test_dashboard_pricing.py` fails if it ends.

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

### Modalities

The agent operates in three modalities, each with distinct behavior:

| Modality | Trigger | Tone | Response Channel |
|----------|---------|------|-----------------|
| `web_chat` | User message in web UI | Conversational, markdown | Web chat |
| `sms` | Inbound SMS to AI number | Short, direct, no markdown | SMS reply |
| `email` | Scheduled email processing | Professional, structured | Web chat (team alert) |

### Safety Gates

- **Internal/external SMS separation** — Internal phone numbers never appear in external threads
- **Unit isolation** — Cross-unit tenant group texts blocked (see [`tools/README.md`](tools/README.md))
- **HITL approval** — External SMS, emails, task deletion, contact updates/deletes, and calendar writes (create or delete) for events with external attendees require human approval. Internal-only calendar writes are not gated.
- **Universal kill switch** — DB-backed toggle disables all automated triggers
- **Rate limiting** — Per-source cooldowns prevent runaway trigger loops
- **Repeat-call loop breaker** — A tool call that fails identically twice in one run gets a "STOP, change approach" instruction appended to the error, so the agent can't spend its whole request budget re-sending arguments that already failed. See [`tools/README.md`](tools/README.md#repeat-call-loop-breaker-_loggingpy)

### Memory System

Git-backed persistent workspace at `/workspace/`:
- `MEMORY.md` — Long-term memory (injected into every conversation)
- `daily_notes/` — Date-stamped notes per topic
- `areas/` — Deep knowledge by domain (properties, tenants, etc.)
- `.claude/skills/` — Playbooks and procedures. The agent discovers and reads skills via the `SkillsToolset` tools (`list_skills`, `load_skill`, `read_skill_resource`, `run_skill_script`); the registry is auto-injected into the system prompt. Workspace file tools (`workspace_write_file` / `workspace_edit_file`) are reserved for **editing** skills. Path mirrors Claude Code's convention so the workspace is interoperable with `cd workspace && claude` runs.

### Server-Side vs Knowledge-Repo Content

The agent's behavior comes from two sources with different error boundaries:

| Source | Location | Edited by | Error handling |
|--------|----------|-----------|----------------|
| **Server-side** | `api/src/sernia_ai/` (Python) | Developers (code deploys) | Bugs crash the app — standard software quality applies |
| **Knowledge repo** | `.workspace/` (`sernia-knowledge` git repo) | Agent + humans at runtime | Must **never** crash the server — all reads are error-wrapped |

This distinction matters most for **skills** (`/workspace/.claude/skills/<name>/SKILL.md`). Skills are runtime-editable YAML+markdown files that the agent itself can create and modify via `workspace_edit_file`. A malformed SKILL.md (bad YAML frontmatter, broken encoding, etc.) must degrade gracefully:

- **Reload** (`reload_skills()` in `agent.py`): Per-directory try/except — a broken skill directory is skipped and logged, other skills still load.
- **Injection** (`SkillsToolset.get_instructions()`): Operates on already-loaded `_skills` dict, so it only sees successfully parsed skills.
- **Decorator** (`refresh_skills_before_run`): Wraps the reload in try/except — if the entire reload fails, the agent runs with stale skills rather than crashing.

The same principle applies to all knowledge-repo content: `MEMORY.md` reads are capped and wrapped, filetree generation catches `OSError`, and workspace file tools return error strings rather than raising.

