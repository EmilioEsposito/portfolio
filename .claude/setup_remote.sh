#!/bin/bash
# Setup script for Claude Code remote environment
# This script runs on SessionStart when CLAUDE_CODE_REMOTE="true"

# Only run in remote environments
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  echo "Not in remote environment, skipping setup"
  exit 0
fi

echo "=== Claude Code Remote Setup ==="
echo "Started: $(date -Iseconds)"

# Create a flag file to confirm the hook ran (useful for debugging)
HOOK_FLAG_FILE="/tmp/.claude_remote_setup_ran"
echo "$(date -Iseconds)" > "$HOOK_FLAG_FILE"

# Get the project directory (where this script lives is .claude/, go up one level)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Project directory: $PROJECT_DIR"

# =============================================================================
# START PNPM INSTALL IN BACKGROUND (slow — ~30s of network downloads)
# =============================================================================
echo ""
echo "--- Starting pnpm install in background ---"
pnpm install > /tmp/pnpm_install.log 2>&1 &
PNPM_PID=$!

# =============================================================================
# GITHUB CLI
# =============================================================================
echo ""
echo "--- GitHub CLI Setup ---"
if ! command -v gh &>/dev/null; then
  echo "Installing GitHub CLI..."
  GH_VERSION="2.65.0"
  GH_ARCHIVE="gh_${GH_VERSION}_linux_amd64.tar.gz"
  curl -fsSL -o "/tmp/${GH_ARCHIVE}" "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${GH_ARCHIVE}" \
    && tar -xzf "/tmp/${GH_ARCHIVE}" -C /tmp \
    && cp "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
    && chmod +x /usr/local/bin/gh \
    && rm -rf "/tmp/${GH_ARCHIVE}" "/tmp/gh_${GH_VERSION}_linux_amd64"
  if command -v gh &>/dev/null; then
    echo "GitHub CLI installed: $(gh --version | head -1)"
  else
    echo "WARNING: GitHub CLI installation failed"
  fi
else
  echo "GitHub CLI already installed: $(gh --version | head -1)"
fi

# =============================================================================
# RAILWAY CLI (requires gh to be installed first — direct URLs are blocked)
# =============================================================================
echo ""
echo "--- Railway CLI Setup ---"
if ! command -v railway &>/dev/null; then
  if command -v gh &>/dev/null; then
    echo "Installing Railway CLI via gh..."
    RAILWAY_VERSION=$(gh release view --repo railwayapp/cli --json tagName -q .tagName 2>/dev/null)
    if [ -n "$RAILWAY_VERSION" ]; then
      gh release download "$RAILWAY_VERSION" --repo railwayapp/cli \
        --pattern "railway-${RAILWAY_VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
        --dir /tmp 2>/dev/null \
        && tar -xzf "/tmp/railway-${RAILWAY_VERSION}-x86_64-unknown-linux-gnu.tar.gz" -C /tmp \
        && cp /tmp/railway /usr/local/bin/railway \
        && chmod +x /usr/local/bin/railway \
        && rm -f "/tmp/railway-${RAILWAY_VERSION}-x86_64-unknown-linux-gnu.tar.gz" /tmp/railway
      if command -v railway &>/dev/null; then
        echo "Railway CLI installed: $(railway --version)"
      else
        echo "WARNING: Railway CLI installation failed"
      fi
    else
      echo "WARNING: Could not determine Railway CLI latest version"
    fi
  else
    echo "WARNING: gh CLI not available, skipping Railway CLI install"
  fi
else
  echo "Railway CLI already installed: $(railway --version)"
fi

# =============================================================================
# PYTHON ENVIRONMENT
# =============================================================================
echo ""
echo "--- Python Environment Setup ---"

echo "Creating Python venv and installing dependencies..."
uv venv
source .venv/bin/activate
uv sync -p python3.11
echo "PYTHONPATH is handled by editable install via uv sync"

# =============================================================================
# DATABASE
# =============================================================================
echo ""
echo "--- Database Setup ---"

# Fix SSL certificate permissions (required for PostgreSQL to start)
# The cloud image may have the key at different paths — fix both
echo "Fixing SSL certificate permissions..."
for key_path in /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl-cert-snakeoil.key; do
  if [ -f "$key_path" ]; then
    chmod 600 "$key_path" 2>/dev/null || true
    echo "  Fixed: $key_path"
  fi
done

# Also fix the PostgreSQL SSL config to point to the correct key location
PG_CONF="/etc/postgresql/16/main/postgresql.conf"
if [ -f "$PG_CONF" ]; then
  # If the key is at the non-standard path, update PostgreSQL config to match
  if [ -f /etc/ssl-cert-snakeoil.key ] && ! [ -f /etc/ssl/private/ssl-cert-snakeoil.key ]; then
    sed -i "s|ssl_key_file = '/etc/ssl/private/ssl-cert-snakeoil.key'|ssl_key_file = '/etc/ssl-cert-snakeoil.key'|" "$PG_CONF" 2>/dev/null || true
  fi
  # Alternatively, just disable SSL for local-only PostgreSQL (simplest fix)
  sed -i 's/^ssl = on/ssl = off/' "$PG_CONF" 2>/dev/null || true
fi

# Configure pg_hba.conf for trust authentication (needed because sudo is broken
# in this cloud environment and PostgreSQL runs as the 'claude' user)
echo "Configuring PostgreSQL authentication..."
PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
if [ -f "$PG_HBA" ]; then
  sed -i 's/local   all             postgres                                peer/local   all             postgres                                trust/' "$PG_HBA" 2>/dev/null || true
  sed -i 's/local   all             all                                     peer/local   all             all                                     trust/' "$PG_HBA" 2>/dev/null || true
  sed -i 's/host    all             all             127.0.0.1\/32            scram-sha-256/host    all             all             127.0.0.1\/32            trust/' "$PG_HBA" 2>/dev/null || true
  # Make readable by PostgreSQL process regardless of which user we're running as
  chmod 644 "$PG_HBA" 2>/dev/null || true
fi

echo "Starting PostgreSQL..."
service postgresql start 2>/dev/null || true
sleep 2
if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  echo "PostgreSQL is ready"
else
  echo "WARNING: PostgreSQL may have failed to start"
fi

echo "Creating portfolio user and database..."
psql -U postgres -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>/dev/null || true
psql -U postgres -c "CREATE DATABASE portfolio OWNER portfolio;" 2>/dev/null || true

# Persist environment variables for the session via CLAUDE_ENV_FILE
# These are available to ALL subsequent Bash tool calls — no .env file needed
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo 'export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"' >> "$CLAUDE_ENV_FILE"
  echo 'export DATABASE_REQUIRE_SSL="false"' >> "$CLAUDE_ENV_FILE"
  echo "Environment variables persisted to session via CLAUDE_ENV_FILE"
fi

# Set for current script execution (needed for alembic/seed below)
export DATABASE_URL="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_URL_UNPOOLED="postgresql://portfolio:portfolio@localhost:5432/portfolio"
export DATABASE_REQUIRE_SSL="false"

echo "Running database migrations..."
alembic upgrade head 2>/dev/null || echo "WARNING: Migrations may have issues, but continuing..."

echo "Seeding database..."
uv run python api/seed_db.py 2>/dev/null
echo "Database ready"

# =============================================================================
# WAIT FOR PNPM INSTALL (started earlier in background)
# =============================================================================
echo ""
echo "--- Waiting for pnpm install to finish ---"
wait $PNPM_PID
PNPM_EXIT=$?
if [ $PNPM_EXIT -eq 0 ]; then
  echo "pnpm dependencies installed"
else
  echo "WARNING: pnpm install failed (exit $PNPM_EXIT). Check /tmp/pnpm_install.log"
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "=== Remote Setup Complete ==="
echo "Finished: $(date -Iseconds)"
echo ""
echo "IMPORTANT: No .env file is needed in this remote environment."
echo "All required environment variables (DATABASE_URL, DATABASE_REQUIRE_SSL, etc.)"
echo "have been persisted to the session via CLAUDE_ENV_FILE and are available to"
echo "all subsequent Bash commands automatically."
echo ""
echo "Available commands:"
echo "  pnpm dev              - Start React Router dev server (port 5173)"
echo "  pnpm fastapi-dev      - Start FastAPI dev server (port 8000)"
echo "  pnpm dev-with-fastapi - Start both servers"
echo ""
exit 0
