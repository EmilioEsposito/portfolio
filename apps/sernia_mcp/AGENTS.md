# Sernia MCP development

This is an independent Python service with its own environment, dependencies, and
Railway deployment. Read [README.md](README.md) for setup, OAuth, storage, and the
production contract. Check [TODOS.md](TODOS.md) before extending tools or auth.

## Structure and commands

`core/` contains harness-independent async functions; `tools/` adapts them to
FastMCP. `clients/` contains deliberately vendored integration code. `app.py`
exposes the ASGI app and wraps its lifespan for workspace sync and request logging.

Run from `apps/sernia_mcp/`, without activating the parent environment:

```bash
uv sync --locked
uv run pytest -q -m 'not live'
uv run uvicorn sernia_mcp.app:app --port 8090
uv run fastmcp dev apps src/sernia_mcp/dev_server.py
```

The development harness mocks sends. Use it for approval-card browser checks;
pytest cannot verify rendering. Refresh the relevant
[FastMCP documentation](https://gofastmcp.com/llms.txt) before framework changes,
especially [HTTP deployment](https://gofastmcp.com/deployment/http) and
[ClerkProvider](https://gofastmcp.com/python-sdk/fastmcp-server-auth-providers-clerk).

## Dependency and configuration boundaries

- Do not import `api.src.*` or add the parent API's framework dependencies to this
  service. Vendor only the required helper and document its source.
- Keep `core/` free of FastMCP dependencies. Translate `CoreError` to `ToolError`
  in the thin tool wrapper.
- Use `config.py`; do not read workspace configuration ad hoc. It loads only the
  service's `.env`, not parent files. Do not use `find_dotenv()`.
- Configuration is captured at import time. Tests changing environment variables
  must reload `sernia_mcp.config`; see `tests/conftest.py`.

### Vendored clients

| Client | Upstream source | Intentional difference |
| --- | --- | --- |
| `google_auth.py` | `api/src/google/common/service_account_auth.py` | No FastAPI exceptions. |
| `gmail.py` | `api/src/google/gmail/service.py` | Send/read primitives, without webhook plumbing. |
| `quo.py` | `api/src/open_phone/service.py` | Contact read/cache, without DB sync. |
| `_fuzzy.py` | `api/src/utils/fuzzy_json.py` | Shared fuzzy-search helper. |
| `git_sync.py` | `api/src/sernia_ai/memory/git_sync.py` | MCP commit author/message prefixes. |

Reflect relevant upstream auth, schema, and scope changes here while keeping the
service self-contained. Review intentional drift rather than blindly copying files.

## Authentication and approvals

Production uses Clerk OAuth plus the server-side email-domain allowlist.
The optional internal bearer is for explicitly configured service-to-service
development; production is OAuth-only. Do not deliver a Clerk backend admin key
or development bearer token to production. Clerk configuration is all-or-nothing;
never expose a partially configured, unauthenticated service.

`SERNIA_MCP_DISABLE_AUTH=true` is local-only and must fail at startup in every
Railway environment. New tools use the existing auth middleware. For finer
permissions, add checks rather than bypassing it.

`SerniaAuthMiddleware` handles synthesized `ui://` resources and hashed Apps tool
names that FastMCP's ordinary lookup does not resolve. Hashed action calls still
require the email-domain identity check. Preserve the tests in `test_auth.py`.

Sends use the Apps approval flow:

1. A model-visible UI tool queues a pending action and returns an approval card.
2. A hidden confirmation tool performs the send after the Apps action.
3. `ApprovalApp` hides confirmation tools from listing and rejects raw-name calls,
   while allowing hashed UI dispatch.

Hashed addresses identify routes; they do not prove a human click. An authenticated
custom client can reproduce an Apps call. Preserve raw-name isolation, domain
authorization, one-time pending IDs, expiry, and `disabled=Rx("decided")` on decided
cards. Fixed reminders are a separate, code-owned exception documented in README.
Do not introduce an unguarded destructive tool.

## Context and workspace

Some clients do not surface MCP resources or server instructions to the model.
Keep `sernia_context` as the entry point and `read_resource` as a callable tool,
alongside registered resources. `edit_resource` performs exact, atomic replacement;
`write_resource` handles new files and full replacements.

The workspace is shared with the API agent through a private Git repository.
Startup clones/pulls it; writes schedule serialized commit/push work. Missing sync
credentials make these operations no-ops, which is useful in tests but makes
unpersisted storage ephemeral. Production needs both persistent workspace/OAuth
storage and working Git sync; see the README deployment contract. Concurrent
cross-service pushes can leave merge conflicts; inspect those rather than assuming
all writes synchronized successfully.

## Verification

- Add core tests with mocked clients and update `test_smoke.py` when changing tools.
- Exercise approval actions through the in-process MCP client in
  `test_approvals.py`; cover context operations in `test_context.py`.
- HTTP/auth changes belong in `test_http_app.py` and `test_auth.py`. Preserve the
  canonical `/mcp` endpoint without redirects that can lose authorization headers.
- Mark provider tests `live` and exclude them by default. Never use real external
  recipients to test sends.
- Keep the ASGI entry point: request instrumentation wraps `mcp.http_app(...)`.
  `stateless_http=True` avoids requiring per-client session affinity.
