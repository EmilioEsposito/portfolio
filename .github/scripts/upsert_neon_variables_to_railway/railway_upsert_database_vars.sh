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
#   ./.github/scripts/upsert_neon_variables_to_railway/railway_upsert_database_vars.sh

set -e

# Configuration: Set to true to skip automatic Railway deploys, false to trigger deploys
SKIP_DEPLOYS=true

echo "Step 1: Validating environment variables..."

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
  echo "✗ Error: One or more required environment variables are not set" >&2
  echo "Missing variables:" >&2
  for var in "${MISSING_VARS[@]}"; do
    echo "  - $var" >&2
  done
  exit 1
fi

echo "✓ All required environment variables are set"
echo "  - RAILWAY_PROJECT_ID: ${RAILWAY_PROJECT_ID:0:8}..."
echo "  - RAILWAY_ENV_ID: ${RAILWAY_ENV_ID:0:8}..."
echo "  - RAILWAY_FASTAPI_SERVICE_ID: ${RAILWAY_FASTAPI_SERVICE_ID:0:8}..."
echo "  - DB_URL_POOLED: ${DB_URL_POOLED:0:30}..."
echo "  - DB_URL_UNPOOLED: ${DB_URL_UNPOOLED:0:30}..."
echo "  - INFORMATIONAL_NEON_BRANCH_NAME: $INFORMATIONAL_NEON_BRANCH_NAME"

echo ""
echo "Step 2: Building input object..."

# Build variables as a JSON object
VARIABLES_JSON=$(jq -n \
  --arg db_url "$DB_URL_POOLED" \
  --arg db_url_unpooled "$DB_URL_UNPOOLED" \
  --arg branch_name "$INFORMATIONAL_NEON_BRANCH_NAME" \
  '{
    DATABASE_URL: $db_url,
    DATABASE_URL_UNPOOLED: $db_url_unpooled,
    INFORMATIONAL_NEON_BRANCH_NAME: $branch_name
  }')

# Build the full input object for VariableCollectionUpsertInput
INPUT_JSON=$(jq -n \
  --arg projectId "$RAILWAY_PROJECT_ID" \
  --arg environmentId "$RAILWAY_ENV_ID" \
  --arg serviceId "$RAILWAY_FASTAPI_SERVICE_ID" \
  --argjson variables "$VARIABLES_JSON" \
  --argjson skipDeploys "$SKIP_DEPLOYS" \
  '{
    projectId: $projectId,
    environmentId: $environmentId,
    serviceId: $serviceId,
    variables: $variables,
    replace: false,
    skipDeploys: $skipDeploys # If true AND Railway has "Wait for CI" enabled (and working), we avoid duplicate deploys.
  }')

echo "✓ Input object built"
echo "$INPUT_JSON" | jq '.' | head -10
echo "  ..."

echo ""
echo "Step 3: Building GraphQL query..."
# Use a parameterized GraphQL query to avoid syntax errors with JSON objects
QUERY="mutation variableCollectionUpsert(\$input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: \$input) }"

echo "✓ GraphQL query defined"

echo ""
echo "Step 4: Creating JSON payload..."
# Create final payload with query and variables
PAYLOAD=$(jq -n \
  --arg query "$QUERY" \
  --argjson input "$INPUT_JSON" \
  '{
    query: $query,
    variables: {
      input: $input
    }
  }')
echo "✓ Payload created"

echo ""
echo "Step 5: Making API call to Railway..."
echo "  URL: https://backboard.railway.com/graphql/v2"
# Make the API call (don't use --fail so we can capture the response even on errors)
# Temporarily disable set -e to handle curl errors gracefully
set +e
RESPONSE=$(curl -s -w "\n%{http_code}" --request POST \
  --url https://backboard.railway.com/graphql/v2 \
  --header "Authorization: Bearer $RAILWAY_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data "$PAYLOAD")
CURL_EXIT_CODE=$?
set -e

# Extract HTTP status code (last line) and response body
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | sed '$d')

# Check for HTTP errors
if [ $CURL_EXIT_CODE -ne 0 ]; then
  echo "✗ Error: curl command failed (exit code: $CURL_EXIT_CODE)" >&2
  echo "This usually indicates a network error or connection issue." >&2
  if [ -n "$RESPONSE_BODY" ]; then
    echo "Response received:" >&2
    echo "$RESPONSE_BODY" | head -50 >&2
  fi
  exit 1
fi

# Check HTTP status code
if [ "$HTTP_CODE" != "200" ]; then
  echo "✗ Error: Railway API returned HTTP $HTTP_CODE" >&2
  echo "Response:" >&2
  if [ -n "$RESPONSE_BODY" ]; then
    echo "$RESPONSE_BODY" | jq '.' 2>/dev/null || echo "$RESPONSE_BODY" | head -50
  else
    echo "(empty response)"
  fi
  exit 1
fi

echo "✓ API call completed (HTTP $HTTP_CODE)"
RESPONSE="$RESPONSE_BODY"

echo "✓ API call completed (HTTP 200)"

echo ""
echo "Step 6: Checking for GraphQL errors..."
# Check for GraphQL errors
if echo "$RESPONSE" | jq -e '.errors' > /dev/null 2>&1; then
  echo "✗ Error: Railway API returned GraphQL errors when updating environment variables:" >&2
  echo "$RESPONSE" | jq -r '.errors[] | "  - \(.message)"' >&2
  echo ""
  echo "Full response:" >&2
  echo "$RESPONSE" | jq '.' >&2
  exit 1
fi

echo "✓ No GraphQL errors detected"

echo ""
echo "Step 7: Verifying response..."
# Check if we got a successful response
if echo "$RESPONSE" | jq -e '.data.variableCollectionUpsert' > /dev/null 2>&1; then
  echo "✓ Success! Response contains variableCollectionUpsert data"
  echo "Response summary:"
  echo "$RESPONSE" | jq '.data.variableCollectionUpsert' | head -10
else
  echo "⚠ Warning: Response structure unexpected"
  echo "Full response:"
  echo "$RESPONSE" | jq '.' | head -20
fi

# Success
echo ""
echo "=========================================="
echo "✓ Successfully updated Railway environment variables:"
echo "  - DATABASE_URL"
echo "  - DATABASE_URL_UNPOOLED"
echo "  - INFORMATIONAL_NEON_BRANCH_NAME"
echo ""
if [ "$SKIP_DEPLOYS" = "true" ]; then
  echo "Railway will NOT automatically redeploy (skipDeploys=true)."
  echo "Deployments will be triggered by CI completion if 'Wait for CI' is enabled."
else
  echo "Railway will automatically redeploy the environment (skipDeploys=false)."
fi
echo "=========================================="