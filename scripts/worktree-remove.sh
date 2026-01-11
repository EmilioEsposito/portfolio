#!/bin/bash
#
# worktree-remove.sh - Remove a git worktree and clean up resources
#
# Usage: ./scripts/worktree-remove.sh <description>
# Example: ./scripts/worktree-remove.sh feature-auth
#          ./scripts/worktree-remove.sh portfolio-feature-auth  # also works
#
# Removes:
#   - The database: portfolio_<description>
#   - The worktree folder: ../portfolio-<description>/
#   - The git worktree reference
#   - The entry from portfolio.code-workspace
#   - Optionally deletes the git branch
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

# Normalize input: accepts either "portfolio-<desc>" or just "<desc>"
normalize_description() {
    local input="$1"

    # Strip "portfolio-" prefix if present
    if [[ "$input" =~ ^portfolio-(.+)$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "$input"
    fi
}

# Validate description argument
validate_description() {
    local desc="$1"

    if [[ -z "$desc" ]]; then
        log_error "Usage: $0 <description>"
        log_error "Example: $0 feature-auth"
        log_error "         $0 portfolio-feature-auth  # also works"
        exit 1
    fi

    # Check for valid characters (alphanumeric and hyphens only)
    if [[ ! "$desc" =~ ^[a-zA-Z0-9-]+$ ]]; then
        log_error "Description must contain only alphanumeric characters and hyphens"
        exit 1
    fi
}

# Convert description to valid postgres database name
desc_to_db_name() {
    local desc="$1"
    # Replace hyphens with underscores, lowercase
    echo "portfolio_${desc//-/_}" | tr '[:upper:]' '[:lower:]'
}

# Drop the database if it exists
drop_database() {
    local db_name="$1"

    log_info "Checking for database: $db_name"

    export PGPASSWORD="portfolio"

    # Check if database exists
    if psql -h localhost -U portfolio -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$db_name"; then
        log_info "Dropping database $db_name..."

        # Terminate existing connections
        psql -h localhost -U portfolio -d postgres -c "
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '$db_name' AND pid <> pg_backend_pid();
        " -q 2>/dev/null || true

        # Drop database
        dropdb -h localhost -U portfolio "$db_name" 2>/dev/null || {
            log_warn "Could not drop database (postgres may not be running)"
        }
        log_success "Database dropped"
    else
        log_info "Database $db_name does not exist (or postgres is not running)"
    fi
}

# Remove worktree folder from the workspace using Cursor CLI
remove_from_workspace() {
    local worktree_dir="$1"

    log_info "Removing from workspace..."

    if command -v cursor &> /dev/null; then
        cursor --remove "$worktree_dir" 2>/dev/null || {
            log_warn "Could not remove from workspace via Cursor CLI (may already be removed)"
            return 0
        }
        log_success "Removed from workspace"
    else
        log_warn "Cursor CLI not found. Remove folder manually from workspace."
    fi
}

# Remove git worktree
remove_worktree() {
    local worktree_dir="$1"
    local branch_name="$2"

    log_info "Removing git worktree..."

    if [[ -d "$worktree_dir" ]]; then
        # Force remove worktree
        git worktree remove "$worktree_dir" --force 2>/dev/null || {
            log_warn "git worktree remove failed, trying manual cleanup..."
            rm -rf "$worktree_dir"
            git worktree prune
        }
        log_success "Worktree removed"
    else
        log_info "Worktree directory does not exist: $worktree_dir"
        # Still try to prune stale worktrees
        git worktree prune
    fi

    # Offer to delete the branch
    if git rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        echo ""
        read -p "Delete branch '$branch_name'? [y/N] " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if git branch -d "$branch_name" 2>/dev/null; then
                log_success "Branch deleted"
            else
                log_warn "Branch has unmerged changes. Use 'git branch -D $branch_name' to force delete."
            fi
        else
            log_info "Branch '$branch_name' kept"
        fi
    fi
}

# Main function
main() {
    local input="$1"

    echo ""
    echo "=========================================="
    echo "  Git Worktree Removal"
    echo "=========================================="
    echo ""

    # Validations
    validate_main_dir

    # Normalize input (accepts "portfolio-<desc>" or just "<desc>")
    local desc=$(normalize_description "$input")
    validate_description "$desc"

    # Compute values
    local worktree_name="portfolio-$desc"
    local worktree_dir="$MAIN_DIR/../$worktree_name"
    local branch_name="$desc"
    local db_name=$(desc_to_db_name "$desc")

    log_info "Removing worktree:"
    echo "  Description: $desc"
    echo "  Directory:   $worktree_dir"
    echo "  Branch:      $branch_name"
    echo "  Database:    $db_name"
    echo ""

    # Confirm
    read -p "Are you sure you want to remove this worktree? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Aborted"
        exit 0
    fi

    # Drop database
    drop_database "$db_name"

    # Remove from workspace
    remove_from_workspace "$worktree_dir"

    # Remove git worktree (and optionally branch)
    remove_worktree "$worktree_dir" "$branch_name"

    # Final cleanup
    echo ""
    echo "=========================================="
    echo "  Worktree Removed"
    echo "=========================================="
    echo ""
    echo "Cleaned up:"
    echo "  - Database: $db_name"
    echo "  - Directory: $worktree_dir"
    echo "  - Workspace entry"
    echo ""
}

main "$1"
