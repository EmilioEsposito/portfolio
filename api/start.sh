#!/bin/sh
set -e

echo ">>> Starting FastAPI application..."

# Run database migrations
echo ">>> Running database migrations..."
alembic upgrade head
echo ">>> Migrations completed successfully"

# Start the Hypercorn server
echo ">>> Starting Hypercorn server..."
exec hypercorn api.index:app --bind [::]:${PORT:-8000} --keep-alive 120

