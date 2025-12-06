#!/bin/bash
# Setup script for Claude Code remote environment
# This script runs on SessionStart when CLAUDE_CODE_REMOTE="true"

# Only run in remote environments
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  echo "Not in remote environment, skipping setup"
  exit 0
fi

echo "=== Claude Code Remote Setup ==="

# Create a flag file to confirm the hook ran (useful for debugging)
HOOK_FLAG_FILE="/tmp/.claude_remote_setup_ran"
echo "$(date -Iseconds)" > "$HOOK_FLAG_FILE"
echo "✓ Hook execution flag created at $HOOK_FLAG_FILE"

# Get the project directory (where this script lives is .claude/, go up one level)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Project directory: $PROJECT_DIR"

# =============================================================================
# PYTHON ENVIRONMENT
# =============================================================================
echo ""
echo "--- Python Environment Setup ---"

echo "Step 1: Creating Python virtual environment..."
uv venv
echo "✓ Virtual environment created"

echo "Step 2: Installing Python dependencies..."
source .venv/bin/activate
uv sync --all-packages -p python3.11
echo "✓ Python dependencies installed"

echo "Step 3: Configuring PYTHONPATH..."
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "export PYTHONPATH=\"$PROJECT_DIR:\$PYTHONPATH\"" >> "$CLAUDE_ENV_FILE"
  echo "✓ PYTHONPATH persisted to session"
fi
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

# =============================================================================
# DATABASE
# =============================================================================
echo ""
echo "--- Database Setup ---"

# Fix SSL certificate permissions (required for PostgreSQL to start)
echo "Step 4: Fixing SSL certificate permissions..."
if [ -f /etc/ssl/private/ssl-cert-snakeoil.key ]; then
  chmod 600 /etc/ssl/private/ssl-cert-snakeoil.key 2>/dev/null || true
  echo "✓ SSL key permissions fixed"
fi

# Configure pg_hba.conf for trust authentication (needed because sudo is broken
# in this cloud environment and PostgreSQL runs as the 'claude' user)
echo "Step 5: Configuring PostgreSQL authentication..."
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
if [ -f "$PG_HBA" ]; then
  # Change local authentication from peer to trust
  sed -i 's/local   all             postgres                                peer/local   all             postgres                                trust/' "$PG_HBA" 2>/dev/null || true
  sed -i 's/local   all             all                                     peer/local   all             all                                     trust/' "$PG_HBA" 2>/dev/null || true
  # Change host authentication from scram-sha-256 to trust for local connections
  sed -i 's/host    all             all             127.0.0.1\/32            scram-sha-256/host    all             all             127.0.0.1\/32            trust/' "$PG_HBA" 2>/dev/null || true
  # Fix ownership so PostgreSQL (running as 'claude') can read it
  chown claude:claude "$PG_HBA" 2>/dev/null || true
  echo "✓ PostgreSQL authentication configured for trust mode"
fi

echo "Step 6: Starting PostgreSQL service..."
service postgresql start 2>/dev/null || true
sleep 2
if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  echo "✓ PostgreSQL service started"
else
  echo "⚠ PostgreSQL may have failed to start, check logs"
fi

echo "Step 7: Creating portfolio user..."
psql -U postgres -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>/dev/null && echo "✓ User created" || echo "⚠ User may already exist"

echo "Step 8: Creating portfolio database..."
psql -U postgres -c "CREATE DATABASE portfolio OWNER portfolio;" 2>/dev/null && echo "✓ Database created" || echo "⚠ Database may already exist"

# Persist DATABASE_URL environment variables for the session
# These match docker-compose.yml: postgresql://portfolio:portfolio@localhost:5432/portfolio
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "Step 9: Persisting database environment variables..."
  echo 'export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_REQUIRE_SSL="false"' >> "$CLAUDE_ENV_FILE"
  echo "✓ Environment variables persisted to session"
fi

# Set for current script execution
export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_REQUIRE_SSL="false"

echo "Step 10: Running database migrations..."
# Run alembic from project root where alembic.ini is located
alembic upgrade head 2>/dev/null || echo "⚠ Migrations may have issues, but continuing..."
echo "✓ Migrations complete"

echo "Step 11: Seeding database..."
# Seed script reads from environment variables: EMILIO_EMAIL, EMILIO_PHONE, SERNIA_EMAIL, SERNIA_PHONE
uv run python apps/api/seed_db.py
echo "✓ Database seeded"

# =============================================================================
# REACT ROUTER (Frontend)
# =============================================================================
echo ""
echo "--- React Router Setup ---"

echo "Step 12: Installing pnpm dependencies..."
pnpm install
echo "✓ pnpm dependencies installed"

echo "Step 13: Building React Router app..."
pnpm build
echo "✓ React Router app built"

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "=== Remote Setup Complete ==="
echo ""
echo "Available commands:"
echo "  pnpm dev              - Start React Router dev server (port 5173)"
echo "  pnpm fastapi-dev      - Start FastAPI dev server (port 8000)"
echo "  pnpm dev-with-fastapi - Start both servers"
echo "  pnpm prefect-dev      - Run Prefect flows"
echo "  python apps/api/seed_db.py - Seed the database (interactive)"
echo ""
exit 0
