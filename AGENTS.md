# Repository guide

This monorepo contains eesposito.com, public AI demos, Sernia Capital's operations
API, an Expo app, and a standalone MCP service. Start with [README.md](README.md)
and the README nearest the code you are changing.

## Development

- Use the Node version in `.nvmrc`, pnpm pinned in `package.json`, Python 3.11,
  and uv as required by `pyproject.toml`. Follow [trust setup](docs/PYTHON_TRUST.md)
  before installing dependencies in an older environment.
- Use `pnpm dev`, `pnpm fastapi-dev`, or `pnpm dev-with-fastapi`. These launchers
  resolve service configuration through `scripts/dev_config.py`; use
  `pnpm config:check api` or `pnpm config:check web` for names/status only.
- Local configuration belongs in ignored service `.env` files. Do not modify
  existing `.env` files, print credentials, or source secret files. Cloud launchers
  fetch development credentials into child processes; see [scripts](scripts/README.md).
- Background automation defaults off locally. Enable it only for intentional
  scheduling work; see [automation policy](docs/BACKGROUND_AUTOMATION.md).
- Cloud sessions already have isolated checkouts. Do not create extra worktrees
  unless requested. For local worktree operations, use the lifecycle scripts in
  [the worktree guide](docs/WORKTREES.md); never guess database ownership or
  force-delete checkouts.
- Claude cloud setup and environment-specific limitations are documented in
  [.claude/README.md](.claude/README.md). Keep hooks and their production guards intact.
- Logfire, Sernia AI, Quo, Neon, Railway, and Cloudflare come from claude.ai connectors,
  not `.mcp.json`. Do not add a `.mcp.json` entry for a service that has a connector; see
  [MCP servers and connectors](.claude/README.md#mcp-servers-and-connectors).

## Change boundaries

- Preserve public-demo budgets and the separation between public and operational
  inference credentials. Never fall back from the public key to an operations key.
- Preserve authentication, recipient gates, approval flows, and private workspace
  boundaries. Runtime prompts, tool docstrings, and Pydantic field descriptions
  affect agent behavior; treat them as functional code.
- Keep secrets, private memory, and unreviewed business fixtures out of Git.
  Credential recovery files stay in the primary checkout's ignored
  `.local/credential-recovery/`, never a disposable worktree. Do not print, source,
  copy, commit, or delete them. Use the [custody CLI](scripts/credentials/README.md)
  and read the [playbook](docs/credentials/README.md) and
  [incident tracker](docs/incidents/README.md) before recovery work.
- Keep changes focused. Document non-obvious integration setup in the owning
  module's README instead of repeating it across agent guides.

## Code structure

- Python: typed functions, async I/O, Pydantic boundary models, and
  `HTTPException` for expected API errors. Prefer absolute imports.
- Mount each top-level API module directly in `api/index.py` at `/api/<module>`,
  replacing underscores with hyphens. Sub-routers use relative prefixes.
- Web: functional React components, Tailwind, loaders/actions for route data,
  and `~/` for app imports. Keep responsive and accessible behavior intact.
- APScheduler is active; DBOS workflows remain disabled. See
  [schedulers](api/src/schedulers/README.md) before changing scheduling.
- The MCP service has its own environment and dependencies; follow its
  [scoped guide](apps/sernia_mcp/AGENTS.md).

## Verification

Run checks appropriate to the change; [CI](.github/workflows/tests.yml) is the
full validation contract:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
pnpm test:security
pnpm --filter web-react-router test
pnpm --filter web-react-router typecheck
pnpm build
# Standalone MCP suite, from apps/sernia_mcp:
uv run pytest -q -m 'not live'
```

- The default backend suite needs only a disposable local Postgres, not live
  third-party credentials. Run migrations and seed data first; see
  [development setup](docs/DEVELOPMENT.md). Root `conftest.py` provides dummy AI
  credentials for import-time agent construction.
- Put backend tests in `api/src/tests/`, MCP tests in `apps/sernia_mcp/tests/`,
  and web tests in `apps/web-react-router/test/`. Do not embed tests in service
  modules or broaden pytest collection to all Python files.
- Mock external calls. Mark real provider/LLM tests `live`; they are excluded by
  default. Tests using ignored fixtures must skip when those files are absent.
- Never send real SMS to external contacts from tests. Only explicitly intended
  live `send_internal_sms` tests may use configured internal recipients.
- Keep pytest's session-scoped event loop: module-level asyncpg pools must not
  outlive their loop. Clear module caches in fixtures when tests reuse identifiers.
- For PydanticAI `TestModel`, override native tools with `[]`; use `output_type=str`
  with `custom_output_text`. See `test_sernia_agent_wiring.py`.

## Pull requests and deployment

Use focused PRs with relevant validation and the web preview URL:
`https://react-router-portfolio-pr-<PR_NUMBER>.up.railway.app/`.
Follow [PR environments](.github/PR_ENVIRONMENTS.md) for provisioning and cleanup.
Preserve service deploy configuration and unrelated staged provider changes.
