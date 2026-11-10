# Claude Code cloud environment

When `CLAUDE_CODE_REMOTE=true`, the session-start hook runs `setup_remote.sh`.
It installs dependencies, starts local PostgreSQL, applies migrations, seeds
non-production demo data, and builds the web app. Optional `SEED_BUCKET_*`
configuration enables private, reviewed seed fixtures; see
[development setup](../docs/DEVELOPMENT.md#sanitized-seed-data-dev-environments).

The Railway CLI is not installed; use the Railway connector for Railway
operations. `RAILWAY_MCP_TOKEN` remains so the dev launchers can fetch development
configuration. PR environments are named `portfolio-pr-<number>`.
Check `/tmp/.claude_remote_setup_ran` to confirm setup completed. Start services
through the root `pnpm dev`, `pnpm fastapi-dev`, or `pnpm dev-with-fastapi` commands.

## Environment constraints

- PostgreSQL can stop during long sessions. Check `pg_isready -h localhost` and
  restart the local service if needed.
- Do not name application credentials `ANTHROPIC_API_KEY` in the cloud environment;
  it conflicts with Claude Code. `api/__init__.py` bridges `SERNIA_ANTHROPIC_API_KEY`
  inside application processes. Test collection uses dummy keys from `conftest.py`.
- Outbound PostgreSQL connections may be blocked. Use the
  [load-prod-data skill](../.agents/skills/load-prod-data/SKILL.md) for authorized,
  sanitized fixture work; do not point local tests at a hosted operational database.
- HTTPS interception can require the system CA bundle. The Google client helper
  `api/src/google/common/ca_bundle.py` bridges `SSL_CERT_FILE` for httplib2.
  uv's native-TLS option affects package downloads, not application clients.
- Quo/OpenPhone's edge may reject the cloud gateway's TLS handshake. Use the Quo
  connector for read-only investigation in that case; do not weaken app TLS checks.
- Ignored request/sensitive fixtures are absent; dependent tests must skip.
  Proxy-related Logfire export warnings can also occur.

## MCP servers and connectors

Hosted services reach local and cloud sessions through claude.ai connectors
(account-level, OAuth), not `.mcp.json`. `.mcp.json` cannot reference a connector,
so an entry for a service that has one shows up as a second server that usually
fails: header tokens are absent in GUI-launched sessions, and header-less entries
need their own OAuth grant. `.mcp.json` holds only local stdio tooling (`shadcn`).

Connect these in claude.ai → Settings → Connectors:

| Connector | Used for |
|-----------|----------|
| Logfire | Traces, SQL queries, dashboards, alerts |
| Sernia AI | Workspace memory, Gmail, ClickUp, approval-gated sends |
| Quo | Contacts, messages, call transcripts |
| Neon | Database inspection and SQL (configured read-only) |
| Railway | Projects, services, deployments, logs, variables |
| cloudflare-api, Cloudflare Developer Platform | Cloudflare API search/execute; Workers, D1, KV, R2, docs search |

GitHub has no project MCP entry either; use the account's GitHub integration or
the `gh` CLI.

## Permissions and hooks

[PreToolUse guards](hooks/README.md) protect production and destructive operations
on Neon and Railway. Preserve them when changing tool allowlists. Allowlist entries
match MCP server names case-sensitively, and connector prefixes vary by client: the
desktop app exposes connectors under an opaque ID, so name-based rules such as
`mcp__Logfire` or `mcp__Railway__list-projects` do not apply to them there. The
guards therefore match on tool name under any server prefix.
Check current tool names and argument shapes before updating rules; renamed tools
can bypass intended matchers or unexpectedly prompt for approval.
