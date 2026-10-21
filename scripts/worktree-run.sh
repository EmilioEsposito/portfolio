#!/usr/bin/env bash
# Select the repository Node runtime for this command and its children only.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

node_is_compatible() {
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 22) ? 0 : 1)' >/dev/null 2>&1
}

if ! node_is_compatible; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # nvm is a shell function, so noninteractive setup does not inherit it.
    source "$NVM_DIR/nvm.sh" --no-use
    nvm use --silent "$(cat "$SCRIPT_DIR/../.nvmrc")" >/dev/null 2>&1 || true
  fi
  if ! node_is_compatible; then
    echo 'Worktree setup: Node 22.22+ is required. Install the .nvmrc version with nvm install, then retry.' >&2
    exit 1
  fi
fi

exec "$@"
