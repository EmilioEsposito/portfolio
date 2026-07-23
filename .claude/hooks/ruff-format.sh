#!/bin/bash
# PostToolUse hook: auto-format Python files Claude just edited so ruff never
# fails in CI for a formatting-only reason.
#
# CI runs `ruff check .` + `ruff format --check .`. Those only *check*; nothing
# *fixed* the code locally before this hook existed, so an agent-authored edit
# (or a raw heredoc append) could reach CI unformatted. This runs
# `ruff format` + `ruff check --fix` on the single edited file right after the
# Edit/Write/MultiEdit tool call.
#
# Fail-soft by design: it must NEVER block an edit. Any problem (no ruff, parse
# error, non-.py file) exits 0 silently. Ruff auto-discovers the nearest
# pyproject/ruff.toml for the file, so it also handles apps/sernia_mcp/ files
# with that subproject's config.

INPUT=$(cat)

FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only touch existing Python files.
case "$FILE" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$FILE" ] || exit 0

# Prefer the repo venv's ruff (matches the CI-installed version), then fall back
# to whatever ruff is on PATH.
RUFF=""
if [ -x "$CLAUDE_PROJECT_DIR/.venv/bin/ruff" ]; then
  RUFF="$CLAUDE_PROJECT_DIR/.venv/bin/ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="$(command -v ruff)"
else
  exit 0
fi

# Import sorting / simple lint autofixes first, then formatting. Suppress output
# and never propagate a failure.
"$RUFF" check --fix --quiet "$FILE" >/dev/null 2>&1 || true
"$RUFF" format --quiet "$FILE" >/dev/null 2>&1 || true

exit 0
