#!/bin/bash
# Setup script for Claude Code remote environment
# This script runs on SessionStart when CLAUDE_CODE_REMOTE="true"

# Only run in remote environments
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  echo "Not in remote environment, skipping setup"
  exit 0
fi

echo "=== Claude Code Remote Setup ==="

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
uv sync -p python3.11
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

echo "Step 4: Starting PostgreSQL service..."
sudo service postgresql start
sleep 2
echo "✓ PostgreSQL service started"

echo "Step 5: Creating portfolio user..."
sudo -u postgres psql -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>/dev/null && echo "✓ User created" || echo "⚠ User may already exist"

echo "Step 6: Creating portfolio database..."
sudo -u postgres psql -c "CREATE DATABASE portfolio OWNER portfolio;" 2>/dev/null && echo "✓ Database created" || echo "⚠ Database may already exist"

# Persist DATABASE_URL environment variables for the session
# These match docker-compose.yml: postgresql://portfolio:portfolio@localhost:5432/portfolio
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "Step 7: Persisting database environment variables..."
  echo 'export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_REQUIRE_SSL="false"' >> "$CLAUDE_ENV_FILE"
  echo "✓ Environment variables persisted to session"
fi

# Set for current script execution
export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_REQUIRE_SSL="false"

echo "Step 8: Running database migrations..."
cd api
uv run alembic upgrade head
cd ..
echo "✓ Migrations complete"

echo "Step 9: Seeding database..."
# Seed script reads from environment variables: EMILIO_EMAIL, EMILIO_PHONE, SERNIA_EMAIL, SERNIA_PHONE
uv run python api/seed_db.py
echo "✓ Database seeded"

# =============================================================================
# REACT ROUTER (Frontend)
# =============================================================================
echo ""
echo "--- React Router Setup ---"

echo "Step 10: Installing pnpm dependencies..."
pnpm install
echo "✓ pnpm dependencies installed"

echo "Step 11: Building React Router app..."
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
echo "  python api/seed_db.py - Seed the database (interactive)"
echo ""
exit 0
