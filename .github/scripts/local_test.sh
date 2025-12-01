#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file in the same directory
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "✓ Loading environment variables from $SCRIPT_DIR/.env"
  set -a  # automatically export all variables
  source "$SCRIPT_DIR/.env"
  set +a  # stop automatically exporting
  echo "✓ Environment variables loaded"
else
  echo "Error: .env file not found at $SCRIPT_DIR/.env" >&2
  echo "Please create a .env file with the required variables:" >&2
  echo "  RAILWAY_API_TOKEN" >&2
  echo "  RAILWAY_PROJECT_ID" >&2
  echo "  RAILWAY_ENV_ID" >&2
  echo "  RAILWAY_FASTAPI_SERVICE_ID" >&2
  echo "  DB_URL_POOLED" >&2
  echo "  DB_URL_UNPOOLED" >&2
  echo "  INFORMATIONAL_NEON_BRANCH_NAME" >&2
  exit 1
fi

# Run the railway_upsert_database_vars.sh script
echo ""
echo "=========================================="
echo "Running railway_upsert_database_vars.sh..."
echo "=========================================="
"$SCRIPT_DIR/railway_upsert_database_vars.sh"
EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
  echo "✓ Script completed successfully (exit code: $EXIT_CODE)"
else
  echo "✗ Script failed (exit code: $EXIT_CODE)"
fi
echo "=========================================="
exit $EXIT_CODE