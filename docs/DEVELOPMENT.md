# Development guide

Run commands from the repository root unless noted. Before installing dependencies
in an older environment, follow [trust setup](PYTHON_TRUST.md). Use Node from [`.nvmrc`](../.nvmrc), the pnpm version pinned in `package.json`, Python **3.11**, [uv](https://docs.astral.sh/uv/), and a development PostgreSQL database. Docker Compose can provide PostgreSQL locally.

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
| `.env` | API database URLs, session secret, inference credentials, and integration credentials. Use [`.env.example`](../.env.example) as a starting reference. |
| `apps/web-react-router/.env` | `CLERK_SECRET_KEY` and `VITE_CLERK_PUBLISHABLE_KEY` from your development Clerk instance. Optional `VITE_PORT` and `BACKEND_PORT` default to 5173 and 8000. |

For a fresh clone, create the ignored files and replace placeholders with your own development configuration. The root example is not a complete working configuration: the launcher also checks Google OAuth settings, `QUO_WEBHOOK_SECRET`, and Twilio credentials, among others. Run the checker to see required names without printing secret values:

```bash
pnpm config:check api
pnpm config:check web
```

The full API currently requires its integration configuration even if you are only working on one feature. For web-only work, configure the web service and use `pnpm dev`; backend-backed features still need the API. The default backend test suite uses dummy provider credentials and does not require live third-party accounts.

Keep inference keys server-side. `PORTFOLIO_OPENROUTER_API_KEY` serves operational inference; `PUBLIC_PORTFOLIO_OPENROUTER_API_KEY` is a separate capped key for public demos. Setup details are in the [inference guide](../api/src/utils/README.md), [Google integration guide](../api/src/google/README.md), and [development configuration guide](../scripts/README.md). The latter documents service allowlists, cloud credential resolution, and configuration precedence.

### Prepare the database and run

For local PostgreSQL, the supplied Compose service uses the development database, username, and password `portfolio`. Configure both API database URLs as `postgresql://portfolio:portfolio@localhost:5432/portfolio` and set `DATABASE_REQUIRE_SSL=false`. For a separate Neon development database, use its pooled/unpooled URLs and `DATABASE_REQUIRE_SSL=true`.

```bash
docker compose up -d postgres
# After API configuration checks pass:
.venv/bin/python scripts/dev_config.py api migrate
pnpm dev-with-fastapi
```

Compose also references `apps/sernia_mcp/.env`; if it reports a missing file, initialize that ignored file from the [MCP example](../apps/sernia_mcp/.env.example). The MCP service is not needed to run the web app and API.

| Service | Default local URL |
| --- | --- |
| Web app | [localhost:5173](http://localhost:5173) |
| API documentation | [localhost:8000/api/docs](http://localhost:8000/api/docs) |
| API through the web proxy | [localhost:5173/api/docs](http://localhost:5173/api/docs) |

For baseline development data, run `uv run python api/seed_db.py` with the same development database selected; see [seed data](#sanitized-seed-data-dev-environments) below.

`pnpm dev` starts only the web app; `pnpm fastapi-dev` starts only the API. Stop the combined launcher with `Ctrl+C`.

The development launcher keeps background automation **off by default**. `BACKGROUND_AUTOMATION_ENABLED=true` enables startup scheduling explicitly. This setting does not disable manually invoked integration endpoints, which can still call external services. Use development accounts and data. See [scheduler documentation](../api/src/schedulers/README.md); APScheduler is active and the older DBOS workflows are disabled.

### Tests and isolated worktrees

For an existing local checkout, use the [worktree lifecycle](../docs/WORKTREES.md) when you need an isolated database and collision-safe ports:

```bash
./scripts/worktree-create.sh feature-name
# In the created checkout:
./scripts/worktree-test.sh
```

The provisioner installs dependencies, migrates/seeds the worktree database, and prints its preview URL. The test wrapper uses a separate test database. Cloud sessions already have an isolated checkout; follow [development configuration](../scripts/README.md) there.

For a manually configured, disposable test database, the backend CI sequence is:

```bash
uv run alembic upgrade head
uv run python api/seed_db.py
uv run pytest -q
```

These commands use the selected database, so keep it separate from operational data. The default suite excludes `live` tests; external SMS tests must mock sending. See [`pytest.ini`](../pytest.ini), [`conftest.py`](../conftest.py), and the [CI workflow](../.github/workflows/tests.yml) for the test environment.

Frontend checks:

```bash
pnpm --filter web-react-router typecheck
pnpm --filter web-react-router test
pnpm build
```

### Docker and mobile

[`docker-compose.yml`](../docker-compose.yml) also defines `fastapi`, `web-react-router`, `my-expo-app`, and `sernia-mcp`. Use it for local container builds with the required environment files present:

```bash
docker compose up -d --build fastapi web-react-router
docker compose down
```

The API container targets the Compose PostgreSQL service; the web container proxies `/api` to `fastapi:8000`. Container configuration differs from the service-scoped development launcher. Keep the default `portfolio` database name unless you also update the API container's database URLs in Compose.

For Expo, configure `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY` and use `pnpm my-expo-app start`. [`app.config.js`](../apps/my-expo-app/app.config.js) reads the root `.env`; a physical device needs `CUSTOM_RAILWAY_BACKEND_URL` set to a reachable LAN or tunnel URL, rather than the device's localhost. The [mobile app directory](../apps/my-expo-app/) contains its EAS build profiles.

## Sanitized Seed Data (dev environments)

[`api/seed_db.py`](../api/seed_db.py) provides baseline contacts, settings, and non-production demo conversations. Maintainers can optionally supply reviewed conversation fixtures through a private Railway bucket. Public contributors do not need bucket access.

```bash
# Maintainer-only: export from an intentionally selected source database.
uv run python scripts/export_seed_fixture.py --limit 10
# Review api/seed_fixtures/agent_conversations.json before uploading.
uv run python scripts/export_seed_fixture.py --upload-only
```

Phone/email redaction and truncation are automatic; names, addresses, and other details in free text still require human review. Keep the fixture out of Git. Avoid the one-step `--upload` option when a review has not occurred.

Seeding can download the fixture in non-production environments when `SEED_BUCKET_ENDPOINT_URL`, `SEED_BUCKET_NAME`, `SEED_BUCKET_ACCESS_KEY_ID`, and `SEED_BUCKET_SECRET_ACCESS_KEY` are configured. Use the bucket's full name from its Credentials tab. Keep these optional values in local secret configuration or the relevant CI/cloud secret store; leave them unset when not using the private fixture.

