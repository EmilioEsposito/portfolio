#!/bin/sh
set -e

echo ">>> Starting FastAPI application..."

# Run database migrations
echo ">>> Running database migrations..."
alembic upgrade head
echo ">>> Migrations completed successfully"

# Start the Hypercorn server
# Command to run the Hypercorn server
# It will find api.index:app starting from the PYTHONPATH (/app)
# Uses the PORT environment variable if set by the platform (e.g., Railway), otherwise defaults to 8000.
# Binds to IPv6 ([::]) as required for Railway Private Networking.
echo ">>> Starting Hypercorn server..."
exec hypercorn api.index:app --bind [::]:${PORT:-8000} --keep-alive 120

