#!/bin/bash

# https://railway.com/graphiql
# Script to upsert Railway database environment variables using variableCollectionUpsert mutation
# This script can be used both in GitHub Actions and locally for testing
# Uses Railway's variableCollectionUpsert mutation which updates multiple variables in a single call
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

# Build variables string in KEY=value format (newline separated)
# This is the format required by Railway's variableCollectionUpsert mutation
VARIABLES_STRING=$(printf "DATABASE_URL=%s\nDATABASE_URL_UNPOOLED=%s\nINFORMATIONAL_NEON_BRANCH_NAME=%s" \
  "$DB_URL_POOLED" \
  "$DB_URL_UNPOOLED" \
  "$INFORMATIONAL_NEON_BRANCH_NAME")

# Use jq to properly escape the variables string for GraphQL (as JSON string)
ESCAPED_VARS=$(printf '%s' "$VARIABLES_STRING" | jq -Rs .)

# Build GraphQL query string with escaped variables embedded
# Note: We use RAILWAY_FASTAPI_SERVICE_ID for serviceId based on the workflow
QUERY_STR=$(printf 'mutation { variableCollectionUpsert(input: { projectId: "%s", environmentId: "%s", serviceId: "%s", variables: %s, replace: true, skipDeploys: false }) }' \
  "$RAILWAY_PROJECT_ID" \
  "$RAILWAY_ENV_ID" \
  "$RAILWAY_FASTAPI_SERVICE_ID" \
  "$ESCAPED_VARS")

# Create final payload
PAYLOAD=$(jq -n --arg query "$QUERY_STR" '{query: $query}')

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
echo "Redeploying Railway environment"