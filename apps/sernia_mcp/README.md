# Sernia MCP Server

HTTP [Model Context Protocol](https://modelcontextprotocol.io/) server exposing a curated subset of Sernia Capital tools to remote AI harnesses (Claude Desktop, Claude.ai custom connectors, ChatGPT, etc.).

Self-contained Python service, deployable as its own Railway service under `mcp.sernia.ai`. Uses [FastMCP](https://gofastmcp.com/) with Clerk OAuth for auth and the [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) extension for human-in-the-loop approvals on destructive sends.

> **Design intent:** read-mostly with a small, gated surface for writes. External SMS / email sends require explicit user approval through an interactive Approve / Reject card; clients that can't render the card cannot reach the underlying send tools (deterministic server-side enforcement, not advisory).

## Tools

| Tool | Kind | Notes |
|------|------|-------|
| `workspace_read` | read | Reads from the configured workspace path |
| `workspace_write` | write | Only `.md`, `.txt`, `.json`. Path-escape rejected. |
| `quo_search_contacts` | read | Fuzzy search OpenPhone contacts |
| `quo_get_thread_messages` | read | SMS thread with a phone number |
| `quo_send_sms` | **UI-gated** | Returns Approve/Reject card. No send without click. |
| `google_search_emails` | read | Gmail search syntax |
| `google_read_email` | read | Full body by message ID |
| `google_send_email` | **UI-gated** | Returns Approve/Reject card. No send without click. |
| `clickup_search_tasks` | read | ClickUp task search with filters |

## Quick start (local dev)

```bash
cd apps/sernia_mcp
uv sync                                                          # creates .venv and installs deps
cp .env.example .env                                             # fill in API keys; leave Clerk vars blank for unauth dev
uv run pytest -v                                                 # full test suite (no network)
uv run uvicorn sernia_mcp.app:app --host 0.0.0.0 --port 8080     # boots HTTP at http://localhost:8080/mcp/
```

`sernia_mcp.app:app` is the ASGI application — uvicorn loads it directly. The module wires a request-logging middleware so every inbound request surfaces in Logfire.

### Browser-based approval testing

```bash
uv run fastmcp dev apps src/sernia_mcp/dev_server.py
```

Spawns an MCP server on :8000 and the FastMCP browser inspector on :8080. The picker lists `quo_send_sms` and `google_send_email`; fill in the form and click Launch to see the approval card. **All upstream sends are mocked** — safe to use any phone/email.

## Auth — Clerk OAuth

Claude Desktop / Claude.ai custom connectors require OAuth 2.1 with Dynamic Client Registration (RFC 7591). The four-var Clerk integration handles all of it:

```bash
FASTMCP_SERVER_AUTH_CLERK_DOMAIN=your-instance.clerk.accounts.dev
FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID=<from Clerk OAuth app>
FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET=<from Clerk OAuth app>
SERNIA_MCP_BASE_URL=https://mcp.sernia.ai
```

If any of the four are missing the server runs **unauthenticated** — useful for local dev, never expose this state publicly.

### Callback URIs — there are two; only one of them is yours to register

Source of confusion: the OAuth flow has two redirect URIs in play. Only the first one belongs in the Clerk dashboard.

**1. The MCP server's own redirect URI** — register this in Clerk OAuth Application → Authorized redirect URIs:

```
https://mcp.sernia.ai/auth/callback           # production
https://dev.mcp.sernia.ai/auth/callback       # dev environment
http://localhost:8080/auth/callback           # local, only if testing the full OAuth flow
```

These are FastMCP's `ClerkProvider` callback — Clerk redirects here after the user signs in, and the provider then hands the auth code back to the MCP client. The path `/auth/callback` is hardcoded by `ClerkProvider`; change it by changing `SERNIA_MCP_BASE_URL`, not the path.

**2. The MCP client's redirect URI** — *don't register this manually*.

When Claude Desktop / Claude.ai connect for the first time, they hit our `/register` endpoint and Dynamic-Client-Register themselves with Clerk, declaring their own callback (e.g. `https://claude.ai/api/mcp/auth_callback` for Claude.ai). Clerk shows you that URL on its consent screen so you can verify the client's identity before clicking Allow Access — but you do not pre-register it. Each MCP client (Claude.ai, Claude Desktop, ChatGPT, VS Code, etc.) will declare its own.

### Connecting Claude Desktop

1. Settings → Connectors → **Add custom connector**.
2. URL: `https://mcp.sernia.ai/mcp/` (or `https://dev.mcp.sernia.ai/mcp/` for dev). The trailing slash matters.
3. Leave the optional OAuth Client ID / Secret fields blank — the server announces DCR via `/register`.
4. Click Add. Claude opens Clerk's hosted sign-in; sign in with your @serniacapital.com Google account; Clerk redirects back; Claude now has a token.

### Verifying OAuth metadata is live

```bash
curl https://mcp.sernia.ai/.well-known/oauth-protected-resource/mcp/ | jq .
# Should return JSON with: resource, authorization_servers, scopes_supported, ...
```

## Deployment (Railway)

This service deploys independently of the main FastAPI app. In Railway:

1. **Project → New Service → GitHub Repo**, root directory `apps/sernia_mcp`.
2. **Build Command**: `uv sync --frozen` (uses `uv.lock`).
3. **Start Command**: `uv run uvicorn sernia_mcp.app:app --host 0.0.0.0 --port $PORT`.
4. **Domain**: bind `mcp.sernia.ai` to this service. Set `SERNIA_MCP_BASE_URL=https://mcp.sernia.ai` to match.
5. Set the four Clerk vars + upstream API keys per `.env.example`.

### Single source of MCP truth

The old `/api/mcp` mount on the FastAPI monorepo (`api/src/sernia_mcp/` + `api/src/tool_core/`) was removed in the same change that introduced this service — `mcp.sernia.ai` is the only MCP listener. The React Router OAuth-metadata proxy hack that supported the nested mount is also gone.

## Workspace storage

`workspace_read` / `workspace_write` operate on `SERNIA_MCP_WORKSPACE_PATH`. To share state with the Sernia AI agent in the monorepo during the migration window, point both at the same directory:

```bash
SERNIA_MCP_WORKSPACE_PATH=/path/to/portfolio/api/src/sernia_ai/workspace
```

Future: when the MCP service is fully extracted, the workspace becomes a Git-tracked or object-stored directory owned by this service.

## Layout

```
apps/sernia_mcp/
├── pyproject.toml               # uv project (own venv, own deps)
├── railway_sernia_mcp.json      # Railway deploy config (start command, healthcheck)
├── .env.example
├── src/sernia_mcp/
│   ├── app.py                   # ASGI entrypoint (uvicorn loads this)
│   ├── server.py                # FastMCP instance + tool registration + Logfire config
│   ├── dev_server.py            # Browser-testable mocked harness
│   ├── config.py                # Env-driven constants
│   ├── identity.py              # Acting-user resolution
│   ├── clients/                 # Vendored upstream HTTP clients
│   │   ├── google_auth.py       #   service-account delegated creds
│   │   ├── gmail.py             #   Gmail v1 API helpers
│   │   ├── quo.py               #   OpenPhone API helpers
│   │   └── _fuzzy.py            #   Fuzzy filter
│   ├── core/                    # Harness-agnostic async tool functions
│   │   ├── workspace/files.py
│   │   ├── google/gmail.py
│   │   ├── quo/{contacts,send_sms}.py
│   │   ├── clickup/tasks.py
│   │   ├── errors.py
│   │   └── types.py
│   └── tools/                   # @mcp.tool wrappers + approvals app
│       ├── {workspace,google,quo,clickup}.py
│       └── approvals.py         #   FastMCPApp HITL flow
└── tests/                       # uv run pytest -v
    ├── test_smoke.py            #   Imports + tool registration
    ├── test_workspace.py        #   Real filesystem
    ├── test_approvals.py        #   In-memory FastMCP Client
    └── test_http_app.py         #   ASGI lifespan + /mcp/ liveness
```

## Out of scope (v1)

- Feature parity with the Sernia AI agent (deliberately a smaller surface).
- External SMS / email sending without UI approval (deferred — DB-backed queue).
- Calendar / Drive / ClickUp **write** tools (deferred; read-only for now).
- Per-user identity / multi-tenant auth (POC: single user via `SERNIA_MCP_DEFAULT_USER`).

## See also

- [`CLAUDE.md`](CLAUDE.md) — AI-assisted-development guide for this service.
- [FastMCP docs](https://gofastmcp.com/) — refresh once per session for new features.
