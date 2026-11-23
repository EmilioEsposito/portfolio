# FastAPI backend

The API serves public AI demos and authenticated Sernia operations. `index.py`
mounts routers and owns the application startup lifecycle; feature modules live
under `src/`. Feature modules own their SQLAlchemy models; shared settings and Alembic migrations
live in `src/database/`.

From the repository root, after [development setup](../docs/DEVELOPMENT.md):

```bash
pnpm fastapi-dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

The launcher resolves API configuration without printing credentials. API docs
are available at `http://localhost:8000/api/docs`. Tests live in `src/tests/`;
live provider tests are excluded by default.

Start with the guides for [public demos](src/portfolio/README.md),
[Sernia AI](src/sernia_ai/README.md), [Zillow Leads Agent](src/zillow_leads/README.md),
[rental listings](src/rental_listings/README.md), [SMS](src/open_phone/README.md),
[Google](src/google/README.md), and [schedulers](src/schedulers/README.md).
Production builds use [Dockerfile](Dockerfile) and
[railway_fastapi.json](railway_fastapi.json).

## Packages

`src/portfolio/` holds everything that exists only for eesposito.com. Every
other package is Sernia operations or shared infrastructure that either site's
code may use. [`test_api_package_layout.py`](src/tests/test_api_package_layout.py)
keeps this table's split true: portfolio code imports only itself and
`utils/`, and only `index.py` and tests import portfolio code. The last column
is the URL prefix of each package's routes; "none" means it serves no routes.

| Package | Serves | Mounted at |
|---------|--------|------------|
| `portfolio/` | Portfolio | `/api/ai-demos` (`chat-emilio`, `chat-weather`, `multi-agent-chat`) |
| `adhoc/` | Sernia | none; a one-off Sernia AI token analysis script |
| `agent_conversations/` | Sernia | `/api/agent-conversations`, `/api/sernia-ai` (also `/api/sernia-ai/push`) |
| `clickup/` | Sernia | none; scheduled jobs |
| `contact/` | Sernia | `/api/contacts` |
| `google/` | Sernia | `/api/google` |
| `open_phone/` | Sernia | `/api/open_phone` |
| `prefect/` | Sernia | none; a Prefect example nothing imports |
| `rental_listings/` | Sernia | `/api/rental-listings` |
| `sernia_ai/` | Sernia | `/api/sernia-ai` |
| `showing_bookings/` | Sernia | `/api/showing-bookings` |
| `task_ai/` | Sernia | none; a concept note |
| `team_tasks/` | Sernia | `/api/team-tasks` (the team's Tasks page: open ClickUp and Quo tasks) |
| `zillow_leads/` | Sernia | `/api/zillow-leads`, `/api/sernia-ai/zillow-leads` |
| `apscheduler_service/` | Shared | `/api/apscheduler` |
| `cron.py` | Shared | `/api/cron` |
| `database/` | Shared | none |
| `dbos_service/` | Shared | none; DBOS is disabled |
| `push/` | Shared | `/api/push` (Expo app tokens) |
| `schedulers/` | Shared | `/api/schedulers` |
| `user/` | Shared | `/api/user` |
| `utils/` | Shared | none |
