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

| Operation | Decision | Rationale |
|-----------|----------|-----------|
| `delete_branch`, `delete_project` | ask | Permanently destroys data |
| `complete_database_migration` | ask | Applies DDL to parent (main) branch |
| `complete_query_tuning` | ask | Applies changes to main branch |
| `run_sql` without `branchId` | ask | Neon defaults to main branch when omitted |
| `run_sql` on main/production branch | ask | Direct production write |
| `run_sql` on PR/feature branches | allow | Safe sandbox |
| `reset_from_parent` | allow | Only refreshes child from main, doesn't modify production |
| All read-only tools | allow (no hook) | No modification risk |

The matcher is `mcp__.*__(<tool>|…)`, so it applies to the Neon connector under any
server prefix, including the desktop app's opaque connector IDs.

**Key insight:** Neon's `branchId` parameter is optional - if omitted, it defaults to the main branch. This is the primary risk vector for accidental production writes.

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

## Worktree provisioning (`worktree-provision.sh`) — SessionStart

Runs `scripts/worktree-provision.sh <checkout> --once` for the checkout the session works
in, which first moves a brand-new branch to freshly fetched `origin/main`. See
[the worktree guide](../../docs/WORKTREES.md#codex-and-claude).

**Why a wrapper:** desktop worktree sessions run hooks in the main checkout with
`CLAUDE_PROJECT_DIR` pointing there, and the provisioner skips the main checkout. The
session's checkout is the hook input's `cwd`. The wrapper runs that checkout's own
provisioner, and exits 0 outside a Git checkout or where the script is absent.

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
# permissionDecision "ask": SQL with no branchId (defaults to main)

echo '{"tool_name":"mcp__Neon__run_sql","tool_input":{"sql":"SELECT 1","branchId":"br-pr-123"}}' | ./neon-guard.sh
# permissionDecision "allow"

echo '{"tool_name":"mcp__Railway__set-variables","tool_input":{"projectId":"p","variables":{}}}' | ./railway-guard.sh
# permissionDecision "ask": no environmentId

echo '{"tool_name":"mcp__Railway__set-variables","tool_input":{"projectId":"p","environmentId":"some-pr-env","variables":{}}}' | ./railway-guard.sh
# no output
```
