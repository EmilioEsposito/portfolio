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
│       ├── ai/                # AI agents (chat_emilio, chat_weather, multi_agent_chat)
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

For parallel development, use worktrees to create isolated environments with separate ports and databases.

### Create a Worktree

```bash
./scripts/create-worktree.sh <description>
# Example: ./scripts/create-worktree.sh feature-auth
# Creates: ../portfolio-feature-auth/
```

The script:
1. Creates `git worktree` at `../portfolio-<description>/`
2. Copies `.env` with unique ports (hash-based) and isolated database
3. Installs Python and Node dependencies
4. Copies the database from main (or runs migrations + seed)
5. Adds the folder to `portfolio.code-workspace`

### Remove a Worktree

```bash
./scripts/remove-worktree.sh <description>
```

Drops the database, removes from workspace, and cleans up the git worktree.

### Requirements

- Main worktree must have `docker-compose up -d postgres` running (shared postgres instance)
- Each worktree uses a separate database: `portfolio_<description>`

---

## Common Commands

```bash
# Development servers
pnpm dev                  # React Router only (port 5173)
pnpm fastapi-dev          # FastAPI only (port 8000)
pnpm dev-with-fastapi     # Both concurrently

# Testing
source .venv/bin/activate && pytest -v -s

# Database migrations
cd api && uv run alembic upgrade head
cd api && uv run alembic revision --autogenerate -m "description"

# Add Shadcn component
cd apps/web-react-router && pnpm dlx @shadcn/ui@latest add <component>
```

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

---

## AI Architecture

**Framework**: PydanticAI with Graph Beta API

**Multi-Agent System** (`api/src/ai/multi_agent_chat/`):
- **Router Agent** (GPT-4o-mini): Routes to specialized agents
- **Emilio Agent** (GPT-4o): Portfolio/career questions
- **Weather Agent**: Weather queries

**Endpoints**:
- `POST /api/ai/multi-agent-chat` - Unified routing
- `POST /api/ai/chat-emilio` - Direct Emilio agent
- `POST /api/ai/chat-weather` - Direct weather agent

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
| `api/src/ai/multi_agent_chat/graph.py` | Multi-agent routing |
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
| [`api/README.md`](api/README.md) | FastAPI run commands |
| [`api/src/ai/README.md`](api/src/ai/README.md) | AI agents architecture and testing |
| [`api/src/schedulers/README.md`](api/src/schedulers/README.md) | Scheduler setup (APScheduler active, DBOS disabled) |
| [`.github/PR_ENVIRONMENTS.md`](.github/PR_ENVIRONMENTS.md) | PR database branching workflow |
| [`.cursor/rules/`](.cursor/rules/) | AI coding guidelines (general, fastapi, react) |
| [`roadmap.md`](roadmap.md) | Project roadmap and future plans |

## External Resources

- [React Router Docs](https://reactrouter.com/start/framework/installation)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [PydanticAI Docs](https://ai.pydantic.dev/)
- [Shadcn UI Docs](https://ui.shadcn.com/docs)
