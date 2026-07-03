#!/bin/bash
# Guard Railway link-environment: require confirmation for production only

INPUT=$(cat)
ENV_NAME=$(echo "$INPUT" | jq -r '.tool_input.environmentName // empty')

if [ "$ENV_NAME" = "production" ]; then
  echo '{"decision":"ask","reason":"Production environment - requires confirmation"}'
else
  echo '{"decision":"allow"}'
fi
