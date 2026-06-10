---
name: load-prod-data
description: Pull real production data into a dev or Claude Code cloud environment — ad hoc via the Neon MCP tools (sanitize before inserting locally), or the persistent sanitized baseline via the Railway seed bucket. Use when local/seeded data isn't enough — e.g. debugging against realistic conversations, reproducing a data-dependent bug, testing UI with real-shaped data.
---

# Load Production Data into a Dev Environment

Claude Code cloud sandboxes **cannot** reach Neon over Postgres (outbound
port 5432 is blocked), so `psql`/SQLAlchemy against real environments won't
work there. Two HTTPS paths exist instead:

| Need | Path |
|------|------|
| Ad-hoc: specific rows, a particular conversation, arbitrary tables, "what does real data look like?" | **A: Neon MCP** (seconds, already authenticated) |
| Persistent baseline: bulk realistic conversations auto-loaded at session start | **B: seed bucket** (curated by the user) |

## Path A — Ad hoc via Neon MCP (primary)

The Neon MCP tools (`mcp__Neon__run_sql`, `describe_table_schema`,
`get_database_tables`, ...) work over HTTPS from the sandbox. Production
writes are guarded by `.claude/hooks/` — reads are fine; prefer a dev/PR
branch when one has the data you need.

Recipe to copy real conversations into local Postgres:

1. Find what you want (project/branch, then query):
   ```sql
   SELECT id, modality, contact_identifier, updated_at
   FROM agent_conversations
   WHERE agent_name = 'sernia'
   ORDER BY updated_at DESC LIMIT 20;
   ```
2. Pull full rows (the `messages` JSONB can be large — fetch one id at a
   time if a query response gets truncated):
   ```sql
   SELECT id, agent_name, messages, metadata_, modality, contact_identifier
   FROM agent_conversations WHERE id = '<id>';
   ```
3. **Sanitize before inserting** — reuse the fixture pipeline's helpers
   (deterministic phone/email redaction + oversized-tool-result truncation):
   ```python
   # uv run python - <<'EOF'  (paste rows from step 2 as `rows`)
   import asyncio, json
   from api.src.utils.seed_fixture import digest, sanitize_text, sanitize_value
   from api.src.ai_demos.models import AgentConversation
   from api.src.database.database import AsyncSessionFactory  # local PG here

   async def load(rows):
       async with AsyncSessionFactory() as s:
           for r in rows:
               s.add(AgentConversation(
                   id=f"adhoc_{digest(r['id'], 10)}",   # never collide with real ids
                   agent_name=r["agent_name"],
                   clerk_user_id=None,                   # shared team access
                   messages=sanitize_value(r["messages"]),
                   metadata_={**(r.get("metadata_") or {}), "adhoc_import": True},
                   modality=r.get("modality"),
                   contact_identifier=sanitize_text(r["contact_identifier"]) if r.get("contact_identifier") else None,
               ))
           await s.commit()
   asyncio.run(load(rows))
   # EOF
   ```

Rules:
- **Always sanitize** rows containing tenant/vendor data before they land
  in the local DB (sandbox DBs are lower-trust than Neon).
- **Never write the pulled data into the repo** (no committed JSON dumps —
  `api/seed_fixtures/` is gitignored for this reason; use it for scratch).
- Same applies to any other table (contacts, emails) — `sanitize_value`
  works on any JSON-ish structure; sanity-check what you inserted.

## Path B — Persistent baseline via the seed bucket

`api/seed_db.py` (run by the session-start hook) auto-downloads a sanitized
fixture of recent conversations from the private Railway ci-bucket whenever
the `SEED_BUCKET_*` env vars are set — so a realistic baseline is usually
already in local Postgres before you start.

Refreshing that fixture is a **human** step (it includes a manual PII
review): the user runs `scripts/export_seed_fixture.py` locally, reviews,
then `--upload-only`. If the baseline is stale or missing, ask the user to
refresh it — don't try to regenerate it from the sandbox.
