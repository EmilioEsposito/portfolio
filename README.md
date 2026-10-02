# Emilio Esposito · Portfolio & operational AI

Source for [eesposito.com](https://eesposito.com), its public AI demos, and the operational systems I build for **Sernia Capital**, the rental real estate business I co-founded and help run.

I'm an AI engineering leader and hands-on builder. This repository shows how I connect model reasoning to working software: persistent context, business integrations, event-driven workflows, human review, and application controls around consequential actions. It contains my personal and Sernia Capital work; employer code is outside this repository.

**Start with the [portfolio](https://eesposito.com), explore the systems below, or try the [public agent demo](https://eesposito.com/multi-agent-chat).**

## Systems to explore

| System | What it does | Walkthrough and source |
| --- | --- | --- |
| **SerniaAI operations agent** | Works across authenticated web chat, team SMS, and scheduled runs. Uses shared memory and business tools to coordinate work and follow-ups. | [Walkthrough](https://eesposito.com/systems/sernia-ai) · [Agent](api/src/sernia_ai/agent.py) · [Module guide](api/src/sernia_ai/README.md) |
| **Emergency routing** | Assesses incoming messages for urgent property issues, then routes qualifying alerts through a configured Twilio phone workflow. | [Walkthrough](https://eesposito.com/systems/emergency-routing) · [Assessment and dispatch](api/src/open_phone/escalate.py) · [Decision policy and model modes](api/src/open_phone/README.md) |
| **Leasing follow-ups** | Reviews email threads on a schedule, surfaces missing replies, and extracts tour details for contact and calendar updates. | [Walkthrough](https://eesposito.com/systems/leasing-leads) · [Workflow](api/src/zillow_email/service.py) |
| **Public AI demos** | Routed chat with a visible execution trace, plus an email drafting studio using fictional scenarios. | [Try chat](https://eesposito.com/multi-agent-chat) · [Try email](https://eesposito.com/ai-email-responder) · [Backend guide](api/src/ai_demos/README.md) |

### Operational systems and public access

The first three systems support Sernia Capital operations. Their source is public; access to company records and operational tools is restricted. The public demos use separate, limited tools and a dedicated inference credential. The email studio does not read a mailbox or send email. Request, concurrency, time, and model budgets bound public inference; the dedicated provider key supplies the spending cap. See [public AI controls](api/src/utils/README.md).

SerniaAI keeps shared knowledge and procedures in a persistent, versioned workspace while retaining each conversation's history. It summarizes oversized tool results and compacts older history before model requests. Its tools connect Quo SMS, Gmail, Calendar, Drive, ClickUp, database search, and scheduling; [triggers](api/src/sernia_ai/triggers/README.md) bring web, SMS, and scheduled work into the same agent.

Approval depends on the action and recipient. Standard external SMS/email and calendar writes involving external attendees require human review enforced in tool code. Contact changes and task deletion also require approval; routine internal work can proceed without it. There are deliberate exceptions: [fixed, code-owned reminders](api/src/sernia_ai/messaging/README.md) validate recipient eligibility, and leasing email automation has an operator-controlled approval setting. See the [tool policies](api/src/sernia_ai/tools/README.md) for the exact boundaries.

Emergency routing and leasing follow-ups use narrower structured model decisions. Application code determines which events qualify, which recipients or records are involved, and when downstream actions run. Emergency assessment has bounded retries and records failures; if no successful assessment calls for escalation, it does not place an escalation call. The model assists the team rather than guaranteeing detection or resolving the underlying incident. Tests and local evaluation tools are in the repository; [assessment documentation](api/src/open_phone/README.md) distinguishes synthetic regression cases, live checks, and production telemetry.

## Repository map

| Entry point | Purpose |
| --- | --- |
| [`apps/web-react-router/`](apps/web-react-router/) | Portfolio, public walkthroughs and demos, and authenticated operator UI. Start with [`app/routes/`](apps/web-react-router/app/routes/) and [`app/root.tsx`](apps/web-react-router/app/root.tsx). |
| [`api/index.py`](api/index.py) | FastAPI application, router mounting, and startup lifecycle. |
| [`api/src/sernia_ai/`](api/src/sernia_ai/) | Operations agent, memory, history processing, business tools, triggers, and approval flow. |
| [`api/src/open_phone/`](api/src/open_phone/) / [`api/src/zillow_email/`](api/src/zillow_email/) | SMS ingestion and escalation / leasing email workflows. |
| [`api/src/ai_demos/`](api/src/ai_demos/) | Public chat agents and routing; email studio endpoints live in [`api/src/google/gmail/`](api/src/google/gmail/). |
| [`api/src/database/`](api/src/database/) / [`api/src/apscheduler_service/`](api/src/apscheduler_service/) | SQLAlchemy models, Alembic migrations, and persistent scheduled jobs. |
| [`apps/sernia_mcp/`](apps/sernia_mcp/README.md) | Separately configured, authenticated MCP service exposing a curated set of operational tools. |
| [`apps/my-expo-app/`](apps/my-expo-app/) | Expo / React Native app. |
| [`api/src/tests/`](api/src/tests/) | Backend tests, including mocked integration coverage and opt-in live checks. |

The primary web app uses **React Router, React, TypeScript, Tailwind, shadcn/ui, and Clerk**. The backend uses **Python, FastAPI, PydanticAI, SQLAlchemy, PostgreSQL, and APScheduler**, with OpenRouter for inference and Logfire for observability. Production runs on Railway with Neon Postgres. Node and pnpm versions are recorded in [`package.json`](package.json); Python dependencies are in [`pyproject.toml`](pyproject.toml) and [`uv.lock`](uv.lock).

## Local development

Run commands from the repository root unless noted. Use Node **22.22+**, the pnpm version pinned in `package.json`, Python **3.11**, [uv](https://docs.astral.sh/uv/), and a development PostgreSQL database. Docker Compose can provide PostgreSQL locally.

### Install and configure

```bash
git clone https://github.com/EmilioEsposito/portfolio.git
cd portfolio
pnpm install --frozen-lockfile
uv sync --frozen -p 3.11
```

The web app and API have separate local configuration files:

| File | Configuration |
| --- | --- |
| `.env` | API database URLs, session secret, inference credentials, and integration credentials. Use [`.env.example`](.env.example) as a starting reference. |
| `apps/web-react-router/.env` | `CLERK_SECRET_KEY` and `VITE_CLERK_PUBLISHABLE_KEY` from your development Clerk instance. Optional `VITE_PORT` and `BACKEND_PORT` default to 5173 and 8000. |

For a fresh clone, create the ignored files and replace placeholders with your own development configuration. The root example is not a complete working configuration: the launcher also checks Google OAuth settings, `OPEN_PHONE_WEBHOOK_SECRET`, and Twilio credentials, among others. Run the checker to see required names without printing secret values:

```bash
pnpm config:check api
pnpm config:check web
```

The full API currently requires its integration configuration even if you are only working on one feature. For web-only work, configure the web service and use `pnpm dev`; backend-backed features still need the API. The default backend test suite uses dummy provider credentials and does not require live third-party accounts.

Keep inference keys server-side. `PORTFOLIO_OPENROUTER_API_KEY` serves operational inference; `PUBLIC_PORTFOLIO_OPENROUTER_API_KEY` is a separate capped key for public demos. Setup details are in the [inference guide](api/src/utils/README.md), [Google integration guide](api/src/google/README.md), and [development configuration guide](scripts/README.md). The latter documents service allowlists, cloud credential resolution, and configuration precedence.

### Prepare the database and run

For local PostgreSQL, the supplied Compose service uses the development database, username, and password `portfolio`. Configure both API database URLs as `postgresql://portfolio:portfolio@localhost:5432/portfolio` and set `DATABASE_REQUIRE_SSL=false`. For a separate Neon development database, use its pooled/unpooled URLs and `DATABASE_REQUIRE_SSL=true`.

```bash
docker compose up -d postgres
# After API configuration checks pass:
.venv/bin/python scripts/dev_config.py api migrate
pnpm dev-with-fastapi
```

Compose also references `apps/sernia_mcp/.env`; if it reports a missing file, initialize that ignored file from the [MCP example](apps/sernia_mcp/.env.example). The MCP service is not needed to run the web app and API.

| Service | Default local URL |
| --- | --- |
| Web app | [localhost:5173](http://localhost:5173) |
| API documentation | [localhost:8000/api/docs](http://localhost:8000/api/docs) |
| API through the web proxy | [localhost:5173/api/docs](http://localhost:5173/api/docs) |

For baseline development data, run `uv run python api/seed_db.py` with the same development database selected; see [seed data](#sanitized-seed-data-dev-environments) below.

`pnpm dev` starts only the web app; `pnpm fastapi-dev` starts only the API. Stop the combined launcher with `Ctrl+C`.

The development launcher keeps background automation **off by default**. `BACKGROUND_AUTOMATION_ENABLED=true` enables startup scheduling explicitly. This setting does not disable manually invoked integration endpoints, which can still call external services. Use development accounts and data. See [scheduler documentation](api/src/schedulers/README.md); APScheduler is active and the older DBOS workflows are disabled.

### Tests and isolated worktrees

For an existing local checkout, use the [worktree lifecycle](docs/WORKTREES.md) when you need an isolated database and collision-safe ports:

```bash
./scripts/worktree-create.sh feature-name
# In the created checkout:
./scripts/worktree-test.sh
```

The provisioner installs dependencies, migrates/seeds the worktree database, and prints its preview URL. The test wrapper uses a separate test database. Cloud sessions already have an isolated checkout; follow [development configuration](scripts/README.md) there.

For a manually configured, disposable test database, the backend CI sequence is:

```bash
uv run alembic upgrade head
uv run python api/seed_db.py
uv run pytest -q
```

These commands use the selected database, so keep it separate from operational data. The default suite excludes `live` tests; external SMS tests must mock sending. See [`pytest.ini`](pytest.ini), [`conftest.py`](conftest.py), and the [CI workflow](.github/workflows/tests.yml) for the test environment.

Frontend checks:

```bash
pnpm --filter web-react-router typecheck
pnpm --filter web-react-router test
pnpm build
```

### Docker and mobile

[`docker-compose.yml`](docker-compose.yml) also defines `fastapi`, `web-react-router`, `my-expo-app`, and `sernia-mcp`. Use it for local container builds with the required environment files present:

```bash
docker compose up -d --build fastapi web-react-router
docker compose down
```

The API container targets the Compose PostgreSQL service; the web container proxies `/api` to `fastapi:8000`. Container configuration differs from the service-scoped development launcher. Keep the default `portfolio` database name unless you also update the API container's database URLs in Compose.

For Expo, configure `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY` and use `pnpm my-expo-app start`. [`app.config.js`](apps/my-expo-app/app.config.js) reads the root `.env`; a physical device needs `CUSTOM_RAILWAY_BACKEND_URL` set to a reachable LAN or tunnel URL, rather than the device's localhost. The [mobile app directory](apps/my-expo-app/) contains its EAS build profiles.

## Sanitized Seed Data (dev environments)

[`api/seed_db.py`](api/seed_db.py) provides baseline contacts, settings, and non-production demo conversations. Maintainers can optionally supply reviewed conversation fixtures through a private Railway bucket. Public contributors do not need bucket access.

```bash
# Maintainer-only: export from an intentionally selected source database.
uv run python scripts/export_seed_fixture.py --limit 10
# Review api/seed_fixtures/agent_conversations.json before uploading.
uv run python scripts/export_seed_fixture.py --upload-only
```

Phone/email redaction and truncation are automatic; names, addresses, and other details in free text still require human review. Keep the fixture out of Git. Avoid the one-step `--upload` option when a review has not occurred.

Seeding can download the fixture in non-production environments when `SEED_BUCKET_ENDPOINT_URL`, `SEED_BUCKET_NAME`, `SEED_BUCKET_ACCESS_KEY_ID`, and `SEED_BUCKET_SECRET_ACCESS_KEY` are configured. Use the bucket's full name from its Credentials tab. Keep these optional values in local secret configuration or the relevant CI/cloud secret store; leave them unset when not using the private fixture.

## Deployment and contributing

[eesposito.com](https://eesposito.com) is the public production site; [dev.eesposito.com](https://dev.eesposito.com) is the development site. Railway hosts the services, and pull requests use preview environments with isolated Neon branches. See the [PR environment guide](.github/PR_ENVIRONMENTS.md) for provisioning and cleanup.

For changes, start with the relevant module guide and [`AGENTS.md`](AGENTS.md), keep the scope focused, and open a pull request with a clear description and relevant validation. Include its web preview URL: `https://react-router-portfolio-pr-<PR_NUMBER>.up.railway.app/`. Preserve the distinction between public demos and authenticated operations, and keep credentials, private memory, and unreviewed business fixtures out of changes.

For collaboration or professional background, visit [eesposito.com](https://eesposito.com) or [get in touch](https://eesposito.com/calendly).
