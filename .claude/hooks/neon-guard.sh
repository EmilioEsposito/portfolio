#!/bin/bash
# Guard Neon operations: require confirmation for destructive actions on main/production
# Key insight: branchId is optional - if omitted, Neon defaults to main branch

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
BRANCH_ID=$(echo "$INPUT" | jq -r '.tool_input.branchId // empty')

# Always destructive - require confirmation regardless of branch
if [[ "$TOOL_NAME" == *"delete_branch"* ]] || \
   [[ "$TOOL_NAME" == *"delete_project"* ]] || \
   [[ "$TOOL_NAME" == *"reset_from_parent"* ]]; then
  echo '{"decision":"ask","reason":"Destructive Neon operation - requires confirmation"}'
  exit 0
fi

# Migration completion applies to parent (usually main) - always ask
if [[ "$TOOL_NAME" == *"complete_database_migration"* ]] || \
   [[ "$TOOL_NAME" == *"complete_query_tuning"* ]]; then
  echo '{"decision":"ask","reason":"Applies changes to parent/main branch - requires confirmation"}'
  exit 0
fi

# SQL operations: ask if no branchId (defaults to main) or explicitly main/production
if [[ "$TOOL_NAME" == *"run_sql"* ]]; then
  if [ -z "$BRANCH_ID" ]; then
    echo '{"decision":"ask","reason":"SQL with no branchId (defaults to main) - requires confirmation"}'
    exit 0
  fi
  # Check for explicit main/production branch names or IDs containing "main"
  if [[ "$BRANCH_ID" == "main" ]] || [[ "$BRANCH_ID" == "production" ]] || [[ "$BRANCH_ID" == *"main"* ]]; then
    echo '{"decision":"ask","reason":"SQL on main/production branch - requires confirmation"}'
    exit 0
  fi
fi

echo '{"decision":"allow"}'
