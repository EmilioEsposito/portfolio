# Development configuration

Run `pnpm dev` (web), `pnpm fastapi-dev` (API), or `pnpm dev-with-fastapi`
(both) after `uv sync --frozen` and `pnpm install --frozen-lockfile`.
These commands use `scripts/dev_config.py` and the repository Python environment.
Check configuration without starting a server:

```sh
pnpm config:check          # both services
pnpm config:check api
pnpm config:check web
```

Locally, the launcher reads the existing ignored root `.env` for the API and
`apps/web-react-router/.env` for the web service. It never writes those files or
shell-evaluates their contents. Values are literal (no `${...}` interpolation).
Shell variables override local files for supported service configuration.
The explicit service allowlists in `dev_config.py` are the contract: add an
integration's variable there when enabling it for development.

In Codex cloud (`CODEX_ENVIRONMENT_ID`) and Claude remote sessions
(`CLAUDE_CODE_REMOTE=true`), the launcher reads Railway's **development**
environment, project `73eb837a-ba86-4899-992c-cefd0c22b91f`, service `fastapi` or
`react-router`. It resolves exact names on every invocation and fails rather
than falling back to another environment. Force a source with
`DEV_CONFIG_SOURCE=railway` / `DEV_CONFIG_SOURCE=local`, or pass `--source`.
A Railway token on a laptop does not change the default local source.

Railway mode needs `RAILWAY_MCP_TOKEN` (an account API token with project read
access), `curl`, and HTTPS access to `backboard.railway.com`. Configure the token
in your cloud environment's secret settings, never in chat or tracked files.
The implementation uses [Railway's GraphQL API](https://docs.railway.com/reference/public-api),
so no global CLI login/link state is required. Only read queries are issued.

Cloud callers must supply `DATABASE_URL` and `DATABASE_URL_UNPOOLED` pointing
to their local PostgreSQL database, plus `DATABASE_REQUIRE_SSL=false` when
appropriate. Railway's database URLs and ports are never imported. Railway
mode rejects non-loopback database hosts and URL queries/fragments (which can
redirect libpq connections). Local mode retains support for an explicitly
configured remote development database such as Neon.
Set `PORT` (API, default 8000), `VITE_PORT` (web, default 5173), and
`BACKEND_PORT` (web proxy, default 8000) for concurrent repositories/worktrees.
The existing cloud checkout is already isolated: do not create another worktree
unless requested. Migrate the selected local database with:

```sh
.venv/bin/python scripts/dev_config.py api migrate
```

Service credentials are held in memory and passed only to that service process.
Neither app receives Railway management tokens, GitHub PATs, production Clerk
webhook keys, or arbitrary host credentials. APIs receive no other repo's
injected app keys; web receives no backend database credentials. A small host
baseline retains paths, proxy settings, and trusted CA configuration. The API
uses `.local/workspace` for local agent files and skips GitHub workspace sync
because no PAT is passed.

The launcher disables subsequent Python dotenv loads and Vite's dotenv loading,
so a stale file cannot overwrite the resolved service configuration. Required
checks report names/status only; provider response bodies are suppressed.
Server console output passes through a literal-value redactor (including
multiline values). This is best-effort output hygiene, not a security boundary:
it cannot redact encoded/transformed secrets or stop code with shell access
from inspecting process memory. The launcher does not persist fetched secrets.

Background automation defaults **off locally**, **on for direct Railway
startup**. [Background automation policy](../docs/BACKGROUND_AUTOMATION.md) also defaults hosted
nonproduction off. `BACKGROUND_AUTOMATION_ENABLED=true` explicitly enables it locally;
`false` disables it in hosted deployments. The development launcher defaults
this to false even when fetching Railway credentials. Normal FastAPI lifespan
still initializes the workspace and cleans up stale local data. This switch
controls startup scheduling, not explicit API operations: manually invoking
integration routes can still call real external services. Clerk webhook
verification uses only configured signing keys, returning an error when none
are configured, so local startup never needs a production signing key.

For missing-name diagnostics, supply the named variable in the selected source.
For Railway failures, verify the token binding, project access, and network
allowlist; do not enable verbose curl logging or dump provider responses.
