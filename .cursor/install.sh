#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status.

# This script installs PROJECT-SPECIFIC dependencies.
# Base tools (Node.js, pnpm, Python, uv, PostgreSQL) are already installed in the snapshot.

echo ">>> Running Cursor agent install script (.cursor/install.sh)..."
echo ">>> Working directory: $(pwd)"
echo ">>> User: $(whoami)"

echo ">>> Verifying base tools from snapshot..."
echo "Node version: $(node --version)"
echo "pnpm version: $(pnpm --version)"
echo "Python version: $(python3 --version)"
echo "uv version: $(uv --version)"
echo "PostgreSQL version: $(psql --version)"

cd /workspace

# =====================================
# Node.js Dependencies
# =====================================
echo ">>> Installing Node.js project dependencies with pnpm..."
if [ -f "pnpm-lock.yaml" ]; then
    pnpm install --frozen-lockfile
else
    echo "Warning: pnpm-lock.yaml not found. Running 'pnpm install'."
    pnpm install
fi

# =====================================
# Playwright Setup
# =====================================
# Playwright system dependencies are in the snapshot.
# Just ensure project-specific Playwright browsers are installed/updated.
echo ">>> Updating Playwright browsers to match project version..."
pnpm --filter web-nextjs exec playwright install --with-deps
echo "✓ Playwright browsers updated"

# =====================================
# Python Dependencies
# =====================================
echo ">>> Installing Python project dependencies into /workspace/.venv with uv..."
if [ -f "uv.lock" ]; then
    uv sync --locked
else
    echo "Warning: uv.lock not found. Running 'uv sync'."
    uv sync
fi

echo ">>> Installing Python dev dependencies..."
if [ -f "pyproject.toml" ]; then
    if [ -f "uv.lock" ]; then
        uv sync --dev --locked
    else
        uv sync --dev
    fi
else
    echo "Skipping dev dependencies: pyproject.toml not found."
fi

# =====================================
# PostgreSQL Setup
# =====================================
echo ">>> Setting up PostgreSQL..."

# PostgreSQL is already installed in the snapshot, just need to start it
echo "Starting PostgreSQL service..."
sudo service postgresql start || echo "⚠ PostgreSQL may already be running"
sleep 2
echo "✓ PostgreSQL service started"

# Create user and database (idempotent)
echo "Creating portfolio user and database..."
sudo -u postgres psql -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>&1 && echo "✓ User created" || echo "⚠ User already exists"
sudo -u postgres psql -c "CREATE DATABASE portfolio OWNER portfolio;" 2>&1 && echo "✓ Database created" || echo "⚠ Database already exists"

# =====================================
# Database Migrations
# =====================================
echo ">>> Running database migrations..."
uv run alembic upgrade head
echo "✓ Database migrations complete"

# =====================================
# Environment Configuration
# =====================================
echo ">>> Configuring PYTHONPATH..."
export PYTHONPATH=/workspace:$PYTHONPATH
echo "✓ PYTHONPATH set to: $PYTHONPATH"

echo ""
echo "✅ Cursor agent install script finished successfully!"
echo ""
echo "Available terminals:"
echo "  - NextJS: pnpm --filter web-nextjs dev"
echo "  - FastAPI: pnpm fastapi-dev"
echo "  - ExpoWeb: pnpm my-expo-app start --web"
echo "" 