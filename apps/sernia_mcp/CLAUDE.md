# CLAUDE.md — apps/sernia_mcp

> **Last updated**: 2026-04-25

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
| `uv run pytest -v` | Run the full suite (~1s, no network). |
| `uv run pytest -m live` | Live tests (require API keys; opt-in only). |
| `uv run uvicorn sernia_mcp.app:app --port 8080` | Boot HTTP server with full inbound-request instrumentation. |
| `uv run fastmcp dev apps src/sernia_mcp/dev_server.py` | Browser-based approval-flow harness (mocked sends). |
| `uv run ruff check` | Lint. |
| `uv run ruff format` | Format. |

All commands run from inside `apps/sernia_mcp/`. **Do not** activate the parent monorepo's venv — uv handles its own isolated venv per the `pyproject.toml` here.

---

## Working principles

### AI-first testability
**Every change should be verifiable by Claude without involving the user.** That means:

- New tool? Add a smoke entry to `tests/test_smoke.py` confirming it appears in `/tools/list`, plus a unit test against the core function with mocked clients.
- New approval flow? Mirror the patterns in `tests/test_approvals.py` (in-process `Client(FastMCP(...))` round-trip).
- Tweaking auth or HTTP? Extend `tests/test_http_app.py` — it boots the real ASGI app via `httpx.ASGITransport` and asserts the route is reachable.
- Boot failure? Run `uv run uvicorn sernia_mcp.app:app --port 8765` in the background and curl `/mcp/` with a real MCP `initialize` POST. The server's response includes `"extensions":{"io.modelcontextprotocol/ui":{}}` when Apps support is wired correctly.

The user is **not** the first line of testing. If a regression is only catchable by hand, write the test first. If a test would require API keys, add a `live` marker and skip by default — write a mocked version to cover wiring.

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

When upstream changes meaningfully (auth flow, schema, scopes), reflect the change here — but keep the diff minimal. The drift is acceptable for v1; the alternative (path-installing `api/`) defeats the self-contained model.

### Workspace storage
`workspace_read` / `workspace_write` operate on `SERNIA_MCP_WORKSPACE_PATH`. Two valid configurations:

1. **Standalone**: a directory owned by this service (e.g. mounted volume on Railway).
2. **Shared during migration**: pointed at `api/src/sernia_ai/workspace/` so the Sernia AI agent and the MCP server read/write the same MEMORY.md, skills/, etc.

The path is captured at `config.py` import time; tests reload the module to pick up env overrides (see `tests/conftest.py`). Don't read `os.environ["SERNIA_MCP_WORKSPACE_PATH"]` ad-hoc — go through `sernia_mcp.config.WORKSPACE_PATH`.

### Auth model
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
   - Smoke test in `tests/test_smoke.py::test_expected_tools_exposed` updating the expected set.
   - For approval flows: extend `tests/test_approvals.py` mirroring the SMS/email patterns.

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
