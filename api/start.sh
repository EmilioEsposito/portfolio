#!/bin/sh
set -e


# Wait for database to be ready before running migrations
echo ">>> Waiting for database to be ready..."
python3 -c "import sys; sys.path.insert(0, 'api'); from src.database.database import wait_for_db; wait_for_db()"

# Run database migrations
echo ">>> Running database migrations..."
alembic upgrade head
echo ">>> Migrations completed successfully"

# Start the Hypercorn server
# Command to run the Hypercorn server
# It will find api.index:app starting from the PYTHONPATH (/app)
# Uses the PORT environment variable if set by the platform (e.g., Railway), otherwise defaults to 8000.
# Binds to IPv6 ([::]) as required for Railway Private Networking.
echo ">>> Starting FastAPI with Hypercorn server..."
exec hypercorn api.index:app --bind [::]:${PORT:-8000} --keep-alive 120

