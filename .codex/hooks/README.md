# PreToolUse Hooks

These hooks implement parameter-based permission control for MCP tools. Claude Code's `settings.json` permissions are all-or-nothing per tool - these hooks add granular control based on tool arguments.

## Philosophy

**Protect production, allow development freely.**

- Read operations are always safe
- Write operations to non-production environments should flow without friction
- Production modifications require explicit confirmation
- Destructive operations (deletes) always require confirmation

## Railway (`railway-link-guard.sh`)

| Environment | Decision |
|-------------|----------|
| `production` | ask |
| All others (dev, PR envs, etc.) | allow |

**Why:** Prevents accidental production deploys/changes. Development and PR environments are sandboxed.

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

**Key insight:** Neon's `branchId` parameter is optional - if omitted, it defaults to the main branch. This is the primary risk vector for accidental production writes.

## Adding New Hooks

1. Create a bash script in this directory
2. Read JSON from stdin: `INPUT=$(cat)`
3. Extract parameters with jq: `echo "$INPUT" | jq -r '.tool_input.paramName'`
4. Output decision JSON: `{"decision":"allow"}` or `{"decision":"ask","reason":"..."}`
5. Register in `.claude/settings.json` under `hooks.PreToolUse`

## Testing Hooks

```bash
# Test with sample input
echo '{"tool_name":"mcp__Neon__run_sql","tool_input":{"sql":"SELECT 1"}}' | ./neon-guard.sh
# Should output: {"decision":"ask","reason":"SQL with no branchId (defaults to main) - requires confirmation"}

echo '{"tool_name":"mcp__Neon__run_sql","tool_input":{"sql":"SELECT 1","branchId":"br-pr-123"}}' | ./neon-guard.sh
# Should output: {"decision":"allow"}
```
