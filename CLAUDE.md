# CLAUDE.md - AI Assistant Guide

> **Last Updated**: 2025-12-29

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
4. Seeds the database
5. Installs pnpm dependencies and builds React Router app

**Verification**: Check `/tmp/.claude_remote_setup_ran` exists to confirm the hook ran.

**After setup, start servers with**:
```bash
pnpm dev              # React Router (port 5173)
pnpm fastapi-dev      # FastAPI (port 8000)
pnpm dev-with-fastapi # Both
```

**Expected warnings**: Logfire API unreachable (proxy restrictions) - non-blocking.

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
- PydanticAI with Graph Beta API for multi-agent AI
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
│   └── setup_remote.sh        # Session-start hook script
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
pytest -m live api/src/tests/test_openphone_tools.py  # Live OpenPhone API tests (needs keys)

# Database migrations
cd api && uv run alembic upgrade head
cd api && uv run alembic revision --autogenerate -m "description"

# Add Shadcn component
cd apps/web-react-router && pnpm dlx @shadcn/ui@latest add <component>
```

### Testing Conventions

- **Default `pytest` run** excludes tests marked `live` (see `pytest.ini` `addopts`).
- **`live` marker**: Tests that hit real third-party APIs (ClickUp, OpenPhone, etc.). Run explicitly with `pytest -m live <file>`. Require API keys in `.env`.
- **Smoke tests** (`TestSmoke` classes): Fast import/wiring checks that verify modules load correctly and components are connected (e.g., agent has history processors wired, sub-agent models configured). No API keys needed.
- **Unit tests**: Mock all external calls. Use realistic tool result data matching actual output formats (ClickUp task dumps, Gmail search results, etc.), not dummy strings.
- **Test location**: `api/src/tests/`

---

## Coding Conventions

> Full rules in `.cursor/rules/*.mdc`

### General
- Respect existing code patterns; don't remove functionality without asking
- Use Tailwind only (no manual CSS)
- Prefer absolute imports
- Never modify `.env` directly

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
- **Database issues?** Verify PostgreSQL is running: `pg_isready -h localhost`

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
