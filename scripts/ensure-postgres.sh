#!/bin/bash
#
# ensure-postgres.sh - Ensure PostgreSQL is running via docker-compose
#
# Usage: ./scripts/ensure-postgres.sh
#
# Idempotent: safe to run multiple times. Starts postgres if not running,
# does nothing if already running. Waits for postgres to be ready.
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Find the repository root (where docker-compose.yml lives)
find_repo_root() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/docker-compose.yml" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    log_error "Could not find docker-compose.yml in any parent directory"
    exit 1
}

main() {
    local repo_root
    repo_root=$(find_repo_root)

    log_info "Ensuring PostgreSQL is running..."

    # Start postgres via docker-compose (idempotent - does nothing if already running)
    cd "$repo_root"
    docker-compose up -d postgres 2>&1 | grep -v "orphan containers" || true

    # Wait for postgres to be ready (up to 30 seconds)
    local max_attempts=30
    local attempt=1
    while ! pg_isready -h localhost -p 5432 -U portfolio -q 2>/dev/null; do
        if [[ $attempt -ge $max_attempts ]]; then
            log_error "PostgreSQL failed to start after ${max_attempts} seconds"
            exit 1
        fi
        if [[ $attempt -eq 1 ]]; then
            log_info "Waiting for PostgreSQL to be ready..."
        fi
        sleep 1
        ((attempt++))
    done

    log_success "PostgreSQL is running"
}

main
