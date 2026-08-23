#!/bin/bash
# Guard Railway link-environment: require confirmation for production only
# Param name differs by @railway/mcp-server version: environmentName (old) vs environment_name (new)

INPUT=$(cat)
ENV_NAME=$(echo "$INPUT" | jq -r '.tool_input.environment_name // .tool_input.environmentName // empty')
ENV_ID=$(echo "$INPUT" | jq -r '.tool_input.environment_id // .tool_input.environmentId // empty')

if [ "$ENV_NAME" = "production" ]; then
  echo '{"decision":"ask","reason":"Production environment - requires confirmation"}'
elif [ -z "$ENV_NAME" ] && [ -n "$ENV_ID" ]; then
  # Linked by ID only - cannot tell whether it is production, so confirm
  echo '{"decision":"ask","reason":"Linking by environment_id - cannot verify it is not production"}'
else
  echo '{"decision":"allow"}'
fi
