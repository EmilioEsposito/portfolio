#!/bin/bash
# Guard Railway connector writes: require confirmation when a call can reach production.
# The connector has no "link" step. Write tools take an environmentId, and when it is
# omitted they act on production or on every environment.
# This hook only ever adds a prompt; non-production calls fall through to normal permissions.

# Railway environment IDs that count as production.
PRODUCTION_ENVIRONMENT_IDS=(
  "50022d5c-db02-4a48-8b5c-864f04af5118" # portfolio / production
)

ask() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

# Fail closed: without jq the call cannot be inspected.
command -v jq >/dev/null 2>&1 || ask "Railway guard cannot inspect this call (jq missing) - requires confirmation"

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
ENV_ID=$(echo "$INPUT" | jq -r '.tool_input.environmentId // empty')
PROJECT_ID=$(echo "$INPUT" | jq -r '.tool_input.projectId // empty')
ACTION="${TOOL_NAME##*__}"

# The matcher is prefix-agnostic and other connectors reuse a few of these tool names.
# Railway calls are recognised by server name or by their projectId/environmentId arguments.
shopt -s nocasematch
if [[ "$TOOL_NAME" != *railway* ]] && [ -z "$PROJECT_ID" ] && [ -z "$ENV_ID" ]; then
  exit 0
fi
shopt -u nocasematch

if [[ "$ACTION" == delete-* ]]; then
  ask "Destructive Railway operation - requires confirmation"
fi

if [ -z "$ENV_ID" ]; then
  ask "Railway write with no environmentId (production or all environments) - requires confirmation"
fi

for PRODUCTION_ID in "${PRODUCTION_ENVIRONMENT_IDS[@]}"; do
  if [ "$ENV_ID" = "$PRODUCTION_ID" ]; then
    ask "Railway production environment - requires confirmation"
  fi
done

exit 0
