# PreToolUse Hooks

These hooks implement parameter-based permission control for MCP tools. Claude Code's `settings.json` permissions are all-or-nothing per tool - these hooks add granular control based on tool arguments.

## Philosophy

**Protect production, allow development freely.**

- Read operations are always safe
- Write operations to non-production environments should flow without friction
- Production modifications require explicit confirmation
- Destructive operations (deletes) always require confirmation

## Railway (`railway-guard.sh`)

| Call | Decision |
|------|----------|
| Any `delete-*` tool | ask |
| Write tool with no `environmentId` | ask |
| Write tool targeting a production `environmentId` | ask |
| Write tool targeting any other environment | no decision (normal permissions apply) |
| Read tools | no hook |

**Why:** The Railway connector has no link step. Write tools take an `environmentId`,
and when it is omitted they act on `production` or on every environment.

Production environment IDs are listed in `PRODUCTION_ENVIRONMENT_IDS` at the top of the
script; add an ID there to guard another project. The matcher lists the connector's
write tools under any server prefix. Other connectors reuse a few of those names, so
the script ignores calls that neither come from a server named Railway nor carry a
`projectId` or `environmentId`.

## Neon (`neon-guard.sh`)

| Call | Decision | Rationale |
|------|----------|-----------|
| `delete_branch`, `delete_project` | ask | Permanently destroys data |
| `complete_database_migration`, `complete_query_tuning` | ask | Applies changes to the parent (production) branch |
| `run_sql` with provably read-only SQL, any branch | allow | A read cannot change production |
| `explain_sql_statement` without `analyze` | allow | Plans the statement without executing it |
| `explain_sql_statement` with `analyze` | same as `run_sql` | `EXPLAIN ANALYZE` executes the statement |
| Other SQL on a non-production branch of a listed project | allow | Safe sandbox |
| Other SQL with no `branch_id`, on production, or on an unlisted project | ask | Could write to production |
| `run_sql_transaction` | never treated as a read | Multi-statement; follows the two rows above |
| Other read-only tools | no hook | No modification risk |

The matcher is `mcp__.*__(<tool>|…)`, so it applies to the Neon connector under any
server prefix, including the desktop app's opaque connector IDs.

**Production is identified by ID.** `branch_id` takes a `br-…` ID and is optional; when it
is omitted Neon uses the default branch, which is production. `SANDBOX_PROJECTS` at the top
of the script maps each project ID to its production branch ID. SQL that is not a read runs
without a prompt only on another branch of a listed project. Add a line there to give
another project a sandbox.

**What counts as a read.** One statement that starts with `SELECT`, `WITH`, `SHOW`,
`EXPLAIN`, `VALUES` or `TABLE`, contains none of the words in `WRITE_WORDS` (for example
`INSERT`, `UPDATE`, `DELETE`, `INTO`), and calls none of the functions in
`SIDE_EFFECT_FUNCTION_PREFIXES` (for example `setval`, `pg_terminate_backend`). The script
does not parse comments or string literals, so a query that mentions `update` in a literal
or starts with a comment asks. Rephrase the query or approve the prompt.

**The check stops accidents; the connector is the boundary.** A word list cannot know
whether a user-defined function writes. The Neon connector is configured read-only, and
Neon runs its `run_sql` in a read-only transaction
(`SELECT current_setting('transaction_read_only')` returns `on`). Keep the connector
read-only for this guard's allow rule to be safe. A write-capable Neon server still gets a
prompt for everything the word lists catch.

## Ruff auto-format (`ruff-format.sh`) — PostToolUse

Unlike the guards above (which are `PreToolUse` and gate production), this is a
`PostToolUse` hook on `Edit|Write|MultiEdit`. After Claude edits a `.py` file it
runs `ruff check --fix` + `ruff format` on that one file, so formatting is
*fixed* locally instead of only *checked* in CI.

**Why:** CI runs `ruff check .` + `ruff format --check .`, which fail the build
but don't fix anything. A Claude-authored edit (or a raw `cat >>` heredoc
append) could reach CI unformatted and redden the PR for a purely mechanical
reason. This closes that gap at the source.

| Property | Behavior |
|----------|----------|
| Trigger | `PostToolUse` on `Edit`, `Write`, `MultiEdit` |
| Scope | Only the edited file, and only when it ends in `.py` and exists |
| Config | Auto-discovers the nearest `pyproject.toml` / `ruff.toml`, so `apps/sernia_mcp/` files use that subproject's config |
| Failure mode | Fail-soft — always exits 0. Missing ruff, non-`.py` file, or a parse error is a silent no-op; it never blocks an edit |

It does **not** cover human hand-edits (only Claude's tool calls). If you want a
committer-agnostic guard too, add `pre-commit` with `ruff-pre-commit`.

## Adding New Hooks

1. Create a bash script in this directory
2. Read JSON from stdin: `INPUT=$(cat)`
3. Extract parameters with jq: `echo "$INPUT" | jq -r '.tool_input.paramName'`
4. Output a decision as `hookSpecificOutput` with `permissionDecision` (`allow`, `ask`
   or `deny`) and `permissionDecisionReason`, or print nothing to leave the call to
   normal permissions
5. Register in `.claude/settings.json` under `hooks.PreToolUse`

## Testing Hooks

```bash
echo '{"tool_name":"mcp__Neon__run_sql","tool_input":{"sql":"SELECT 1"}}' | ./neon-guard.sh
# permissionDecision "allow": read-only SQL

echo '{"tool_name":"mcp__Neon__run_sql","tool_input":{"sql":"DELETE FROM t"}}' | ./neon-guard.sh
# permissionDecision "ask": not provably read-only, no branch_id (defaults to production)

echo '{"tool_name":"mcp__Railway__set-variables","tool_input":{"projectId":"p","variables":{}}}' | ./railway-guard.sh
# permissionDecision "ask": no environmentId

echo '{"tool_name":"mcp__Railway__set-variables","tool_input":{"projectId":"p","environmentId":"some-pr-env","variables":{}}}' | ./railway-guard.sh
# no output
```

`scripts/tests/neon-guard.test.cjs` covers the Neon guard's decision table and runs in CI
with `pnpm test:security`. Add a case there when you change the script.
