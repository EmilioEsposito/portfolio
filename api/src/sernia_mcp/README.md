# Sernia MCP Server

HTTP [Model Context Protocol](https://modelcontextprotocol.io/) server exposing a curated subset of Sernia Capital tools to remote AI harnesses (Claude mobile app, ChatGPT app, other agent runtimes).

Runs as a mounted sub-application inside the main FastAPI app at `/api/mcp`.

> **Design intent:** This server is read-mostly with a small, gated surface for writes. It is *not* feature-parity with the Sernia AI pydantic-ai agent. For destructive or external-facing actions (external SMS, external email, deletes, calendar writes, task creation), use the Sernia AI web chat — those operations require human-in-the-loop approval which is not yet modeled in MCP.

## Architecture

```
apps/mcp-client (Claude mobile, ChatGPT, ...)
          │ HTTPS + Bearer
          ▼
/api/mcp/  ← FastMCP ASGI mount on the main FastAPI app
          │
          ▼
api/src/sernia_mcp/tools/*.py   (thin @mcp.tool wrappers)
          │
          ▼
api/src/tool_core/**   (harness-agnostic async functions)
          │
          ▼
api/src/open_phone/service.py, api/src/google/*, httpx clients
```

The `tool_core/` layer is **harness-agnostic**: plain async functions with typed args and Pydantic return models. It can be called from any harness — the MCP wrappers live in `sernia_mcp/tools/`, and a future pydantic-ai wrapper could live in `sernia_ai/tools/`. Today the Sernia AI agent has its own (duplicated) implementation — see "Relationship to sernia_ai" below.

## Tools (v1)

| Tool | Kind | Notes |
|------|------|-------|
| `workspace_read` | read | Reads from `api/src/sernia_ai/workspace/` (shared with Sernia AI) |
| `workspace_write` | write | Only `.md`, `.txt`, `.json` suffixes. Path-escape rejected. |
| `quo_search_contacts` | read | Fuzzy search OpenPhone contacts |
| `quo_get_thread_messages` | read | SMS thread with a phone number |
| `quo_send_sms` | **UI-gated** | Returns an Approve/Reject card. No send without click. |
| `google_search_emails` | read | Gmail search syntax |
| `google_read_email` | read | Full body by message ID |
| `google_send_email` | **UI-gated** | Returns an Approve/Reject card. No send without click. |
| `clickup_search_tasks` | read | ClickUp task search with filters |

## Auth — Clerk OAuth (required by Claude custom connectors)

Claude doesn't accept static bearer tokens. The MCP spec and Claude's custom connector UI both require OAuth 2.1, and Claude uses Dynamic Client Registration (DCR, RFC 7591) to auto-register itself as a client at connection time.

FastMCP ships a first-party `ClerkProvider` that proxies DCR + authorization code flow to Clerk, so we don't have to implement an OAuth server from scratch. The existing Clerk instance this project already uses for the web app is reused — no new identity provider.

### One-time Clerk dashboard setup

1. Clerk dashboard → **OAuth Applications** → Create application.
2. Name it e.g. "Sernia MCP".
3. Add this **Authorized redirect URI** (exact match required):
   ```
   {SERNIA_MCP_BASE_URL}/auth/callback
   ```
   where `SERNIA_MCP_BASE_URL` is the public URL from your `.env` (see below). For Railway prod: `https://dev.eesposito.com/api/mcp/auth/callback`. For ngrok: rotate the URL here each time you start a new tunnel, or use a reserved domain.
4. Copy the **Client ID** and **Client Secret** into `.env`.
5. Your **instance domain** (e.g. `your-instance.clerk.accounts.dev`) is on the Clerk dashboard home.

### Required env vars

```bash
FASTMCP_SERVER_AUTH_CLERK_DOMAIN=your-instance.clerk.accounts.dev
FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID=<from Clerk OAuth app>
FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET=<from Clerk OAuth app>
SERNIA_MCP_BASE_URL=https://dev.eesposito.com/api/mcp
```

If any of these four are missing the MCP endpoint is **not mounted** — the rest of the FastAPI app still boots. This is intentional so local dev (and the Sernia AI web chat) works without Clerk OAuth configured.

### Claude custom connector setup

After the Clerk side is ready and the server is deployed:

1. In Claude (Desktop or claude.ai) → **Settings → Connectors → Add custom connector**.
2. URL: `{SERNIA_MCP_BASE_URL}/` (the trailing slash matters).
3. Leave the optional OAuth Client ID / Secret fields blank — Claude will use DCR via the `/register` endpoint our server exposes.
4. Click Add. Claude will redirect you to Clerk's hosted sign-in; sign in with your @serniacapital.com Google account; Clerk redirects back; Claude now has a token.
5. The 9 tools should appear in the connector's tool list.

### Verifying OAuth metadata is live

```bash
curl https://dev.eesposito.com/api/mcp/.well-known/oauth-authorization-server | jq .
# Should return keys: issuer, authorization_endpoint, token_endpoint, registration_endpoint, ...
```

## Identity

Domain-wide Google delegation still uses `identity.resolve_user_email_for_request()`. For the POC it's hardcoded to `emilio@serniacapital.com`. Once multi-user OAuth is proven, the next step is to extract the authenticated email from the Clerk JWT claims via `get_access_token()` in each tool — then different users acting through the MCP connect to *their own* Google mailbox.

## Cross-harness memory

`workspace_read` / `workspace_write` operate on the same directory (`api/src/sernia_ai/workspace/`) that the Sernia AI agent uses — `MEMORY.md`, `skills/<name>/SKILL.md`, `areas/`, `daily_notes/`. Writes from MCP are visible to the Sernia AI agent on its next run (via the filetree + memory injection in `sernia_ai/instructions.py`). This is the "self-improving skills" loop — any harness that can write to this workspace contributes to the shared knowledge base.

## Approval gating (MCP Apps + tool-visibility split)

Destructive writes are gated via the official [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) extension using a deterministic server-side pattern — not advisory hints, not the conversation-only `Approval` provider, and not `ctx.elicit()`.

How it works:

1. `quo_send_sms` / `google_send_email` are `@app.ui()` *entry-point* tools on a `FastMCPApp` (`api/src/sernia_mcp/tools/approvals.py`).
2. At call time, the server checks `ctx.client_supports_extension(UI_EXTENSION_ID)`. A client that doesn't advertise Apps support gets a `ToolError` and **no further state change** — the send cannot happen.
3. An Apps-capable client gets a Prefab `Card` with Approve / Reject buttons. The send args are parked in an in-memory dict (`_PENDING[uuid]`). No external API call yet.
4. Clicking Approve fires `CallTool("_confirm_send_sms", pending_id=..., decision="approve")`. That tool is `@app.tool()` — `visibility=["app"]` — **hidden from `tools/list`** and **uncallable via `tools/call`** from the model side. The real `send_sms_core` / `send_email_core` only runs inside this hidden tool.
5. Reject or timeout: the pending row is consumed; no send.

The model has no reachable path to the send primitive. Server-side enforcement is structural, not compliance-based. Verified locally via fastmcp `Client` — hidden tools raise `Unknown tool` on direct invocation.

**Client compatibility:** Claude Desktop, Claude app, VS Code GitHub Copilot, Goose, Postman, MCPJam (per the MCP Apps client matrix). The `test_agent.py` pydantic-ai client intentionally does *not* advertise Apps support, so it's a fail-closed regression test.

**In-memory state caveat:** pending rows are lost on server restart. That's fine for the expected ~seconds-to-minutes approval window. Move to DB when hours-long pending becomes a real scenario.

**Out of scope:** queue-based *team* approvals where a different human approves the caller's send. MCP Apps is for the caller approving their own action. Team approvals still require the DB-backed pattern we deferred.

## Relationship to sernia_ai

**sernia_ai is the canonical harness; the MCP server is additive.**

- The pydantic-ai agent in `api/src/sernia_ai/` is unchanged. Its tools in `sernia_ai/tools/*.py` still own their own implementations.
- `api/src/tool_core/` currently **duplicates** a thin slice of sernia_ai tool logic. This is intentional for the POC — it lets MCP ship without risking regressions to the production agent.
- A later refactor will collapse the duplication by pointing `sernia_ai/tools/*.py` at `tool_core/` via thin wrappers (add back `ctx.deps` logging, `ApprovalRequired`, etc.). That PR is out of scope here.

## Development

### Running the server

```bash
# Start the API. If Clerk OAuth env vars aren't set the /api/mcp endpoint
# simply won't mount (you'll see a warning in the log) — the rest of the
# API still works. For local iteration on tools, use `fastmcp dev apps`
# below instead; it doesn't need Clerk configured.
pnpm fastapi-dev
```

### Testing the approval flow in a browser (`fastmcp dev apps`)

The main server's write tools (`quo_send_sms`, `google_send_email`) need an MCP
Apps-capable client. For local iteration, FastMCP ships a browser-based dev host
that renders Prefab UIs. A standalone dev harness at
`api/src/sernia_mcp/dev_server.py` mocks the upstream sends so you can exercise
the flow with any phone/email without actually sending anything.

```bash
fastmcp dev apps api/src/sernia_mcp/dev_server.py \
  --mcp-port 8123 --dev-port 8081 --no-reload
```

This spawns:
- An MCP server on `http://localhost:8123/mcp/` (the dev harness, *not* the
  FastAPI app).
- A browser-based dev UI on `http://localhost:8081/` — opens automatically.

In the browser: the picker lists `quo_send_sms` and `google_send_email`; fill in
a form and click Launch; the approval card renders; click Approve or Reject and
watch the inspector panel for the `CallTool → _confirm_send_*` traffic. Mocked
upstream calls are printed to stdout (`[DEV MOCK send_sms_core] ...`).

### Running the regression tests

```bash
pytest api/src/tests/test_sernia_mcp_approvals.py -v
```

Covers:
- Backend `_confirm_send_*` approve / reject / unknown-id behavior
  (with upstream `send_*_core` mocked).
- Structural isolation: hidden `@app.tool()` tools absent from `tools/list`
  and raise `Unknown tool` on direct `tools/call`.
- Capability-happy path: entry-point tool queues a pending row and returns
  a PrefabApp when Apps capability is advertised.
- Fail-closed: non-Apps client rejection happens before any state mutation.

Not covered (would require a real Apps host):
- The PrefabApp's rendering fidelity in a specific client.
- The button-click → CallTool round-trip through an Apps host's AppBridge.
  Use `fastmcp dev apps` in a browser to exercise this manually.

## Out of scope (explicit non-goals for v1)

- Feature parity with the Sernia AI agent (deliberately a smaller surface).
- External SMS / email sending (requires HITL approval queue — deferred).
- Calendar / Drive / ClickUp write tools (deferred; read-only for now).
- Scheduled / triggered agent runs via MCP (still on the pydantic-ai path).
- Per-user identity, rate limiting, or multi-tenant auth.
- Collapsing the `tool_core` ↔ `sernia_ai/tools` duplication.
