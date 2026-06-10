# CLAUDE.md - AI Assistant Guide

> **Last Updated**: 2026-06-10
> `AGENTS.md` is a symlink to this file so non-Claude agent tools pick it up too.

---

## Repository Overview

A monorepo for **Sernia Capital LLC**'s rental real estate business with AI-powered automation, plus personal portfolio projects.

**Philosophy**: Iterative development, simplicity first, solo developer context.

**Key Applications**:
- **React Router Web App** (`apps/web-react-router/`) - Primary web interface
- **FastAPI Backend** (`api/`) - Python API with multi-agent AI, webhooks, scheduling
- **Expo Mobile App** (`apps/my-expo-app/`) - React Native mobile app

---

## Development Environments

This project supports two development environments:

| Environment | Database | Setup |
|------------|----------|-------|
| **Local CLI** | Remote Neon Postgres | Manual setup with `.env` |
| **Claude Code on Web** | Local PostgreSQL | Auto-setup via session-start hook |

### Claude Code on Web (Remote)

When running in Claude Code's cloud environment (`CLAUDE_CODE_REMOTE=true`), a **session-start hook** automatically runs `.claude/setup_remote.sh` which:

1. Creates Python venv and installs dependencies (`uv sync`)
2. Starts local PostgreSQL and configures authentication
3. Runs database migrations (`alembic upgrade head`)
4. Seeds the database (contacts, default `model_config` app setting, and — on non-production — demo Sernia conversations so the chat UI and `db_*` search tools have data; see `api/seed_db.py`). When the `SEED_BUCKET_*` env vars are configured, it also downloads sanitized real conversations from a private Railway bucket (see README.md "Sanitized Seed Data")
5. Installs pnpm dependencies and builds React Router app
6. Bridges `RAILWAY_MCP_TOKEN` → `RAILWAY_API_TOKEN` and pre-links the portfolio project to `development/fastapi` (not production - safer default). Switch envs/services with the `link-environment` / `link-service` MCP tools. **PR environments:** Named `portfolio-pr-<number>` (e.g., `portfolio-pr-248`). List all with `railway environment list --json`.

**Verification**: Check `/tmp/.claude_remote_setup_ran` exists to confirm the hook ran.

**Known environment constraints** (Claude Code on web):
- **Postgres can stop mid-session** (long sessions). If DB tests fail with "connection refused", run `sudo service postgresql start` and retry.
- **Anthropic key naming**: the app's key is `SERNIA_ANTHROPIC_API_KEY` — never name an env var `ANTHROPIC_API_KEY` (it breaks Claude Code cloud sessions). `api/__init__.py` bridges it to `ANTHROPIC_API_KEY` inside app/test processes for SDK auto-discovery. If `SERNIA_ANTHROPIC_API_KEY` is absent in this sandbox, live Anthropic tests can't run; the root `conftest.py` provides dummy keys so imports/collection work.
- **Outbound port 5432 is blocked** — remote Neon Postgres is unreachable from the sandbox. For real production data use the **`load-prod-data` skill** (`.claude/skills/load-prod-data/`): ad hoc rows via the Neon MCP tools (sanitize before inserting locally), persistent baseline via the Railway seed bucket (auto-loaded at session start when `SEED_BUCKET_*` is configured).
- **Gitignored fixture dirs** (`api/src/tests/requests/`, `api/src/tests/sensitive/`) are absent — tests depending on them auto-skip.

**After setup, start servers with**:
```bash
pnpm dev              # React Router (port 5173)
pnpm fastapi-dev      # FastAPI (port 8000)
pnpm dev-with-fastapi # Both
```

**Expected warnings**: Logfire API unreachable (proxy restrictions) - non-blocking.

**Production Protection**: PreToolUse hooks in `.claude/hooks/` guard Railway and Neon operations. Philosophy: protect production, allow development freely. See [`.claude/hooks/README.md`](.claude/hooks/README.md) for details.

### Local CLI Development

Requires manual setup - see [Initial Setup](#initial-setup) below. Uses remote Neon Postgres with `DATABASE_REQUIRE_SSL=true`.

---

## Tech Stack

### Frontend
- React Router v7 (framework mode) + Vite + React 19
- Shadcn UI + Tailwind CSS + Clerk auth
- Vercel AI SDK for streaming

### Backend
- FastAPI + Hypercorn (ASGI) + Python 3.11+
- SQLAlchemy 2.0 + Alembic + Neon Postgres
- PydanticAI (>=1.106, capabilities API: `WebSearch`/`WebFetch`/`ProcessHistory`/`Instrumentation`; graph API for multi-agent demos). Note: pydantic-ai validates provider API keys at `Agent()` construction — agents built at import time need keys (or the conftest dummies) present.
- APScheduler for scheduled jobs + Logfire observability
- DBOS workflows (currently disabled - see below)

### External Integrations
OpenPhone, Google Workspace, Twilio, Clerk, Railway, ClickUp

---

## Directory Structure

```
/
├── .claude/                   # Claude Code hooks and settings
│   ├── settings.json          # Permissions, hooks config
│   ├── setup_remote.sh        # Session-start hook script
│   └── hooks/                 # PreToolUse guards for Railway/Neon
├── api/                       # FastAPI Backend
│   ├── index.py               # Main app entry
│   └── src/
│       ├── ai_demos/          # Demo AI agents (chat_emilio, chat_weather, multi_agent_chat)
│       ├── sernia_ai/         # Sernia AI production agent
│       ├── database/          # Models, migrations
│       ├── apscheduler_service/ # APScheduler (active)
│       ├── dbos_service/      # DBOS workflows (disabled)
│       └── ...                # Other modules
├── apps/
│   ├── web-react-router/      # React Router v7 Web App
│   │   └── app/routes/        # File-based routes
│   └── my-expo-app/           # Expo mobile app
├── packages/                  # Shared pnpm workspace packages
└── .cursor/rules/             # AI coding guidelines (general.mdc, fastapi.mdc, react.mdc)
```

---

## Initial Setup (Local CLI)

```bash
# 1. Install dependencies
pnpm install
uv venv && source .venv/bin/activate
uv sync -p python3.11

# 2. Configure environment
cp .env.example .env  # Edit with your API keys

# 3. Start development
pnpm dev-with-fastapi
```

**Environment files**:
- **Root `.env`** - FastAPI backend only (database, API keys, etc.)
- **`apps/web-react-router/.env`** - Frontend only (created automatically for worktrees)

**Required environment variables** (see `.env.example`):
- `OPENAI_API_KEY` - AI features
- `DATABASE_URL`, `DATABASE_URL_UNPOOLED` - Neon Postgres
- `CLERK_SECRET_KEY`, `VITE_CLERK_PUBLISHABLE_KEY` - Auth
- Various integration keys (OpenPhone, Google, etc.)

---

## Git Worktrees

For parallel development, use worktrees to create isolated environments with separate ports and databases. See [`docs/WORKTREES.md`](docs/WORKTREES.md) for full documentation.

```bash
./scripts/worktree-create.sh feature-auth   # Create
./scripts/worktree-remove.sh feature-auth   # Remove
```

Each worktree gets its own ports (hash-based), database, Python venv, and node_modules.

---

## Common Commands

```bash
# Development servers
pnpm dev                  # React Router only (port 5173)
pnpm fastapi-dev          # FastAPI only (port 8000)
pnpm dev-with-fastapi     # Both concurrently

# Testing
source .venv/bin/activate && pytest -v -s             # All unit tests (excludes live)
pytest -m live api/src/tests/test_clickup_tools.py    # Live ClickUp API tests (needs keys)
pytest -m live api/src/tests/test_quo_tools.py        # Live Quo (OpenPhone) API tests (needs keys)

# Database migrations
cd api && uv run alembic upgrade head
cd api && uv run alembic revision --autogenerate -m "description"

# Add Shadcn component
cd apps/web-react-router && pnpm dlx @shadcn/ui@latest add <component>

# Approve build scripts for new packages (pnpm 11+)
pnpm approve-builds <package-name>  # Updates pnpm-workspace.yaml allowBuilds
```

### Testing Conventions

- **The default `pytest` run must pass with NO third-party credentials** — only a local Postgres. This is enforced: CI (`.github/workflows/tests.yml`) and Claude Code on web both run the default suite without real keys. If you add a test that needs a real API, mark it `live`.
- **Default `pytest` run** excludes tests marked `live` (see `pytest.ini` `addopts`).
- **`live` marker**: Tests that hit real third-party APIs (ClickUp, OpenPhone, Google, real LLM calls, etc.). Run explicitly with `pytest -m live <file>`. Require API keys in `.env`.
- **Dummy AI keys**: the root `conftest.py` sets placeholder `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` if absent — pydantic-ai validates provider keys when an `Agent` is constructed (import time), so collection would otherwise fail without keys.
- **Gitignored fixtures**: tests that need local-only files (`api/src/tests/requests/`, `api/src/tests/sensitive/`) must `skipif` when the files are missing — see `test_open_phone.py` for the pattern.
- **Smoke tests** (`TestSmoke` classes): Fast import/wiring checks that verify modules load correctly and components are connected (e.g., agent has history processors wired, sub-agent models configured). No API keys needed.
- **Unit tests**: Mock all external calls. Use realistic tool result data matching actual output formats (ClickUp task dumps, Gmail search results, etc.), not dummy strings.
- **SMS test safety**: NEVER create live tests that send real SMS to external contacts (tenants, vendors, etc.). External SMS tests must ALWAYS mock the send. Only `send_internal_sms` may be tested live against real internal numbers. If a dedicated test phone number is provided in the future, it will be explicitly configured — do not guess or use tenant numbers.
- **Test location**: `api/src/tests/` (but `pytest.ini` collects `test_*` functions from EVERY `*.py` file under `api/` — see gotchas below).

### Pytest Gotchas (learned the hard way)

- `pytest.ini` sets `python_files = *.py`, so inline tests in service modules are collected too. Consequences:
  - A FastAPI route handler named `test_*` gets collected as a test — set `handler.__test__ = False` (see `sernia_ai/push/routes.py`).
  - Every package dir under `api/` needs an `__init__.py`, or pytest imports the file under a second top-level module name and SQLAlchemy models get double-registered ("Table already defined").
- **Event loop scope is `session`** (`pytest.ini`): the app uses module-level async engines (asyncpg pools). Per-test loops cause order-dependent "Event loop is closed" failures. Don't change this back to `function`.
- **Module-level caches leak between tests** — e.g. the tool-result summary cache keyed by `tool_call_id`. Clear them in autouse fixtures when tests reuse short ids (see `test_history_processors.py`).
- **`TestModel` does not support native tools** (WebSearch/WebFetch). To run `sernia_agent` against `TestModel`, use `with sernia_agent.override(native_tools=[]):` and pass `output_type=str` (the structured output spec needs tool-mode output, which `custom_output_text` can't produce). See `test_sernia_agent_wiring.py`.

---

## Coding Conventions

> Full rules in `.cursor/rules/*.mdc`

### General
- Respect existing code patterns; don't remove functionality without asking
- Use Tailwind only (no manual CSS)
- Prefer absolute imports
- Never modify `.env` directly
- **Document external dependencies**: When a module depends on external services, API keys, generated credentials (e.g. VAPID keys), third-party protocols, or non-obvious setup steps, create a `README.md` in that module's directory. Include: what the dependency is, how to generate/obtain credentials, links to relevant specs/docs, and how to debug. This context is critical for migration, recreation, and debugging.

### TypeScript/React Router
- Functional components, no classes
- Interfaces over types, maps over enums
- Mobile-first responsive design
- Use loaders/actions for data fetching
- `~/` alias for `app/` imports

### Python/FastAPI
- Type hints on all functions
- Pydantic models over raw dicts
- Async for I/O operations
- Early returns, guard clauses
- HTTPException for expected errors

### API Routing Convention

**Module → prefix mapping must match the folder name.** Each top-level module in `api/src/` gets its own router mounted directly on the app with a prefix that mirrors the Python module name (underscores become hyphens).

| Python module | API prefix | Example endpoint |
|---------------|-----------|------------------|
| `api/src/ai_demos/` | `/api/ai-demos` | `/api/ai-demos/chat-emilio` |
| `api/src/sernia_ai/` | `/api/sernia-ai` | `/api/sernia-ai/chat` |
| `api/src/open_phone/` | `/api/open-phone` | `/api/open-phone/webhook` |

**Rules:**
- Never nest one module's router inside another module's router. Each module mounts directly on the app in `api/index.py`.
- The URL prefix is the module folder name with underscores replaced by hyphens.
- Sub-routers within a module use relative prefixes (e.g. `workspace_admin/routes.py` uses `prefix="/workspace"` under `sernia_ai`).

---

## AI Architecture

**Framework**: PydanticAI with Graph Beta API

**Demo Agents** (`api/src/ai_demos/`):
- **Router Agent** (GPT-4o-mini): Routes to specialized agents
- **Emilio Agent** (GPT-4o): Portfolio/career questions
- **Weather Agent**: Weather queries

**Sernia Agent** (`api/src/sernia_ai/`):
- Production AI assistant for Sernia Capital

**Endpoints**:
- `POST /api/ai-demos/multi-agent-chat` - Unified routing
- `POST /api/ai-demos/chat-emilio` - Direct Emilio agent
- `POST /api/ai-demos/chat-weather` - Direct weather agent
- `POST /api/sernia-ai/chat` - Sernia AI agent

Uses Vercel AI SDK Data Stream Protocol for streaming responses.

---

## Scheduled Jobs

**Active**: APScheduler (`api/src/apscheduler_service/`)

All scheduled jobs run via APScheduler with DB-backed persistence. See [`api/src/schedulers/README.md`](api/src/schedulers/README.md) for details.

**DBOS Disabled**: DBOS workflows are disabled to avoid $75/month DB keep-alive costs. The code is preserved and can be re-enabled - see the schedulers README for instructions. Search for `# DBOS DISABLED` to find all disabled code.

---

## Database

**Provider**: Neon Postgres (remote) or local PostgreSQL (Claude Code on web)

**Models**: `api/src/database/models.py` (User, Contact, Email, etc.)

**Migrations**:
```bash
cd api
uv run alembic upgrade head                              # Apply
uv run alembic revision --autogenerate -m "description"  # Create
```

---

## Deployment

**Platform**: Railway

| Environment | URL |
|------------|-----|
| Production | https://eesposito.com |
| Dev | https://dev.eesposito.com |
| FastAPI | https://eesposito-fastapi.up.railway.app/api/docs |

**PR Environments**: Auto-created with isolated Neon database branches. See [`.github/PR_ENVIRONMENTS.md`](.github/PR_ENVIRONMENTS.md) for details.

**When creating a PR, always include the web-react-router Railway preview URL in the PR description.** The URL follows this format (substitute the actual PR number):

```
https://react-router-portfolio-pr-<PR_NUMBER>.up.railway.app/
```

Example: PR #235 → `https://react-router-portfolio-pr-235.up.railway.app/`

---

## Key Files

| File | Purpose |
|------|---------|
| `api/index.py` | FastAPI entry point |
| `api/src/database/models.py` | SQLAlchemy models |
| `api/src/ai_demos/multi_agent_chat/graph.py` | Multi-agent routing |
| `apps/web-react-router/app/root.tsx` | React Router root layout |
| `.claude/setup_remote.sh` | Session-start hook for cloud env |
| `.claude/settings.json` | Claude Code permissions and hooks |

---

## Troubleshooting

### Claude Code on Web
- **Hook didn't run?** Check if `/tmp/.claude_remote_setup_ran` exists
- **Logfire warnings?** Expected - proxy restrictions, non-blocking
- **Database issues?** Verify PostgreSQL is running: `pg_isready -h localhost`. It can stop mid-session — restart with `sudo service postgresql start`.
- **"Event loop is closed" in async DB tests?** A test changed the event-loop scope — it must stay `session` (see pytest.ini comment).

### Local CLI
- **SSL errors?** Ensure `DATABASE_REQUIRE_SSL=true` for Neon
- **Module not found?** Activate venv: `source .venv/bin/activate`

---

## Related Documentation

| Document | Purpose |
|----------|---------|
| [`README.md`](README.md) | Setup guide, Docker instructions, environment URLs |
| [`docs/WORKTREES.md`](docs/WORKTREES.md) | Git worktrees for parallel development |
| [`api/README.md`](api/README.md) | FastAPI run commands |
| [`api/src/ai_demos/README.md`](api/src/ai_demos/README.md) | AI demo agents architecture and testing |
| [`api/src/schedulers/README.md`](api/src/schedulers/README.md) | Scheduler setup (APScheduler active, DBOS disabled) |
| [`.github/PR_ENVIRONMENTS.md`](.github/PR_ENVIRONMENTS.md) | PR database branching workflow |
| [`.cursor/rules/`](.cursor/rules/) | AI coding guidelines (general, fastapi, react) |
| [`roadmap.md`](roadmap.md) | Project roadmap and future plans |

## External Resources

- [React Router Docs](https://reactrouter.com/start/framework/installation)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [PydanticAI Docs](https://ai.pydantic.dev/)
- [Shadcn UI Docs](https://ui.shadcn.com/docs)
