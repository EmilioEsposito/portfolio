# Claude Code cloud environment

When `CLAUDE_CODE_REMOTE=true`, the session-start hook runs `setup_remote.sh`.
It installs dependencies, starts local PostgreSQL, applies migrations, seeds
non-production demo data, and builds the web app. Optional `SEED_BUCKET_*`
configuration enables private, reviewed seed fixtures; see
[development setup](../docs/DEVELOPMENT.md#sanitized-seed-data-dev-environments).

The hook bridges `RAILWAY_MCP_TOKEN` to `RAILWAY_API_TOKEN` and links the
`development/fastapi` service. PR environments are named `portfolio-pr-<number>`.
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

## Permissions and hooks

[PreToolUse guards](hooks/README.md) protect production and destructive operations.
Preserve them when changing tool allowlists. Allowlist entries match MCP server
names case-sensitively: a connector named `Logfire` differs from local `logfire`.
Check current tool names and argument shapes before updating rules; renamed tools
can bypass intended matchers or unexpectedly prompt for approval.
