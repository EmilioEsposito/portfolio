#!/bin/bash

# Script to upsert Railway database environment variables using a GraphQL query file
# This script can be used both in GitHub Actions and locally for testing
#
# Required environment variables:
#   RAILWAY_API_TOKEN - Railway API token
#   RAILWAY_PROJECT_ID - Railway project ID
#   RAILWAY_ENV_ID - Railway environment ID
#   RAILWAY_FASTAPI_SERVICE_ID - Railway service ID
#   DB_URL_POOLED - Pooled database URL
#   DB_URL_UNPOOLED - Unpooled database URL
#   INFORMATIONAL_NEON_BRANCH_NAME - Informational branch name
#
# Usage:
#   export RAILWAY_API_TOKEN="your-token"
#   export RAILWAY_PROJECT_ID="your-project-id"
#   # ... set other vars ...
#   ./.github/scripts/railway_upsert_database_vars.sh

set -e

# Get script directory and resolve query file path
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUERY_FILE="$SCRIPT_DIR/../queries/railway_upsert_database_vars.graphql"

# Required environment variables
REQUIRED_VARS=(
  "RAILWAY_API_TOKEN"
  "RAILWAY_PROJECT_ID"
  "RAILWAY_ENV_ID"
  "RAILWAY_FASTAPI_SERVICE_ID"
  "DB_URL_POOLED"
  "DB_URL_UNPOOLED"
  "INFORMATIONAL_NEON_BRANCH_NAME"
)

# Validate required environment variables
MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var}" ]; then
    MISSING_VARS+=("$var")
  fi
done

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
  echo "Error: One or more required environment variables are not set" >&2
  echo "Missing variables:" >&2
  for var in "${MISSING_VARS[@]}"; do
    echo "  - $var" >&2
  done
  exit 1
fi

# Check if query file exists
if [ ! -f "$QUERY_FILE" ]; then
  echo "Error: Query file not found at $QUERY_FILE" >&2
  exit 1
fi

# Read the query file and substitute variables using sed
QUERY=$(sed \
  -e "s|\${RAILWAY_PROJECT_ID}|$RAILWAY_PROJECT_ID|g" \
  -e "s|\${RAILWAY_ENV_ID}|$RAILWAY_ENV_ID|g" \
  -e "s|\${RAILWAY_FASTAPI_SERVICE_ID}|$RAILWAY_FASTAPI_SERVICE_ID|g" \
  -e "s|\${DB_URL_POOLED}|$DB_URL_POOLED|g" \
  -e "s|\${DB_URL_UNPOOLED}|$DB_URL_UNPOOLED|g" \
  -e "s|\${INFORMATIONAL_NEON_BRANCH_NAME}|$INFORMATIONAL_NEON_BRANCH_NAME|g" \
  "$QUERY_FILE")

# Use jq to properly construct the JSON payload with the multi-line query
# jq handles all JSON escaping automatically (quotes, newlines, etc.)
# -R: raw input (don't parse as JSON)
# -s: slurp (read all input into a single string)
PAYLOAD=$(printf '%s' "$QUERY" | jq -Rs '{query: .}')

# Make the API call
RESPONSE=$(curl -s --fail --request POST \
  --url https://backboard.railway.com/graphql/v2 \
  --header "Authorization: Bearer $RAILWAY_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data "$PAYLOAD")

# Check for HTTP errors (curl --fail will exit non-zero, but we check anyway)
if [ $? -ne 0 ]; then
  echo "Error: Failed to update Railway environment variables (HTTP error)" >&2
  exit 1
fi

# Check for GraphQL errors
if echo "$RESPONSE" | jq -e '.errors' > /dev/null 2>&1; then
  echo "Error: Railway API returned GraphQL errors when updating environment variables:" >&2
  echo "$RESPONSE" | jq -r '.errors[] | "  - \(.message)"' >&2
  exit 1
fi

# Success
echo "Updated DATABASE_URL, DATABASE_URL_UNPOOLED, and INFORMATIONAL_NEON_BRANCH_NAME for Railway environment"
