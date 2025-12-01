# CLAUDE.md - AI Assistant Guide

> **Last Updated**: 2025-12-01
> **Purpose**: Comprehensive guide for AI assistants (Claude, Cursor AI, etc.) working on this codebase

---

## Table of Contents
1. [Repository Overview](#repository-overview)
2. [Tech Stack](#tech-stack)
3. [Directory Structure](#directory-structure)
4. [Development Setup](#development-setup)
   - [Claude Code Cloud Environment](#claude-code-cloud-environment)
5. [Development Workflows](#development-workflows)
6. [Coding Conventions](#coding-conventions)
7. [AI/LLM Integration Architecture](#aillm-integration-architecture)
8. [Testing Guidelines](#testing-guidelines)
9. [Database & Migrations](#database--migrations)
10. [Common Tasks](#common-tasks)
11. [Deployment](#deployment)
12. [Important Files Reference](#important-files-reference)

---

## Repository Overview

### Purpose
A monorepo serving **Sernia Capital LLC**'s rental real estate business with AI-powered automation tools, plus personal portfolio projects for learning and experimentation.

### Philosophy
- **Iterative Development**: Some features start as learning projects, evolve into production tools for the rental business, and may eventually scale into public SaaS products
- **Simplicity First**: Keep things clean, simple, and maintainable
- **Solo Developer Context**: Simple credential management, straightforward patterns

### Key Applications
1. **React Router Web App** (`apps/web-react-router/`) - New primary web interface (migrating from Next.js)
2. **Next.js Web App** (`apps/web/`) - Legacy web interface (being replaced)
3. **FastAPI Backend** (`api/`) - Python API with multi-agent AI, webhooks, scheduling
4. **Expo Mobile App** (`apps/my-expo-app/`) - React Native mobile app (iOS/Android/Web)

---

## Tech Stack

### Frontend
- **Framework**: Next.js 15.2.3 with App Router
- **React**: 19.0.0
- **UI Library**: [Shadcn UI](https://ui.shadcn.com/docs) (30+ components based on Radix UI)
- **Styling**: Tailwind CSS 3.4.15 with dark/light mode support
- **Authentication**: Clerk 6.11.3
- **AI SDK**: Vercel AI SDK (ai@5.0.92) with streaming support
- **State Management**: React hooks, URL search params (nuqs)
- **Database**: @vercel/postgres, @neondatabase/serverless
- **Icons**: Lucide React, Radix Icons
- **Package Manager**: pnpm

### Mobile
- **Framework**: Expo 53.0.7 + React Native 0.79.2
- **Navigation**: Expo Router 5.0.3 (file-based)
- **Authentication**: @clerk/clerk-expo
- **Platform Support**: iOS, Android, Web

### Backend
- **Framework**: FastAPI 0.115.12
- **Python**: 3.11+
- **Server**: Hypercorn 0.17.3 (ASGI)
- **ORM**: SQLAlchemy 2.0.41 with asyncio
- **Database**: Neon Postgres (serverless)
- **Migrations**: Alembic 1.13.1
- **AI Framework**: PydanticAI 1.18.0 with Graph Beta API
- **LLM Provider**: OpenAI (GPT-4o, GPT-4o-mini)
- **Observability**: Logfire 0.11.0
- **Task Scheduling**: APScheduler 3.11.0
- **Package Manager**: uv

### External Integrations
- **OpenPhone**: SMS/voice API
- **Google Workspace**: Gmail, Calendar, Sheets, Drive
- **Twilio**: SMS escalation
- **Clerk**: Authentication (web + mobile)
- **Railway**: Hosting platform
- **ClickUp**: Task management
- **Expo**: Mobile distribution

---

## Directory Structure

```
/
├── .codex/                    # Codex Cloud Agent config
├── .cursor/                   # Cursor IDE rules and config
│   └── rules/                 # AI coding guidelines
│       ├── general.mdc        # General rules for all files
│       ├── fastapi.mdc        # Python/FastAPI rules
│       └── nextjs.mdc         # TypeScript/Next.js rules
├── .github/                   # GitHub templates
├── .vscode/                   # VS Code launch configs
├── api/                       # FastAPI Backend (Python)
│   ├── index.py               # Main FastAPI app
│   ├── src/
│   │   ├── ai/                # AI agents
│   │   │   ├── chat_emilio/   # Portfolio chatbot agent
│   │   │   ├── chat_weather/  # Weather agent
│   │   │   └── multi_agent_chat/  # Multi-agent router (PydanticAI Graph)
│   │   ├── scheduler/         # APScheduler service
│   │   ├── open_phone/        # OpenPhone SMS integration
│   │   ├── google/            # Google Workspace APIs
│   │   │   ├── gmail/         # Email automation
│   │   │   ├── sheets/        # Sheets integration
│   │   │   ├── calendar/      # Calendar events
│   │   │   └── pubsub/        # Gmail webhooks
│   │   ├── zillow_email/      # Zillow email automation
│   │   ├── push/              # Push notifications
│   │   ├── contact/           # Contact management
│   │   ├── user/              # User management
│   │   ├── oauth/             # OAuth flows
│   │   ├── database/          # DB models & migrations
│   │   │   ├── models.py      # SQLAlchemy models
│   │   │   └── migrations/    # Alembic versions (19 migrations)
│   │   ├── utils/             # Shared utilities
│   │   └── tests/             # Pytest tests
│   ├── db_create_migration.sh # Generate new migration
│   └── db_run_migration.sh    # Apply migrations
├── apps/
│   ├── web-react-router/      # React Router v7 Web App (primary)
│   │   ├── app/
│   │   │   ├── routes/        # File-based routes
│   │   │   ├── components/    # React components
│   │   │   └── lib/           # Utilities
│   │   ├── server.js          # Express server with API proxy
│   │   ├── vite.config.ts     # Vite config with dev proxy
│   │   └── react-router.config.ts
│   ├── web/                   # Next.js Web App (legacy)
│   │   ├── app/               # App Router pages
│   │   ├── components/        # React components
│   │   │   └── ui/            # Shadcn components
│   │   ├── hooks/             # Custom hooks
│   │   ├── lib/               # Utilities
│   │   ├── tests/             # Playwright E2E tests
│   │   └── next.config.js     # Next.js config
│   └── my-expo-app/           # React Native Mobile App
│       ├── app/               # Expo Router pages
│       │   ├── (auth)/        # Auth screens
│       │   └── (tabs)/        # Tab navigation
│       └── components/        # RN components
├── packages/                  # Shared packages (pnpm workspace)
│   ├── features/              # Shared features
│   │   ├── scheduler/         # Scheduler components
│   │   └── hello/             # Demo screens
│   └── ui/                    # Cross-platform UI library
│       ├── components/        # ThemedView, ThemedText, etc.
│       └── hooks/             # useColorScheme, useThemeColor
├── prompts/                   # AI prompts & docs
├── AGENTS.md                  # Codex Cloud Agent specific docs
├── README.md                  # General setup guide
├── .env.example               # Environment variables template
├── docker-compose.yml         # Local dev containers
├── package.json               # Root pnpm scripts
├── pnpm-workspace.yaml        # Workspace config
├── pyproject.toml             # Python dependencies
└── roadmap.md                 # Project roadmap
```

---

## Development Setup

### Prerequisites
- **Node.js**: Latest LTS
- **pnpm**: [Install pnpm](https://pnpm.io/installation)
- **Python**: 3.11+
- **uv**: [Install uv](https://github.com/astral-sh/uv#installation)
- **Docker**: For local Postgres (optional)

### Initial Setup

```bash
# 1. Clone the repository
git clone https://github.com/EmilioEsposito/portfolio.git
cd portfolio

# 2. Install JavaScript dependencies
pnpm install

# 3. Setup Python virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 4. Install Python dependencies
uv sync -p python3.11
uv sync --dev -p python3.11  # Include dev dependencies

# 5. Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# 6. Setup local Postgres (optional, see below)
docker compose --env-file .env up -d postgres
cd api && uv run alembic upgrade head

# 7. Start development servers
pnpm dev              # Next.js only
pnpm fastapi-dev      # FastAPI only
pnpm dev-with-fastapi # Both concurrently
```

### Environment Variables

Create `.env` based on `.env.example`:

**Required Variables**:
```bash
# AI
OPENAI_API_KEY=sk-***

# Database (Neon or Local)
DATABASE_URL=postgresql://***  # For Next.js (pooled)
DATABASE_URL_UNPOOLED=postgresql://***  # For FastAPI (direct)
DATABASE_REQUIRE_SSL=false  # Only for local Postgres

# Authentication
CLERK_SECRET_KEY=sk_***
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_***
EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_***

# Admin
ADMIN_PASSWORD_SALT=***
ADMIN_PASSWORD_HASH=***

# Integrations
OPEN_PHONE_API_KEY=***
OPEN_PHONE_WEBHOOK_SECRET=***
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS={}
CLICKUP_API_KEY=***

# Misc
CRON_SECRET=***
SESSION_SECRET_KEY=***
CUSTOM_RAILWAY_BACKEND_URL=http://localhost:8000  # Or Railway URL
```

### Local Postgres Setup

**When to use**: Codespaces or environments without Neon access

```bash
# 1. Update .env
DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"
DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"
DATABASE_REQUIRE_SSL=false

# 2. Start Postgres container
docker compose --env-file .env up -d postgres

# 3. Run migrations
cd api
uv run alembic upgrade head
```

### Claude Code Cloud Environment

**When to use**: Running Claude Code via the web/cloud interface (not local CLI)

Claude Code Cloud has specific constraints that differ from local development:

#### What Works
- Python 3.11 with uv package manager
- Dependency installation via `uv sync`
- JavaScript/Node.js with pnpm
- Git operations
- HTTP/HTTPS connections to allowlisted domains
- OpenAI API calls (via HTTPS)

#### Known Limitations

| Feature | Status | Notes |
|---------|--------|-------|
| Neon PostgreSQL | **NOT SUPPORTED** | DNS resolution fails for `*.neon.tech` hosts |
| Direct PostgreSQL (port 5432) | **NOT SUPPORTED** | TCP connections on non-HTTP ports fail |
| Logfire API | **NOT SUPPORTED** | Blocked by proxy (`logfire-us.pydantic.dev`) |
| Docker | Limited | May not be available |
| Local Postgres | Limited | Depends on Docker availability |

#### Environment Variable Quirk

When environment variables are injected into Claude Code Cloud, they may be wrapped in quotes that must be stripped. Use this Python snippet to clean them:

```python
import os

# List of env vars that may have quote wrapping
env_vars_to_clean = [
    "DATABASE_URL", "DATABASE_URL_UNPOOLED", "OPENAI_API_KEY",
    "CLERK_SECRET_KEY", "DEV_CLERK_WEBHOOK_SECRET", "PROD_CLERK_WEBHOOK_SECRET",
    # Add other secrets as needed
]

for var in env_vars_to_clean:
    value = os.environ.get(var, "")
    if value.startswith('"') and value.endswith('"'):
        os.environ[var] = value[1:-1]
```

#### Running FastAPI in Claude Code Cloud

Due to database connectivity limitations, the full FastAPI app cannot start in Claude Code Cloud because:
1. The lifespan handler requires successful database health checks
2. DNS resolution fails for Neon PostgreSQL hosts

**Workarounds**:
1. Skip database-dependent tests and focus on code review/editing tasks
2. Use the local CLI version of Claude Code for full development workflows
3. Consider adding a `SKIP_DB_HEALTHCHECK` environment variable to allow graceful degradation

#### Recommended Claude Code Cloud Usage

Best suited for:
- Code review and editing
- Documentation updates
- Running tests that don't require database
- Git operations
- Dependency analysis

Not suited for:
- Running the full FastAPI server
- Database migrations
- Integration tests requiring Neon
- Tasks requiring Logfire observability

---

## Development Workflows

### Common Scripts

#### Root Level (package.json)
```bash
pnpm dev                  # Start Next.js dev server (port 3000)
pnpm dev-with-fastapi     # Start both Next.js and FastAPI
pnpm build                # Build Next.js for production
pnpm start                # Start Next.js production server
pnpm lint                 # Run Next.js linter
pnpm test:e2e             # Run Playwright E2E tests
pnpm fastapi-dev          # Start FastAPI dev server (port 8000)
pnpm my-expo-app          # Launch Expo app
pnpm reinstall            # Clean reinstall all dependencies
```

#### Expo Mobile
```bash
pnpm my-expo-app start    # Start Expo dev server
pnpm eas-build-local      # Build locally for iOS
pnpm eas-build-dev        # Build on EAS (development)
pnpm eas-build-prod       # Build on EAS (production)
```

#### Python/FastAPI
```bash
# Always activate venv first!
source .venv/bin/activate

# Run dev server with hot reload
python3 -m hypercorn api.index:app --reload -b 0.0.0.0:8000

# Run tests
pytest                    # All tests
pytest -v -s              # Verbose with stdout
pytest api/src/tests/     # Specific directory

# Database migrations
cd api
uv run alembic upgrade head           # Apply migrations
uv run alembic downgrade -1           # Rollback one
uv run alembic revision --autogenerate -m "description"
```

### Docker Workflows

```bash
# Build and run all services
docker compose --env-file .env up -d --build

# Individual services
docker compose --env-file .env up -d postgres
docker compose --env-file .env up -d fastapi
docker compose --env-file .env up -d nextjs

# Stop and remove containers
docker compose down

# View logs
docker compose logs -f fastapi
docker compose logs -f nextjs
```

### Access URLs

#### Local Development
- **React Router**: http://localhost:5173
- **Next.js (legacy)**: http://localhost:3000
- **FastAPI**: http://localhost:8000/api/docs

#### Dev Environment
- **React Router**: https://dev.eesposito.com
- **FastAPI (direct)**: https://dev-eesposito-fastapi.up.railway.app/api/docs
- **FastAPI (via proxy)**: https://dev.eesposito.com/api/docs

#### Production
- **Next.js**: https://eesposito.com
- **FastAPI (direct)**: https://eesposito-fastapi.up.railway.app/api/docs
- **FastAPI (via proxy)**: https://eesposito.com/api/docs

---

## Coding Conventions

### General Rules (All Files)

> **Source**: `.cursor/rules/general.mdc`

1. **Respect Existing Code**: Don't remove comments or commented-out code without asking
2. **Suggest, Don't Force**: Offer stylistic improvements only if significantly better; otherwise follow existing patterns
3. **No Manual CSS**: Use Tailwind only; no manual CSS tweaking
4. **Simple Credentials**: Solo developer context - keep auth simple
5. **Never Remove Functionality**: Don't remove imports, routes, or features without explicit permission
6. **Production-Ready Only**: Only suggest code safe for main branch and production
7. **Absolute Imports**: Prefer absolute imports over relative for easier refactoring
8. **No Unrelated Changes**: Stick to the task at hand
9. **Never Modify .env**: Suggest edits only
10. **Shadcn UI**: Use `pnpm dlx @shadcn/ui@latest add <component>` (old way gives errors)

### TypeScript/Next.js Rules

> **Source**: `.cursor/rules/nextjs.mdc`

#### Key Principles
- Write concise, technical TypeScript with accurate examples
- Use functional and declarative patterns; **avoid classes**
- Prefer iteration and modularization over duplication
- Descriptive variable names with auxiliary verbs: `isLoading`, `hasError`
- File structure: exported component, subcomponents, helpers, static content, types
- **Support light and dark mode** (tailwind.config.js)

#### Naming Conventions
- **Directories**: lowercase with dashes (`components/auth-wizard`)
- **Components**: Named exports

#### TypeScript Usage
- Use TypeScript for all code
- **Prefer interfaces over types**
- **Avoid enums; use maps instead**
- Functional components with TypeScript interfaces

#### Syntax and Formatting
- Use `function` keyword for pure functions
- Avoid unnecessary curly braces in conditionals
- Use declarative JSX

#### UI and Styling
- Use **Shadcn UI, Radix, and Tailwind**
- Responsive design with Tailwind CSS
- **Mobile-first approach**

#### Performance Optimization
- **Minimize** `use client`, `useEffect`, `setState`
- **Favor React Server Components (RSC)**
- Wrap client components in Suspense with fallback
- Dynamic loading for non-critical components
- Optimize images: WebP format, size data, lazy loading

#### Key Conventions
- Use **nuqs** for URL search parameter state management
- Optimize Web Vitals (LCP, CLS, FID)
- **Limit `use client`**:
  - Favor server components and Next.js SSR
  - Use only for Web API access in small components
  - **Avoid for data fetching or state management**

### Python/FastAPI Rules

> **Source**: `.cursor/rules/fastapi.mdc`

#### Key Principles
- Write concise, technical responses with accurate Python examples
- Use functional, declarative programming; **avoid classes where possible**
- Prefer iteration and modularization over duplication
- Descriptive variable names with auxiliary verbs: `is_active`, `has_permission`
- Lowercase with underscores: `routers/user_routes.py`
- Favor named exports
- Use **RORO pattern** (Receive an Object, Return an Object)

#### Python/FastAPI Specifics
- Use `def` for pure functions, `async def` for async operations
- **Type hints for all function signatures**
- Prefer **Pydantic models over raw dictionaries**
- File structure: exported router, sub-routes, utilities, static content, types

#### Error Handling
- **Prioritize error handling and edge cases**:
  - Handle errors at the beginning of functions
  - Use **early returns** for error conditions
  - Place happy path last
  - Avoid unnecessary else; use if-return pattern
  - Use **guard clauses** for preconditions
  - Implement proper error logging
  - Use custom error types for consistency

#### Dependencies
- FastAPI
- Pydantic v2
- Async database libraries (asyncpg)
- SQLAlchemy 2.0

#### FastAPI-Specific Guidelines
- Use functional components and Pydantic models
- Declarative route definitions with return type annotations
- Use `def` for sync, `async def` for async
- Minimize `@app.on_event`; prefer **lifespan context managers**
- Use middleware for logging, monitoring, optimization
- **HTTPException for expected errors**
- Middleware for unexpected errors
- Pydantic's BaseModel for validation

#### Performance Optimization
- **Minimize blocking I/O**; use async for database and external APIs
- Implement caching (Redis, in-memory)
- Optimize serialization with Pydantic
- Lazy loading for large datasets

#### Key Conventions
1. Rely on **dependency injection** for state management
2. Prioritize API performance metrics
3. **Limit blocking operations**:
   - Favor async, non-blocking flows
   - Use dedicated async functions
   - Clear route and dependency structure

---

## AI/LLM Integration Architecture

### Framework: PydanticAI

**Version**: 1.18.0
**Documentation**: [PydanticAI Docs](https://ai.pydantic.dev/)

#### Why PydanticAI?
- **Type-safe**: Built on Pydantic v2 for robust validation
- **Streaming**: Native support for streaming responses
- **Tool Use**: Plain and context-aware tool definitions
- **Graph API**: Multi-agent routing with Graph Beta
- **Vercel AI SDK**: Direct adapter for UI streaming
- **Observability**: Integrated with Logfire

### Multi-Agent System Architecture

#### Location
`api/src/ai/multi_agent_chat/`

#### Components

**1. Router Agent** (`decision_agent.py`)
- **Model**: GPT-4o-mini (fast, cost-effective)
- **Purpose**: Analyzes user messages and routes to specialized agents
- **Output**: Structured `RoutingDecision` with agent name
- **Retries**: 2 attempts on failure

```python
class AgentName(str, Enum):
    emilio = "emilio"  # Portfolio/career queries
    weather = "weather"  # Weather queries

router_agent = Agent(
    model=OpenAIChatModel("gpt-4o-mini"),
    system_prompt="You are a routing agent...",
    output_type=RoutingDecision,
    retries=2
)
```

**2. Emilio Portfolio Agent** (`chat_emilio/agent.py`)
- **Model**: GPT-4o (high quality for personal branding)
- **Purpose**: Answers questions about Emilio's portfolio, skills, projects
- **Tools**:
  - `read_emilio_linkedin_profile()` - Fetches LinkedIn PDF
  - `read_emilio_portfolio_website()` - Fetches homepage HTML
  - `get_emilio_links()` - Returns links (GitHub, articles, interviews)
- **Data Sources**:
  - LinkedIn profile PDF
  - LinkedIn skills PDF
  - Portfolio website (eesposito.com)
  - Published articles (LegalZoom AI launch)
  - Interview transcripts (Search CIO)

**3. Weather Agent** (`chat_weather/agent.py`)
- **Purpose**: Handles weather-related queries
- **Tools**: External weather API integration

**4. Graph-Based Router** (`graph.py`)
- **Framework**: PydanticAI Graph Beta API
- **Flow**:
  1. `route_message` - Router agent decides
  2. Decision node - Branches to agent
  3. `run_emilio_agent` or `run_weather_agent`
  4. Return response
- **State Management**:
  - Message history
  - Agent selection tracking
  - Vercel AI streaming mode support

```python
g = GraphBuilder(
    state_type=MultiAgentState,
    input_type=MultiAgentInput,
    output_type=MultiAgentOutput
)

g.add_node("route_message", route_message_node)
g.add_node("run_emilio_agent", run_emilio_agent_node)
g.add_node("run_weather_agent", run_weather_agent_node)
```

### Vercel AI SDK Integration

#### Endpoint
`POST /api/ai/multi-agent-chat` (`multi_agent_chat/routes.py`)

#### Flow
1. Receive Vercel AI SDK formatted request
2. Extract latest message
3. Run graph with `vercel_ai` mode
4. Stream response using Vercel Data Stream Protocol
5. Return SSE stream

#### Headers
```
Content-Type: text/event-stream
x-vercel-ai-ui-message-stream: v1
X-Accel-Buffering: no
Cache-Control: no-cache, no-transform
```

#### Example Usage (Frontend)
```typescript
import { useChat } from 'ai/react'

const { messages, input, handleSubmit } = useChat({
  api: '/api/ai/multi-agent-chat'
})
```

### Observability with Logfire

**Setup** (`api/index.py`):
```python
logfire.configure(environment=os.getenv('RAILWAY_ENVIRONMENT_NAME', 'local'))
logfire.instrument_pydantic_ai()
logfire.instrument_httpx()
logfire.instrument_asyncpg()
logfire.instrument_fastapi(app)
```

**What's Tracked**:
- All AI agent runs
- HTTP requests (internal and external)
- Database queries
- FastAPI endpoint performance
- Slack alerts on new chat messages

### AI-Powered Features

#### 1. Multi-Agent Chat (`/multi-agent-chat`)
- Dynamic routing between specialized agents
- Streaming responses
- Message history support

#### 2. Email Escalation (`open_phone/escalate.py`)
- Analyzes incoming SMS for urgency
- Decides whether to escalate to Twilio
- Uses LLM for sentiment analysis

#### 3. Zillow Email Automation (`zillow_email/`)
- Extracts showing requests from emails
- Generates personalized responses
- Creates contacts and calendar events
- Schedules follow-ups

#### 4. APScheduler Notifications (`scheduler/`)
- AI-generated email content
- Scheduled SMS based on user behavior
- Error notifications with context

### Adding a New Agent

**Step 1**: Create agent directory
```bash
mkdir -p api/src/ai/my_new_agent
touch api/src/ai/my_new_agent/__init__.py
touch api/src/ai/my_new_agent/agent.py
touch api/src/ai/my_new_agent/routes.py
```

**Step 2**: Define agent (`agent.py`)
```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

my_agent = Agent(
    model=OpenAIChatModel("gpt-4o-mini"),
    system_prompt="You are a helpful assistant for...",
    retries=2
)

@my_agent.tool_plain
async def my_tool() -> str:
    """Tool description for LLM."""
    return "Tool result"
```

**Step 3**: Create routes (`routes.py`)
```python
from fastapi import APIRouter
from pydantic import BaseModel
from .agent import my_agent

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

@router.post("/my-agent/chat")
async def chat(request: ChatRequest):
    result = await my_agent.run(request.message)
    return {"response": result.data}
```

**Step 4**: Register router (`api/index.py`)
```python
from api.src.ai.my_new_agent.routes import router as my_agent_router
app.include_router(my_agent_router, prefix="/api")
```

**Step 5**: Add to multi-agent router (if needed)
- Update `AgentName` enum in `decision_agent.py`
- Add node to graph in `graph.py`
- Update router agent system prompt

---

## Testing Guidelines

### Backend Testing (Pytest)

#### Configuration
**File**: `pytest.ini`

```ini
[pytest]
python_files = test_*.py *_test.py *.py
python_functions = test_*
addopts = -s -v --ignore=api/src/database/migrations
pythonpath = .
testpaths = api
asyncio_mode = strict
log_cli = True
log_cli_level = DEBUG
```

#### Test Locations
- `api/src/tests/` - Dedicated test directory
- Module files with embedded tests (e.g., `routes.py`, `agent.py`)

#### Running Tests
```bash
# Always activate venv first!
source .venv/bin/activate

# All tests
pytest

# Verbose with stdout
pytest -v -s

# Specific directory
pytest api/src/tests/

# Specific file
pytest api/src/ai/multi_agent_chat/test_multi_agent_chat_vercel.py

# Run single test
pytest api/src/tests/test_open_phone.py::test_send_message
```

#### Async Testing
```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result == expected
```

#### Common Fixtures
**File**: `api/src/tests/conftest.py`

```python
@pytest.fixture
async def async_client():
    """Async HTTP client for testing endpoints."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
async def db_session():
    """Test database session."""
    async with get_db() as session:
        yield session
```

### Frontend Testing (Playwright)

#### Configuration
**File**: `apps/web/playwright.config.ts`

```typescript
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
})
```

#### Running Tests
```bash
# All E2E tests
pnpm test:e2e

# With UI
pnpm test:e2e:ui

# Specific test
pnpm test:e2e tests/example.spec.ts

# Debug mode
pnpm test:e2e --debug
```

#### Test Structure
```typescript
import { test, expect } from '@playwright/test'

test('homepage has title', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle(/Portfolio/)
})
```

### Testing Best Practices

1. **Test Isolation**: Each test should be independent
2. **Async/Await**: Use properly for async operations
3. **Fixtures**: Reuse common setup via fixtures
4. **Assertions**: Be specific with assertions
5. **Error Messages**: Include helpful error messages
6. **Coverage**: Aim for critical paths, not 100% coverage
7. **CI/CD**: All tests must pass before merge

---

## Database & Migrations

### Database: Neon Postgres

**Provider**: [Neon](https://neon.tech)
**Type**: Serverless PostgreSQL
**Features**: Autoscaling, branching, pooling

#### Connection Strings
```bash
# Pooled (for Next.js)
DATABASE_URL=postgresql://user:pass@project.pooler.region.aws.neon.tech/neondb

# Direct (for FastAPI/Alembic)
DATABASE_URL_UNPOOLED=postgresql://user:pass@project.region.aws.neon.tech/neondb
```

### ORM: SQLAlchemy 2.0

#### Models Location
`api/src/database/models.py`

#### Key Models
- **User**: Clerk user sync
- **Contact**: OpenPhone contacts with JSONB data
- **Email**: Gmail messages
- **GoogleOAuthToken**: OAuth credentials
- **PushNotificationToken**: Expo push tokens
- **OpenPhoneEvent**: SMS webhook events

#### Model Example
```python
from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=False)
    name = Column(String)
    email = Column(String)
    open_phone_contact = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```

### Migrations: Alembic

#### Configuration
**File**: `alembic.ini`
**Versions**: `api/src/database/migrations/versions/` (19 migrations)

#### Common Commands
```bash
cd api

# Activate venv
source .venv/bin/activate

# Apply all pending migrations
uv run alembic upgrade head

# Rollback one migration
uv run alembic downgrade -1

# Create new migration (auto-detect changes)
uv run alembic revision --autogenerate -m "Add column to users table"

# Create blank migration
uv run alembic revision -m "Custom migration"

# View current version
uv run alembic current

# View migration history
uv run alembic history
```

#### Helper Scripts
```bash
# Create migration
./api/db_create_migration.sh "migration description"

# Run migrations
./api/db_run_migration.sh
```

#### Migration Best Practices

1. **Always auto-generate first**: `alembic revision --autogenerate`
2. **Review generated migrations**: Auto-generate isn't perfect
3. **Test migrations**: Run upgrade/downgrade locally
4. **Add indexes**: For foreign keys and frequently queried columns
5. **Default values**: Provide for new non-nullable columns
6. **Batch operations**: Use for large tables
7. **Reversible**: Always implement `downgrade()`
8. **One purpose**: Each migration should do one thing

#### Example Migration
```python
"""Add status column to contacts

Revision ID: abc123
Revises: def456
Create Date: 2025-11-27

"""
from alembic import op
import sqlalchemy as sa

revision = 'abc123'
down_revision = 'def456'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('contacts',
        sa.Column('status', sa.String(), nullable=True, server_default='active')
    )
    op.create_index('ix_contacts_status', 'contacts', ['status'])

def downgrade():
    op.drop_index('ix_contacts_status', 'contacts')
    op.drop_column('contacts', 'status')
```

---

## Common Tasks

### 1. Adding a Shadcn UI Component

```bash
cd apps/web
pnpm dlx @shadcn/ui@latest add <component-name>

# Examples
pnpm dlx @shadcn/ui@latest add button
pnpm dlx @shadcn/ui@latest add dialog
pnpm dlx @shadcn/ui@latest add table
```

Components are added to `apps/web/components/ui/`.

### 2. Creating a New Next.js Page

```bash
cd apps/web/app
mkdir my-new-page
touch my-new-page/page.tsx
```

**page.tsx**:
```typescript
export default function MyNewPage() {
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-2xl font-bold">My New Page</h1>
    </div>
  )
}
```

Access at: `http://localhost:3000/my-new-page`

### 3. Creating a New FastAPI Endpoint

**Step 1**: Create module directory
```bash
mkdir -p api/src/my_feature
touch api/src/my_feature/__init__.py
touch api/src/my_feature/routes.py
touch api/src/my_feature/service.py
touch api/src/my_feature/schema.py
```

**Step 2**: Define schemas (`schema.py`)
```python
from pydantic import BaseModel

class MyRequest(BaseModel):
    name: str
    value: int

class MyResponse(BaseModel):
    result: str
    processed: bool
```

**Step 3**: Implement service (`service.py`)
```python
async def process_request(name: str, value: int) -> dict:
    # Business logic here
    return {"result": f"Processed {name}", "processed": True}
```

**Step 4**: Create routes (`routes.py`)
```python
from fastapi import APIRouter, HTTPException
from .schema import MyRequest, MyResponse
from .service import process_request

router = APIRouter()

@router.post("/my-feature", response_model=MyResponse)
async def my_endpoint(request: MyRequest):
    try:
        result = await process_request(request.name, request.value)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 5**: Register router (`api/index.py`)
```python
from api.src.my_feature.routes import router as my_feature_router
app.include_router(my_feature_router, prefix="/api")
```

Access at: `http://localhost:8000/api/my-feature`

### 4. Adding a Database Model

**Step 1**: Define model in `api/src/database/models.py`
```python
class MyModel(Base):
    __tablename__ = "my_models"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**Step 2**: Generate migration
```bash
cd api
uv run alembic revision --autogenerate -m "Add my_models table"
```

**Step 3**: Review migration in `api/src/database/migrations/versions/`

**Step 4**: Apply migration
```bash
uv run alembic upgrade head
```

### 5. Scheduling a Task with APScheduler

```python
from api.src.scheduler.service import schedule_email, schedule_sms
from datetime import datetime, timedelta

# Schedule email
run_time = datetime.now() + timedelta(hours=1)
await schedule_email(
    subject="Reminder",
    body="Your appointment is tomorrow",
    recipient="user@example.com",
    run_date=run_time
)

# Schedule SMS
await schedule_sms(
    message="Your showing is in 30 minutes",
    recipient="+15551234567",
    run_date=run_time
)
```

### 6. Sending Push Notifications

```python
from api.src.push.service import send_push_to_user

await send_push_to_user(
    user_id="user_123",
    title="New Message",
    body="You have a new message from John",
    data={"message_id": "msg_456"}
)
```

### 7. Working with OpenPhone SMS

```python
from api.src.open_phone.service import send_message

await send_message(
    phone_number="+15551234567",
    message="Hello from Sernia Capital!"
)
```

### 8. Adding Environment Variables

**Step 1**: Add to `.env.example` with placeholder
```bash
NEW_API_KEY=****
```

**Step 2**: Add to `.env` with actual value
```bash
NEW_API_KEY=sk_live_abc123...
```

**Step 3**: Access in Python
```python
import os
api_key = os.getenv("NEW_API_KEY")
```

**Step 4**: Access in Next.js
```typescript
// For public variables (prefix with NEXT_PUBLIC_)
const apiUrl = process.env.NEXT_PUBLIC_API_URL

// For server-side only
const secret = process.env.NEW_API_KEY  // Only in Server Components or API routes
```

### 9. Running Database Queries

#### In FastAPI
```python
from api.src.database.database import get_db
from api.src.database.models import Contact
from sqlalchemy import select

async def get_contact_by_phone(phone: str):
    async with get_db() as session:
        query = select(Contact).where(Contact.phone == phone)
        result = await session.execute(query)
        return result.scalar_one_or_none()
```

#### In Next.js (Server Component)
```typescript
import { sql } from '@vercel/postgres'

export default async function MyPage() {
  const { rows } = await sql`SELECT * FROM contacts WHERE active = true`

  return (
    <div>
      {rows.map(row => <div key={row.id}>{row.name}</div>)}
    </div>
  )
}
```

### 10. Debugging Tips

#### Backend
```python
# Add print statements (captured in console)
print(f"Debug: {variable}")

# Use Logfire for structured logging
import logfire
logfire.info("Processing request", user_id=user_id, data=data)

# Pytest with output
pytest -v -s  # -s shows print statements
```

#### Frontend
```typescript
// Console logging
console.log('Debug:', variable)

// React DevTools for component state
// Vercel toolbar for environment info
```

---

## Deployment

### Platform: Railway

**Project**: [Portfolio on Railway](https://railway.com/project/73eb837a-ba86-4899-992c-cefd0c22b91f)

### Environments

1. **Local**: Development on laptop
2. **Dev**: Testing environment (dev.eesposito.com)
3. **Production**: Live site (eesposito.com)
4. **PR Environments**: Temporary environments per pull request

### PR Environment Database Branching

Each pull request automatically gets its own isolated Neon database branch and Railway environment. This is managed by `.github/workflows/neon_workflow.yml`.

#### How It Works

1. **On PR open/synchronize**:
   - Creates a Neon database branch named `preview/pr-{number}-{branch-name}`
   - Finds the corresponding Railway PR environment (`pr-{number}` or `portfolio-pr-{number}`)
   - Validates all required environment variables are set
   - Updates `DATABASE_URL`, `DATABASE_URL_UNPOOLED`, and `INFORMATIONAL_NEON_BRANCH_NAME` in Railway
   - Railway automatically redeploys when environment variables are updated
   - Railway automatically runs Alembic migrations during predeploy (configured in `api/railway_fastapi.json`)

2. **On PR close**:
   - Deletes the Neon database branch
   - Railway automatically cleans up the PR environment

#### Required GitHub Secrets/Variables

| Type | Name | Description |
|------|------|-------------|
| Secret | `NEON_API_KEY` | Neon API key for branch management |
| Secret | `RAILWAY_GHA_TOKEN` | Railway Account Token (not Project Token) for GitHub Actions |
| Variable | `NEON_PROJECT_ID` | Neon project ID |
| Variable | `RAILWAY_PROJECT_ID` | Railway project ID |
| Variable | `RAILWAY_FASTAPI_SERVICE_ID` | Railway FastAPI service ID |

#### Workflow File
**Location**: `.github/workflows/neon_workflow.yml`

```yaml
# Triggers on PR events: opened, reopened, synchronize, closed
# Creates Neon branch → Finds Railway env → Validates vars → Updates DB URLs
# Railway auto-redeploys when env vars change, then runs migrations via predeploy hook
```

#### Railway Environment Variables Set by Workflow

The workflow sets three environment variables in the Railway PR environment:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Pooled connection URL (for application use) |
| `DATABASE_URL_UNPOOLED` | Direct connection URL (for migrations) |
| `INFORMATIONAL_NEON_BRANCH_NAME` | The Neon branch name (e.g., `preview/pr-42-feat-new-feature`) |

All variables are validated before being set. If any required variable is missing, the workflow fails with an error.

#### Database Migrations
Migrations run automatically during Railway's predeploy phase (configured in `api/railway_fastapi.json`):
- Predeploy command: `source .venv/bin/activate && alembic upgrade head`
- Runs after build completes but before the application starts
- Uses the `DATABASE_URL_UNPOOLED` environment variable set by the workflow
- Triggered automatically when Railway redeploys after environment variable updates

#### Neon Branch Naming
- Pattern: `preview/pr-{PR_NUMBER}-{branch-name}`
- Example: `preview/pr-42-feat-new-feature`
- Auto-expires: 14 days after creation
- Branch name is stored in Railway as `INFORMATIONAL_NEON_BRANCH_NAME` for reference

#### Railway Environment Matching
- Railway PR environments are named `pr-{number}`, `pr-{number}-{hash}`, `portfolio-pr-{number}`, or `portfolio-pr-{number}-{hash}`
- The workflow retries up to 6 times (10s apart) to find the environment
- If not found after all retries, the workflow fails with a detailed error message
- Environment variable updates trigger an automatic Railway redeploy

#### Debugging PR Environments

```bash
# Check Neon branches
# Go to Neon Console → Project → Branches

# Check Railway environments
# Go to Railway Dashboard → Project → Environments

# View workflow logs
# Go to GitHub → Actions → "Create/Delete Branch for Pull Request"
```

### Railway Services

#### Next.js Web App
- **Build**: `pnpm build`
- **Start**: `pnpm start`
- **Port**: 3000
- **Domain**: eesposito.com (prod), dev.eesposito.com (dev)

#### FastAPI Backend
- **Build**: `uv sync -p python3.11`
- **Start**: `python3 -m hypercorn api.index:app -b 0.0.0.0:8000`
- **Port**: 8000
- **Domain**: eesposito-fastapi.up.railway.app (prod)

#### Neon Postgres
- Managed separately on Neon platform
- Connection strings in Railway environment variables

### Deployment Workflow

#### Automatic Deployments
- **Dev branch** → Auto-deploys to dev environment
- **Main branch** → Auto-deploys to production

#### Manual Deployment
```bash
# Via Railway CLI
railway login
railway link  # Link to project
railway up    # Deploy current code
railway logs  # View logs
```

### Environment Variables on Railway

**Management**:
1. Navigate to project on Railway dashboard
2. Select environment (dev/production)
3. Select service (nextjs/fastapi)
4. Go to Variables tab
5. Add/edit variables

**Best Practice**: Keep `.env.example` updated for reference

### Database Migrations on Railway

#### Automatic (Recommended)
Add to FastAPI start command:
```bash
uv run alembic upgrade head && python3 -m hypercorn api.index:app -b 0.0.0.0:8000
```

#### Manual
```bash
railway run --service fastapi sh
cd api
uv run alembic upgrade head
exit
```

### Monitoring

#### Logfire
- All production logs sent to Logfire
- Environment tag: `RAILWAY_ENVIRONMENT_NAME`
- Slack alerts on errors

#### Railway Metrics
- CPU, memory, network usage
- Deployment history
- Build logs

---

## Important Files Reference

### Configuration Files

| File | Purpose |
|------|---------|
| `package.json` | Root pnpm scripts and workspace config |
| `pnpm-workspace.yaml` | Defines workspace packages |
| `pyproject.toml` | Python dependencies (uv) |
| `alembic.ini` | Database migration config |
| `docker-compose.yml` | Local container orchestration |
| `pytest.ini` | Python test configuration |
| `apps/web/next.config.js` | Next.js configuration |
| `apps/web/tailwind.config.js` | Tailwind CSS theme |
| `apps/web/playwright.config.ts` | E2E test configuration |
| `tsconfig.json` | Root TypeScript config |
| `.env.example` | Environment variables template |
| `.gitignore` | Git ignore patterns |

### Documentation Files

| File | Purpose |
|------|---------|
| `README.md` | General setup guide |
| `CLAUDE.md` | **This file** - AI assistant guide |
| `AGENTS.md` | Codex Cloud Agent specific docs |
| `roadmap.md` | Project roadmap and future plans |
| `.cursor/rules/*.mdc` | Cursor AI coding rules |

### Key Source Files

| File | Purpose |
|------|---------|
| `api/index.py` | FastAPI app entry point |
| `api/src/database/models.py` | SQLAlchemy models |
| `api/src/database/database.py` | Database connection setup |
| `api/src/ai/multi_agent_chat/graph.py` | Multi-agent routing logic |
| `api/src/ai/multi_agent_chat/decision_agent.py` | Router agent |
| `api/src/ai/chat_emilio/agent.py` | Portfolio agent |
| `api/src/scheduler/service.py` | APScheduler task scheduling |
| `api/src/open_phone/service.py` | OpenPhone SMS integration |
| `apps/web/app/layout.tsx` | Root Next.js layout |
| `apps/web/components/ui/` | Shadcn UI components |
| `apps/web/lib/` | Frontend utilities |

---

## Quick Reference Cheat Sheet

### Start Development
```bash
# Terminal 1 - Next.js
pnpm dev

# Terminal 2 - FastAPI
source .venv/bin/activate
pnpm fastapi-dev

# Or both together
pnpm dev-with-fastapi
```

### Run Tests
```bash
# Backend
source .venv/bin/activate
pytest -v -s

# Frontend
pnpm test:e2e
```

### Database Operations
```bash
cd api
uv run alembic upgrade head                              # Apply migrations
uv run alembic revision --autogenerate -m "description"  # Create migration
```

### Add UI Component
```bash
cd apps/web
pnpm dlx @shadcn/ui@latest add <component>
```

### View Logs
```bash
# Railway
railway logs --service fastapi
railway logs --service nextjs

# Docker
docker compose logs -f fastapi
docker compose logs -f nextjs
```

### Common URLs
- Local React Router: http://localhost:5173
- Local Next.js (legacy): http://localhost:3000
- Local FastAPI: http://localhost:8000/api/docs
- Dev: https://dev.eesposito.com
- Production: https://eesposito.com

---

## Additional Resources

### Official Documentation
- [Next.js Docs](https://nextjs.org/docs)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [PydanticAI Docs](https://ai.pydantic.dev/)
- [Shadcn UI Docs](https://ui.shadcn.com/docs)
- [Tailwind CSS Docs](https://tailwindcss.com/docs)
- [Expo Docs](https://docs.expo.dev/)
- [Railway Docs](https://docs.railway.com/)
- [Neon Docs](https://neon.tech/docs)

### Internal Resources
- [Project Roadmap](roadmap.md) - Future plans and progress
- [API Documentation](http://localhost:8000/api/docs) - Swagger UI
- [VS Code Launch Configs](.vscode/launch.json) - Debug configurations

---

## Getting Help

### For AI Assistants
- **Check this file first** for coding conventions and common tasks
- **Review existing code** for patterns before making changes
- **Read error messages carefully** - they often contain the solution
- **Test locally** before suggesting changes
- **Ask questions** if requirements are unclear

### For Human Developers
- Check documentation in this file and README.md
- Review roadmap.md for project context
- Consult .cursor/rules/ for AI assistant guidelines
- Refer to official framework documentation
- Check Railway logs for production issues

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2025-12-01 | Added Claude Code Cloud environment section with limitations and workarounds |
| 1.0.0 | 2025-11-27 | Initial comprehensive documentation |

---

**Maintained by**: Emilio Esposito
**Repository**: [github.com/EmilioEsposito/portfolio](https://github.com/EmilioEsposito/portfolio)
**License**: Private
