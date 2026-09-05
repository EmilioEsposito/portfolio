#!/usr/bin/env bash
# Run both servers and stop their process groups together.
set -euo pipefail
exec python3 "$(dirname "$0")/dev.py"
