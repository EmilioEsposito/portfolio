#!/usr/bin/env bash
# Select installed local tools for this command and its children only.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# GUI worktrees can start with only the macOS system paths, even when the
# required tools are installed. Keep explicit shell tool choices ahead of fallbacks.
if [[ "$(uname -s)" == Darwin ]]; then
  for prefix in "${HOMEBREW_PREFIX:-/opt/homebrew}" /usr/local; do
    [[ -x "$prefix/bin/brew" ]] || continue
    export PATH="$PATH:$prefix/bin:$prefix/opt/libpq/bin"
    # uv-managed Python can otherwise inherit Apple's CA store even when
    # Homebrew Python passes the bootstrap check with its own OpenSSL defaults.
    if [[ -r "$prefix/etc/ca-certificates/cert.pem" ]]; then
      export SSL_CERT_FILE="${SSL_CERT_FILE-$prefix/etc/ca-certificates/cert.pem}"
    fi
    if [[ "${1:-}" == python3 && -x "$prefix/bin/python3" ]]; then
      case "$(command -v python3)" in
        /usr/bin/python3|*/Contents/Developer/usr/bin/python3|/Library/Developer/CommandLineTools/usr/bin/python3)
          shift
          set -- "$prefix/bin/python3" "$@"
          ;;
      esac
    fi
    break
  done
  export PATH="$PATH:$HOME/.local/bin:$HOME/Library/pnpm:/usr/local/bin"
fi

node_is_compatible() {
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 22) ? 0 : 1)' >/dev/null 2>&1 &&
    node "$SCRIPT_DIR/check-node-trust.cjs" >/dev/null 2>&1
}

if ! node_is_compatible; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # nvm is a shell function, so noninteractive setup does not inherit it.
    source "$NVM_DIR/nvm.sh" --no-use
    nvm use --silent "$(cat "$SCRIPT_DIR/../.nvmrc")" >/dev/null 2>&1 || true
  fi
  if ! node_is_compatible; then
    echo 'Worktree setup: Node 22.23.4+ with patched CA roots is required. Install the .nvmrc version with nvm install, then retry.' >&2
    exit 1
  fi
fi

exec "$@"
