# Emilio Esposito · Portfolio & operational AI

Source for [eesposito.com](https://eesposito.com), its public AI demos, and the
operations software I build for Sernia Capital, the rental real estate business
I co-founded. This repository contains personal and Sernia Capital work; employer
code is outside it.

## Systems

| System | Purpose | Source |
| --- | --- | --- |
| [SerniaAI](https://eesposito.com/systems/sernia-ai) | Operations agent with persistent context, business tools, and human approval flows. | [Agent guide](api/src/sernia_ai/README.md) |
| [Emergency routing](https://eesposito.com/systems/emergency-routing) | Assesses incoming property messages and routes qualifying alerts through Twilio. | [Assessment and dispatch](api/src/open_phone/README.md) |
| [Zillow Leads Agent](https://eesposito.com/systems/leasing-leads) | Answers from reviewed rental listings, with configurable approval for replies. Showing bookings are planned. | [Lead agent guide](api/src/zillow_leads/README.md) |
| [Public agent demo](https://eesposito.com/multi-agent-chat) | Routed chat with a visible execution trace. | [Demo agents](api/src/ai_demos/README.md) |

Operational records and tools require authentication. Public demos use separate,
limited tools and a capped inference credential; request, concurrency, time, and
model budgets bound public usage. See [public AI controls](api/src/utils/README.md).

The general operations agent connects Quo, Google Workspace, ClickUp, database search,
and scheduling. Approval requirements are enforced in tool code and depend on
the action and recipient. See [tool policies](api/src/sernia_ai/tools/README.md)
and [fixed reminders](api/src/sernia_ai/messaging/README.md) for exceptions.
Emergency assessment assists the team; it does not guarantee detection or
resolution. Its guide separates synthetic regression cases, live checks, and
production telemetry.

## Repository map

| Path | Responsibility |
| --- | --- |
| [`apps/web-react-router/`](apps/web-react-router/README.md) | React Router web app: portfolio, demos, and operator UI. |
| [`api/`](api/README.md) | FastAPI backend, integrations, agents, and scheduled jobs. |
| [`apps/sernia_mcp/`](apps/sernia_mcp/README.md) | Independent MCP service with Clerk OAuth and its own dependencies. |
| [`apps/my-expo-app/`](apps/my-expo-app/README.md) | Expo / React Native client. |
| [`packages/`](packages/) | Shared pnpm workspace packages. |
| [`scripts/`](scripts/README.md) | Development configuration and worktree tooling. |
| [`logfire/`](logfire/) | Versioned [alert snapshots](logfire/alerts/README.md) and [dashboards](logfire/dashboards/README.md). |

The web app uses React, TypeScript, Tailwind, shadcn/ui, and Clerk. The API uses
PydanticAI, SQLAlchemy, PostgreSQL, and APScheduler, with OpenRouter for inference
and Logfire for observability. Production runs on Railway with Neon Postgres.

## Development and verification

Follow the [development guide](docs/DEVELOPMENT.md) for prerequisites, service
configuration, database setup, test commands, Docker, and mobile development.
It includes the [trust setup](docs/PYTHON_TRUST.md) required for older environments.

After configuration:

```bash
pnpm dev-with-fastapi
uv run pytest -q
pnpm --filter web-react-router test
pnpm --filter web-react-router typecheck
pnpm build
```

Backend tests live in [`api/src/tests/`](api/src/tests/), MCP tests in
[`apps/sernia_mcp/tests/`](apps/sernia_mcp/tests/), and web tests in
[`apps/web-react-router/test/`](apps/web-react-router/test/).
Default tests mock third-party services; live tests are opt-in.
[CI](.github/workflows/tests.yml) also checks lint, formatting, and dependency regressions.

### Sanitized Seed Data

Development seeding and optional private fixtures are documented in the
[seed data guide](docs/DEVELOPMENT.md#sanitized-seed-data-dev-environments).
Public contributors do not need private bucket access.

## Contributing and deployment

Read [AGENTS.md](AGENTS.md) and the relevant module guide. Keep PRs focused,
include validation and the web preview URL, and preserve the separation between
public demos and authenticated operations. Do not commit credentials, private
memory, or unreviewed business fixtures.

[PR environments](.github/PR_ENVIRONMENTS.md) use isolated database branches.
The public production site is [eesposito.com](https://eesposito.com); the
development site is [dev.eesposito.com](https://dev.eesposito.com).
See [open work](docs/ROADMAP.md) for exploratory follow-ups.

For professional background or collaboration, visit the
[portfolio](https://eesposito.com) or [get in touch](https://eesposito.com/calendly).
