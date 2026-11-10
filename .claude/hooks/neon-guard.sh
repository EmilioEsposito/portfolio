#!/bin/bash
# Guard Neon operations: require confirmation for destructive actions on main/production
# Key insight: branchId is optional - if omitted, Neon defaults to main branch

decide() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s","permissionDecisionReason":"%s"}}\n' "$1" "$2"
  exit 0
}

# Fail closed: without jq the call cannot be inspected.
command -v jq >/dev/null 2>&1 || decide ask "Neon guard cannot inspect this call (jq missing) - requires confirmation"

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
BRANCH_ID=$(echo "$INPUT" | jq -r '.tool_input.branchId // empty')

# Always destructive - require confirmation regardless of branch
if [[ "$TOOL_NAME" == *"delete_branch"* ]] || \
   [[ "$TOOL_NAME" == *"delete_project"* ]]; then
  decide ask "Destructive Neon operation - requires confirmation"
fi

# Migration completion applies to parent (usually main) - always ask
if [[ "$TOOL_NAME" == *"complete_database_migration"* ]] || \
   [[ "$TOOL_NAME" == *"complete_query_tuning"* ]]; then
  decide ask "Applies changes to parent/main branch - requires confirmation"
fi

# SQL operations: ask if no branchId (defaults to main) or explicitly main/production
if [[ "$TOOL_NAME" == *"run_sql"* ]]; then
  if [ -z "$BRANCH_ID" ]; then
    decide ask "SQL with no branchId (defaults to main) - requires confirmation"
  fi
  # Check for explicit main/production branch names or IDs containing "main"
  if [[ "$BRANCH_ID" == "main" ]] || [[ "$BRANCH_ID" == "production" ]] || [[ "$BRANCH_ID" == *"main"* ]]; then
    decide ask "SQL on main/production branch - requires confirmation"
  fi
fi

decide allow "Neon operation on a non-production branch"
