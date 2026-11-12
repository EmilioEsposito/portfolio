#!/bin/bash
# SessionStart hook: provision the worktree this session works in.
#
# The checkout is the hook input's `cwd`, not the hook's own working directory: in a
# desktop-app worktree session hooks run in the main checkout and CLAUDE_PROJECT_DIR
# points there, so a provisioner started from either one skips the session's worktree.
# The worktree's own provisioner runs, so a stale main checkout cannot supply old rules.
# See docs/WORKTREES.md.

INPUT=$(cat)

TARGET=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -d "$TARGET" ] || TARGET="$PWD"

ROOT=$(git -C "$TARGET" rev-parse --show-toplevel 2>/dev/null) || exit 0
PROVISIONER="$ROOT/scripts/worktree-provision.sh"
[ -x "$PROVISIONER" ] || exit 0

exec "$PROVISIONER" "$ROOT" --once
