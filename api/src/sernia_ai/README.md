# Sernia AI

Production AI assistant for Sernia Capital LLC's rental real estate business.

Built with **PydanticAI** (Graph Beta API), **FastAPI**, and integrated with OpenPhone, Gmail, Google Calendar/Drive, and ClickUp.

## Architecture

- **Agent** (`agent.py`) — Main PydanticAI agent with tool use, sub-agents, and persistent memory
- **Instructions** (`instructions.py`) — Static system prompt + dynamic context injection (datetime, memory, filetree, modality, triggers)
- **Config** (`config.py`) — Phone IDs, rate limits, and other tunables
- **Model config** (`model_config.py`) — Runtime-switchable main-agent model (GPT-5.4 / Sonnet 4.6 / Opus 4.7), resolved per run via the `model_config` app_setting. `WebFetchTool` is attached only when an Anthropic model is active.
- **Routes** (`routes.py`) — FastAPI endpoints for chat, conversations, approvals, and admin

## Documentation

| Document | Description |
|----------|-------------|
| [`tools/README.md`](tools/README.md) | SMS gates, mass-texting pattern, tool inventory |
| [`triggers/README.md`](triggers/README.md) | Trigger flows (team SMS, AI SMS, email) with diagrams |
| [`push/README.md`](push/README.md) | W3C Web Push + VAPID implementation |
| [`PLAN.md`](PLAN.md) | Master architecture document (design decisions, phases) |

## Key Concepts

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
- **HITL approval** — External SMS, emails, task deletion, contact updates/deletes, and calendar writes require human approval
- **Universal kill switch** — DB-backed toggle disables all automated triggers
- **Rate limiting** — Per-source cooldowns prevent runaway trigger loops

### Memory System

Git-backed persistent workspace at `/workspace/`:
- `MEMORY.md` — Long-term memory (injected into every conversation)
- `daily_notes/` — Date-stamped notes per topic
- `areas/` — Deep knowledge by domain (properties, tenants, etc.)
- `.claude/skills/` — Playbooks and procedures. The agent discovers and reads skills via the `SkillsToolset` tools (`list_skills`, `load_skill`, `read_skill_resource`, `run_skill_script`); the registry is auto-injected into the system prompt. Workspace file tools (`workspace_write` / `workspace_edit`) are reserved for **editing** skills. Path mirrors Claude Code's convention so the workspace is interoperable with `cd workspace && claude` runs.

### Server-Side vs Knowledge-Repo Content

The agent's behavior comes from two sources with different error boundaries:

| Source | Location | Edited by | Error handling |
|--------|----------|-----------|----------------|
| **Server-side** | `api/src/sernia_ai/` (Python) | Developers (code deploys) | Bugs crash the app — standard software quality applies |
| **Knowledge repo** | `.workspace/` (`sernia-knowledge` git repo) | Agent + humans at runtime | Must **never** crash the server — all reads are error-wrapped |

This distinction matters most for **skills** (`/workspace/.claude/skills/<name>/SKILL.md`). Skills are runtime-editable YAML+markdown files that the agent itself can create and modify via `workspace_edit`. A malformed SKILL.md (bad YAML frontmatter, broken encoding, etc.) must degrade gracefully:

- **Reload** (`reload_skills()` in `agent.py`): Per-directory try/except — a broken skill directory is skipped and logged, other skills still load.
- **Injection** (`SkillsToolset.get_instructions()`): Operates on already-loaded `_skills` dict, so it only sees successfully parsed skills.
- **Decorator** (`refresh_skills_before_run`): Wraps the reload in try/except — if the entire reload fails, the agent runs with stale skills rather than crashing.

The same principle applies to all knowledge-repo content: `MEMORY.md` reads are capped and wrapped, filetree generation catches `OSError`, and workspace file tools return error strings rather than raising.

