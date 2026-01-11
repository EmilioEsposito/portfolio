#!/bin/bash
#
# worktree-create.sh - Create an isolated git worktree for parallel development
#
# Usage: ./scripts/worktree-create.sh <description>
# Example: ./scripts/worktree-create.sh feature-auth
#
# Creates:
#   - Git worktree at ../portfolio-<description>/
#   - Isolated database: portfolio_<description>
#   - Unique ports (hash-based) for FastAPI and frontend
#   - Adds folder to portfolio.code-workspace
#
# Requirements:
#   - Must be run from the main portfolio directory
#   - Docker must be available (postgres will be started automatically)
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Validate we're in the main portfolio directory (not a worktree)
validate_main_dir() {
    if [[ ! -f "$MAIN_DIR/package.json" ]] || [[ ! -d "$MAIN_DIR/api" ]]; then
        log_error "Must be run from the main portfolio directory"
        exit 1
    fi

    # Check we're not in a worktree by comparing git-dir and git-common-dir
    # In main repo: both point to same location
    # In worktree: git-dir points to .git/worktrees/<name>, common-dir to main .git
    cd "$MAIN_DIR"
    local git_dir=$(git rev-parse --git-dir 2>/dev/null)
    local common_dir=$(git rev-parse --git-common-dir 2>/dev/null)

    if [[ "$git_dir" != "$common_dir" ]]; then
        log_error "This appears to be a worktree, not the main repository"
        log_error "Run this script from the main portfolio directory"
        exit 1
    fi
}

# Validate description argument
validate_description() {
    local desc="$1"

    if [[ -z "$desc" ]]; then
        log_error "Usage: $0 <description>"
        log_error "Example: $0 feature-auth"
        exit 1
    fi

    # Check for valid characters (alphanumeric and hyphens only)
    if [[ ! "$desc" =~ ^[a-zA-Z0-9-]+$ ]]; then
        log_error "Description must contain only alphanumeric characters and hyphens"
        exit 1
    fi

    # Check it doesn't start or end with hyphen
    if [[ "$desc" =~ ^- ]] || [[ "$desc" =~ -$ ]]; then
        log_error "Description cannot start or end with a hyphen"
        exit 1
    fi
}

# Compute deterministic port offset from description
compute_port_offset() {
    local desc="$1"
    # Use cksum for cross-platform hash, take modulo 100, multiply by 10
    # This gives offsets: 0, 10, 20, ..., 990
    local hash=$(echo -n "$desc" | cksum | cut -d' ' -f1)
    echo $(( (hash % 100) * 10 ))
}

# Convert description to valid postgres database name
desc_to_db_name() {
    local desc="$1"
    # Replace hyphens with underscores, lowercase
    echo "portfolio_${desc//-/_}" | tr '[:upper:]' '[:lower:]'
}

# Ensure postgres is running (idempotent)
ensure_postgres() {
    log_info "Ensuring postgres is running..."

    # Start postgres via docker-compose (idempotent - does nothing if already running)
    cd "$MAIN_DIR"
    docker-compose up -d postgres

    # Wait for postgres to be ready (up to 30 seconds)
    local max_attempts=30
    local attempt=1
    while ! pg_isready -h localhost -p 5432 -U portfolio -q 2>/dev/null; do
        if [[ $attempt -ge $max_attempts ]]; then
            log_error "PostgreSQL failed to start after ${max_attempts} seconds"
            exit 1
        fi
        sleep 1
        ((attempt++))
    done

    log_success "PostgreSQL is running"
}

# Create the database (copy from main or run migrations)
setup_database() {
    local db_name="$1"
    local worktree_dir="$2"

    log_info "Setting up database: $db_name"

    export PGPASSWORD="portfolio"

    # Check if database already exists
    if psql -h localhost -U portfolio -lqt | cut -d \| -f 1 | grep -qw "$db_name"; then
        log_warn "Database $db_name already exists. Skipping creation."
        return 0
    fi

    # Create database
    log_info "Creating database $db_name..."
    createdb -h localhost -U portfolio "$db_name"

    # Try to copy from main database
    log_info "Attempting to copy data from main database..."
    if pg_dump -h localhost -U portfolio portfolio 2>/dev/null | psql -h localhost -U portfolio "$db_name" -q 2>/dev/null; then
        log_success "Database copied from main"
    else
        log_warn "Could not copy database, running migrations and seed instead..."

        # Run migrations
        cd "$worktree_dir"
        source .venv/bin/activate
        cd api
        uv run alembic upgrade head

        # Run seed
        cd "$worktree_dir"
        source .venv/bin/activate
        python api/seed_db.py

        log_success "Database initialized with migrations and seed"
    fi
}

# Add worktree folder to the workspace using Cursor CLI
add_to_workspace() {
    local worktree_dir="$1"

    log_info "Adding to workspace..."

    if command -v cursor &> /dev/null; then
        cursor --add "$worktree_dir" 2>/dev/null || {
            log_warn "Could not add to workspace via Cursor CLI"
            return 0
        }
        log_success "Added to workspace"
    else
        log_warn "Cursor CLI not found. Add folder manually: $worktree_dir"
    fi
}

# Main function
main() {
    local desc="$1"

    echo ""
    echo "=========================================="
    echo "  Git Worktree Setup"
    echo "=========================================="
    echo ""

    # Validations
    validate_main_dir
    validate_description "$desc"

    # Compute values
    local worktree_name="portfolio-$desc"
    local worktree_dir="$MAIN_DIR/../$worktree_name"
    local branch_name="$desc"
    local port_offset=$(compute_port_offset "$desc")
    local fastapi_port=$((8000 + port_offset))
    local frontend_port=$((5173 + port_offset))
    local db_name=$(desc_to_db_name "$desc")

    log_info "Configuration:"
    echo "  Description:    $desc"
    echo "  Worktree:       $worktree_dir"
    echo "  Branch:         $branch_name"
    echo "  FastAPI port:   $fastapi_port"
    echo "  Frontend port:  $frontend_port"
    echo "  Database:       $db_name"
    echo ""

    # Check if worktree already exists
    if [[ -d "$worktree_dir" ]]; then
        log_error "Worktree directory already exists: $worktree_dir"
        exit 1
    fi

    # Ensure postgres is running (idempotent)
    ensure_postgres

    # Create git worktree
    log_info "Creating git worktree..."
    git worktree add "$worktree_dir" -b "$branch_name" 2>/dev/null || \
    git worktree add "$worktree_dir" "$branch_name"
    log_success "Git worktree created"

    # Copy and modify .env for FastAPI
    log_info "Configuring environment files..."
    if [[ -f "$MAIN_DIR/.env" ]]; then
        cp "$MAIN_DIR/.env" "$worktree_dir/.env"

        # Append/override worktree-specific settings
        cat >> "$worktree_dir/.env" << EOF

# =============================================================================
# Worktree-specific overrides (auto-generated by create-worktree.sh)
# =============================================================================
PORT=$fastapi_port
DATABASE_URL=postgresql://portfolio:portfolio@localhost:5432/$db_name
DATABASE_URL_UNPOOLED=postgresql://portfolio:portfolio@localhost:5432/$db_name
DATABASE_REQUIRE_SSL=false
EOF
        log_success "FastAPI .env configured"
    else
        log_warn "No .env found in main directory, creating minimal config"
        cat > "$worktree_dir/.env" << EOF
# Worktree environment (auto-generated by create-worktree.sh)
PORT=$fastapi_port
DATABASE_URL=postgresql://portfolio:portfolio@localhost:5432/$db_name
DATABASE_URL_UNPOOLED=postgresql://portfolio:portfolio@localhost:5432/$db_name
DATABASE_REQUIRE_SSL=false
EOF
    fi

    # Create frontend .env
    mkdir -p "$worktree_dir/apps/web-react-router"
    cat > "$worktree_dir/apps/web-react-router/.env" << EOF
# Frontend environment (auto-generated by create-worktree.sh)
# BACKEND_PORT tells vite.config.ts where to proxy API requests
BACKEND_PORT=$fastapi_port
EOF
    log_success "Frontend .env configured"

    # Setup Python environment
    log_info "Setting up Python environment..."
    cd "$worktree_dir"
    uv venv
    source .venv/bin/activate
    uv sync -p python3.11
    log_success "Python environment ready"

    # Setup Node environment
    log_info "Installing Node dependencies..."
    cd "$worktree_dir"
    pnpm install
    log_success "Node dependencies installed"

    # Setup database
    setup_database "$db_name" "$worktree_dir"

    # Add to workspace
    add_to_workspace "$worktree_dir"

    # Print summary
    echo ""
    echo "=========================================="
    echo "  Worktree Ready!"
    echo "=========================================="
    echo ""
    echo "Location: $worktree_dir"
    echo "Branch:   $branch_name"
    echo ""
    echo "Ports:"
    echo "  FastAPI:  http://localhost:$fastapi_port"
    echo "  Frontend: http://localhost:$frontend_port"
    echo ""
    echo "Database: $db_name"
    echo ""
    echo "To start development:"
    echo "  cd $worktree_dir"
    echo "  pnpm dev-with-fastapi"
    echo ""
    echo "The worktree has been added to your Cursor workspace."
    echo ""
}

main "$1"
