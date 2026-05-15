#!/bin/bash
# Guard Neon operations: require confirmation for destructive actions on main/production

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
BRANCH_NAME=$(echo "$INPUT" | jq -r '.tool_input.branchName // .tool_input.branch // empty')

# Delete operations always require confirmation
if [[ "$TOOL_NAME" == *"delete_branch"* ]] || [[ "$TOOL_NAME" == *"delete_project"* ]]; then
  echo '{"decision":"ask","reason":"Destructive Neon operation - requires confirmation"}'
  exit 0
fi

# SQL on main/production branch requires confirmation
if [[ "$TOOL_NAME" == *"run_sql"* ]]; then
  if [ "$BRANCH_NAME" = "main" ] || [ "$BRANCH_NAME" = "production" ] || [ -z "$BRANCH_NAME" ]; then
    echo '{"decision":"ask","reason":"SQL on main/production branch - requires confirmation"}'
    exit 0
  fi
fi

echo '{"decision":"allow"}'
