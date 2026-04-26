# CLAUDE.md — apps/sernia_mcp

> **Last updated**: 2026-04-26

AI-assisted development guide for the **Sernia MCP server** — a self-contained Python service that exposes a curated subset of Sernia tools to remote AI harnesses (Claude Desktop, Claude.ai, ChatGPT app) over HTTP with Clerk OAuth.

This is a **separate Railway service** from the FastAPI monorepo at `api/`. Own venv, own `pyproject.toml`, own `.env`, own deploy lifecycle. The eventual goal is to be liftable to its own private repo for commercialization without surgery.

---

## Quick orientation

```
apps/sernia_mcp/
├── pyproject.toml          ← uv-managed; deps are intentionally minimal
├── railway_sernia_mcp.json ← Railway deploy config (build/start/healthcheck)
├── .env.example
├── src/sernia_mcp/         ← all production code
│   ├── app.py              ← ASGI entrypoint (uvicorn loads this)
│   ├── server.py           ← FastMCP instance + tool registration + Logfire config
│   ├── dev_server.py       ← browser-testable harness with mocked sends
│   ├── config.py           ← env-driven constants; loads ./.env (no parent walking)
│   ├── identity.py         ← acting-user resolver (POC: single user)
│   ├── clients/            ← VENDORED upstream HTTP clients (Gmail, Quo, fuzzy)
│   ├── core/               ← harness-agnostic async tool functions
│   └── tools/              ← @mcp.tool wrappers + approvals app
└── tests/                  ← runs via `uv run pytest` from this dir
```

The `core/` layer (formerly `tool_core/` in the monorepo) has zero dependencies on FastMCP — it's plain async Python with Pydantic results. The `tools/` layer adapts it to FastMCP. This split is deliberate so the same core can be reused by other harnesses (PydanticAI, CLI scripts, etc.) without rewriting.

---

## Common commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Create `.venv/` and install deps. Run after pulling. |
| `uv run pytest -v` | Run the full suite (~3s, no network). |
| `uv run pytest -m live` | Live tests (require API keys; opt-in only). |
| `uv run uvicorn sernia_mcp.app:app --port 8090` | Boot HTTP server with full inbound-request instrumentation. |
| `uv run fastmcp dev apps src/sernia_mcp/dev_server.py` | Browser-based approval-flow harness (mocked sends). |
| `uv run ruff check` | Lint. |
| `uv run ruff format` | Format. |

All commands run from inside `apps/sernia_mcp/`. **Do not** activate the parent monorepo's venv — uv handles its own isolated venv per the `pyproject.toml` here.

### VS Code launchers

`.vscode/launch.json` (at repo root) has three relevant entries:

- **Sernia MCP Server** — boots uvicorn on `127.0.0.1:8090` with `debugpy` attached and `SERNIA_MCP_DISABLE_AUTH=true` set (so the MCP Inspector + curl can connect without OAuth).
- **MCP Inspector (Official)** — `npx @modelcontextprotocol/inspector` with `DANGEROUSLY_OMIT_AUTH=true` (proxy session-token bypass for localhost-only dev). Auto-opens the inspector UI.
- **Sernia MCP — Approval Card Harness** — `fastmcp dev apps src/sernia_mcp/dev_server.py`. Browser inspector pointed at the mocked-sends harness for testing the Prefab approval-card flow.
- **Sernia MCP + Inspector (Compound)** — runs the server + inspector together. One-click "go from nothing to browsing my MCP tools."

---

## Working principles

### AI-first testability
**Every change should be verifiable by Claude without involving the user.** That means:

- New tool? Add a smoke entry to `tests/test_smoke.py` confirming it appears in `/tools/list`, plus a unit test against the core function with mocked clients.
- New approval flow? Mirror the patterns in `tests/test_approvals.py` (in-process `Client(FastMCP(...))` round-trip).
- Tweaking auth or HTTP? Extend `tests/test_http_app.py` — it boots the real ASGI app via `httpx.ASGITransport` and asserts the route is reachable.
- Boot failure? Run `uv run uvicorn sernia_mcp.app:app --port 8765` in the background and curl `/mcp/` with a real MCP `initialize` POST. The server's response includes `"extensions":{"io.modelcontextprotocol/ui":{}}` when Apps support is wired correctly.

The user is **not** the first line of testing. If a regression is only catchable by hand, write the test first. If a test would require API keys, add a `live` marker and skip by default — write a mocked version to cover wiring.

### Tool surface — mirrors Claude Code's Read / Edit / Write
The model-facing context layer in `tools/context.py` deliberately follows the same shape Claude Code uses, so models that already know that pattern transfer it cleanly:

| Tool | Mirrors | Use |
|------|---------|-----|
| `sernia_context()` | (doorway) | Always called first. Returns memory + skill list (no skill bodies). |
| `read_resource(uri)` | `Read` | Full content of `memory://current` or `skill://<name>/SKILL.md`. |
| `edit_resource(uri, old_string, new_string, replace_all=False)` | `Edit` | String-substitution edit; whitespace-exact, atomic, fails on ambiguity. |
| `write_resource(uri, content)` | `Write` | Full overwrite — for new files or large rewrites. |

**Why `read_resource` exists alongside the MCP `resources/read` protocol method**: Claude.ai's connector implementation does **not** surface `resources/read` to the model as a callable. We confirmed this empirically — the model would see a skill URI in `sernia_context`'s response but had no way to fetch the body. The tool form is the workaround. `memory://` and `skill://{name}/SKILL.md` resources are still registered for hosts that DO surface them (Claude Desktop, MCP Inspector); both paths read the same files.

**Why the doorway pattern**: Claude.ai also doesn't reliably surface MCP servers' `instructions` field to the model. Tested with an easter-egg phrase in `instructions` — model couldn't see it. So "base context" can't be passively injected — has to be a tool the model can actively call. `sernia_context`'s description starts with `[ALWAYS CALL THIS FIRST...]` to encourage this; in practice models comply.

### Refresh FastMCP docs once per session
Before doing meaningful work in this repo, fetch:

- https://gofastmcp.com/llms.txt — index of all docs.
- https://gofastmcp.com/deployment/http — production HTTP deployment patterns (we run via uvicorn, see `app.py`).
- https://gofastmcp.com/python-sdk/fastmcp-server-auth-providers-clerk — `ClerkProvider` reference.

FastMCP ships features regularly (Generative UI, Code Mode, Tool Search, middleware ecosystem). Patterns we don't use today may obsolete patterns we do.

### Vendored client policy
`src/sernia_mcp/clients/` contains **vendored** code — slim copies of Gmail / OpenPhone / fuzzy-search helpers from the monorepo. Sync points:

- `clients/google_auth.py` ← `api/src/google/common/service_account_auth.py` (drops FastAPI HTTPException baggage)
- `clients/gmail.py` ← `api/src/google/gmail/service.py` (only send + read primitives; no watch/history webhook plumbing)
- `clients/quo.py` ← `api/src/open_phone/service.py` (only contact read + cache; drops DB sync)
- `clients/_fuzzy.py` ← `api/src/utils/fuzzy_json.py` (verbatim)
- `clients/git_sync.py` ← `api/src/sernia_ai/memory/git_sync.py` (commit-author and commit-message prefixes changed from `agent:` → `mcp:` so the GitHub history shows which service edited what; logic identical)

When upstream changes meaningfully (auth flow, schema, scopes), reflect the change here — but keep the diff minimal. The drift is acceptable for v1; the alternative (path-installing `api/`) defeats the self-contained model.

### Workspace storage
The MCP server's knowledge surface (`MEMORY.md` + `skills/<name>/SKILL.md`) is **git-backed and shared with the Sernia AI agent** through the private `EmilioEsposito/sernia-knowledge` GitHub repo. Both services clone, pull, and push to the same repo, so edits flow between them transparently.

Wiring:

- **At startup** — `app.py` wraps the FastMCP lifespan to call `ensure_repo(WORKSPACE_PATH)` before serving traffic. This either clones the repo into the workspace dir (first boot) or pulls latest (subsequent boots). Failures are logged but never crash the server.
- **After every `edit_resource` write** — `tools/context.py` schedules a fire-and-forget `commit_and_push(WORKSPACE_PATH)` task. Lock-serialized within the process; the git_sync code handles cross-service merge conflicts (they pull-before-push).
- **No PAT, no sync** — without `GITHUB_EMILIO_PERSONAL_WRITE_PAT` set, both `ensure_repo` and `commit_and_push` no-op. Tests run that way; local dev can too. **Production must always have the PAT set** or the workspace becomes ephemeral on each Railway redeploy.

The path itself comes from `SERNIA_MCP_WORKSPACE_PATH` (default `./workspace`). It's captured at `config.py` import time; tests reload the module to pick up env overrides (see `tests/conftest.py`). Don't read `os.environ["SERNIA_MCP_WORKSPACE_PATH"]` ad-hoc — go through `sernia_mcp.config.WORKSPACE_PATH`.

Concurrency caveat: if both this MCP service AND the sernia_ai agent push to the repo at the same time, one of them will hit a non-fast-forward error. The git_sync code handles this by pulling-before-pushing and committing any merge conflicts as-is. In practice writes are infrequent enough that this is rarely exercised, but if you see weird `agent: commit conflicted files from merge` commits on GitHub, that's why.

### Security model — two layers, both load-bearing

1. **Authentication (Clerk OAuth)** — `ClerkProvider` validates the bearer token via introspection + userinfo. A request without a valid Clerk-issued token gets a 401 with the `www-authenticate` challenge.
2. **Authorization (email-domain allowlist)** — `AuthMiddleware(auth=require_allowed_email_domain)` rejects authenticated users whose email domain isn't in `config.ALLOWED_EMAIL_DOMAINS` (env: `SERNIA_MCP_ALLOWED_EMAIL_DOMAINS`, default `serniacapital.com`). Without this, any user who signed in to the same Clerk instance for an unrelated app would be accepted.

The authorization callable lives in `src/sernia_mcp/auth.py` — it inspects `ctx.token.claims["email"]` and raises `AuthorizationError` for non-allowed domains. Test guardrails in `tests/test_auth.py` pin every allow/reject path.

**When extending tool surface, do not add a new auth path.** All tools route through the same `AuthMiddleware` automatically. If you need *finer* per-tool permissions (e.g. an admin-only tool), tag the tool and add a second auth check rather than bypassing the middleware.

**Recommended Clerk configuration** (defense-in-depth, not load-bearing): in the Clerk dashboard, set Restrictions → Allowlist → `*@serniacapital.com`. Even if our server-side check has a bug, Clerk won't have authenticated the user in the first place.

### Local-dev auth bypass (`SERNIA_MCP_DISABLE_AUTH`)

For local exploration with the MCP Inspector / curl, set `SERNIA_MCP_DISABLE_AUTH=true`. The server then runs unauthenticated even when the four Clerk vars are present. **Hard production guard**: if this flag is set AND `RAILWAY_ENVIRONMENT_NAME` is present (any Railway env), the server **raises at boot** — see `_disable_auth_requested` in `server.py`. Means a stray flag in `.env` can't accidentally ship to a deployed service.

The VS Code "Sernia MCP Server" launcher sets this in its `env` block, so launching via the IDE always boots in unauth mode regardless of what's in `.env`.

### `SerniaAuthMiddleware` overrides

We extend FastMCP's `AuthMiddleware` (in `auth.py`) with two bypasses for known incompatibilities with the Apps approval flow:

1. **`ui://` resources** — FastMCP synthesizes Prefab renderer resources at `ui://prefab/tool/<hash>/renderer.html` *on demand*. The parent middleware does `get_resource(uri)` first, which returns None for synthesized URIs, and rejects with "resource not found." Our override short-circuits for any `ui://` URI.
2. **Hashed app-tool names** — Apps action buttons fire `tools/call` with `<12-hex>_<local-name>` (per FastMCP's `addressing.py`). The parent's `get_tool(name)` doesn't recognize the hashed form. Our override detects hashed names via `parse_hashed_backend_name`, skips the precheck, and runs only the email-domain identity check before delegating to the dispatcher.

Both overrides are tested in `tests/test_auth.py`. If FastMCP changes how Apps routes calls, the bypass tests will likely catch it before the approval flow regresses silently.

### Auth model (provider details)
Clerk OAuth (DCR-compatible) is the production auth. Four env vars are all-or-nothing:

```
FASTMCP_SERVER_AUTH_CLERK_DOMAIN
FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID
FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET
SERNIA_MCP_BASE_URL
```

If any one is missing, `clerk_oauth_configured()` returns False and the server boots **unauthenticated**. That's intentional for local dev. **Never expose unauth state to the public internet** — Railway should always have all four set.

**Two callback URIs, only one to register.** The OAuth flow has two redirect URIs and they're easy to confuse:

- **Server-side (you register this in Clerk):** `{SERNIA_MCP_BASE_URL}/auth/callback` — Clerk redirects here after the user signs in. Path is hardcoded by FastMCP's `ClerkProvider`; only `SERNIA_MCP_BASE_URL` changes. Add one entry per env (`https://dev.mcp.sernia.ai/auth/callback`, `https://mcp.sernia.ai/auth/callback`, optionally `http://localhost:8080/auth/callback`).
- **Client-side (don't register; informational only):** Each MCP client (Claude.ai, Claude Desktop, ChatGPT, VS Code) declares its own callback (e.g. `https://claude.ai/api/mcp/auth_callback`) when it Dynamic-Client-Registers via our `/register` endpoint. Clerk's consent screen displays it so you can verify the client; clicking Allow Access is the right action.

### Approval flow (HITL via FastMCP Apps)
Destructive sends (`quo_send_sms`, `google_send_email`) are gated through the [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) extension using a deterministic tool-visibility split:

1. The model calls the visible `@app.ui()` tool. The tool does **not** send — it queues a pending row and returns a Prefab `Card` with Approve/Reject buttons.
2. The buttons fire `CallTool("_confirm_send_*", ...)` against a hidden `@app.tool()` (visibility=`["app"]`) that runs the actual send.
3. Hidden tools are absent from `tools/list` AND raise `Unknown tool` on direct `tools/call` (verified in `test_approvals.py::TestHiddenToolEnforcement`).

The model has no reachable path to the send primitive. This is structural enforcement, not a capability check — clients without Apps support get a payload they can't render, and the send simply cannot happen.

When tweaking the approval cards: keep the "decided" reactive state binding (`disabled=Rx("decided")`) so users can't double-click. Verify in the browser via `fastmcp dev apps src/sernia_mcp/dev_server.py` — pytest can't catch UI rendering bugs.

---

## Deployment

The service deploys as its **own** Railway service.

| Setting | Value |
|---------|-------|
| Root directory | `apps/sernia_mcp` |
| Build command | `uv sync --frozen` |
| Start command | `uv run uvicorn sernia_mcp.app:app --host 0.0.0.0 --port $PORT` |
| Public domain | `mcp.sernia.ai` (production), `dev.mcp.sernia.ai` (development) |
| Port | injected as `$PORT` by Railway |

Required env on Railway: the four Clerk vars + `SERNIA_MCP_BASE_URL=https://mcp.sernia.ai` + upstream API keys + `SERNIA_MCP_WORKSPACE_PATH` (for first cut, point at `/data/workspace` on a mounted Railway volume).

### Relationship to the FastAPI monorepo
The old `api/src/sernia_mcp/` and `api/src/tool_core/` packages — plus the `/api/mcp` mount and the React Router OAuth-metadata proxy hack — were removed as part of the same change that introduced this service. There's no longer a second MCP listening anywhere in the monorepo. `mcp.sernia.ai` is the single source of MCP truth.

`fastmcp` itself is still a dep on the root `pyproject.toml` because `api/src/sernia_ai/tools/quo_tools.py` uses `FastMCPToolset` to bridge the OpenPhone OpenAPI spec into the PydanticAI agent. That's a different use case (PydanticAI tool wrapper, not an HTTP MCP server) and isn't going anywhere.

---

## Adding a new tool

1. Implement the harness-agnostic logic in `core/<service>/<verb>.py` — plain async function, typed args, returns a Pydantic model from `core/types.py` or a string.
2. Add a thin wrapper in `tools/<service>.py` decorated with `@mcp.tool`. Translate `CoreError` subclasses to `ToolError`.
3. If the tool mutates external state (sends a message, deletes data), put it behind the approval flow in `tools/approvals.py` instead — never expose a destructive `@mcp.tool`.
4. Add tests:
   - Unit test against the core function with the upstream client mocked.
   - Smoke test in `tests/test_smoke.py::test_expected_tools_exposed` — add the tool name to the expected set.
   - For approval flows: extend `tests/test_approvals.py` mirroring the SMS/email patterns.
   - For knowledge/context tools: extend `tests/test_context.py` (it covers the doorway, read, edit, write paths end-to-end via in-memory `Client(mcp)`).

---

## Common pitfalls

- **Don't `find_dotenv()`** — it walks parent directories and picks up the monorepo's `.env`. `config.py` uses `load_dotenv(dotenv_path=".env")` deliberately.
- **Don't import from `api.src.*`** — that defeats the self-contained model. If you need a helper from the monorepo, vendor it into `clients/`.
- **Don't add SQLAlchemy / FastAPI / pydantic-ai / Twilio** to `pyproject.toml`. Keep the dep surface small. If you need persistence, use a small SQLite or Postgres-via-asyncpg layer scoped to this service.
- **`load_dotenv()` is called at module import**. If a test wants to override env vars, it must `monkeypatch.setenv()` AND `importlib.reload(sernia_mcp.config)` — see `tests/conftest.py` for the pattern.

---

## Pointers to recent decisions

- **Why a separate Railway service?** Mono FastAPI build was getting heavy and bundling MCP-only deps; future commercialization needs a clean lift-out path.
- **Why uvicorn directly instead of `fastmcp run`?** `fastmcp run` builds the Starlette ASGI app internally and gives no hook to wrap it with middleware. Without that hook, inbound-request logging (and auth-flow debugging) is invisible. `app.py` exposes `app = mcp.http_app(...)` with a `_RequestLogMiddleware` already attached, then uvicorn runs it.
- **Where did `fastmcp.json` go?** Removed. It was only consumed by `fastmcp run`, which we no longer use. The browser-based approval harness (`fastmcp dev apps src/sernia_mcp/dev_server.py`) takes its source path as a CLI arg and doesn't read `fastmcp.json`.
- **Why `stateless_http=True`?** No per-client session state — every MCP request is self-contained. Matches the "remote agents" use case and supports horizontal scaling on Railway without session affinity.
- **Why no Docker?** Railway's nixpacks autodetects `pyproject.toml` + `uv.lock` and we explicitly set the start command via `railway_sernia_mcp.json`. A Dockerfile is only needed if we hit autodetection limits.
- **Why `/mcp` and not `/mcp/`?** Claude posts to `/mcp` (no trailing slash) per the spec. Mounting at `/mcp/` triggers a Starlette 307 redirect on the no-slash request, and many HTTP clients drop the `Authorization` header when following redirects — every Claude request would arrive unauthenticated. Pinned in `tests/test_http_app.py::test_mcp_endpoint_no_redirect_on_canonical_path`.
- **Why is `read_resource` a tool when MCP has `resources/read`?** Empirically, Claude.ai's connector doesn't surface `resources/read` to the model. The tool form is the workaround. Resources stay registered for hosts that DO surface them (Claude Desktop, MCP Inspector).
- **Why is the server's `instructions` field not load-bearing?** Same reason as above — Claude.ai doesn't surface it to the model either. Tested with an easter-egg in `instructions` and the model couldn't see it. We rely on `sernia_context` instead.
