#!/usr/bin/env bash
# See docs/WORKTREES.md. All lifecycle rules live in worktree.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/worktree.py" create "$@"
