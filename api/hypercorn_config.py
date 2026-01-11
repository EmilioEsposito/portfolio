"""
Hypercorn configuration file.

Reads PORT from environment variable for worktree support.
Used by both local development (pnpm fastapi-dev, VS Code debug) and production (api/start.sh).

See also: api/start.sh, .vscode/launch.json, package.json
"""
import os

# Read port from environment, default to 8000
port = os.getenv("PORT", "8000")

# Bind to all interfaces on the configured port
# IPv4 for local dev, IPv6 ([::]) is used in start.sh for Railway
bind = [f"0.0.0.0:{port}"]

# Keep-alive timeout (seconds)
keep_alive = 120
