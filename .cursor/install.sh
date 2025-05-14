#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status.

echo ">>> Running agent install script (.cursor/install.sh)..."
echo ">>> Working directory: $(pwd)"
echo ">>> User: $(whoami)"

echo ">>> Verifying tool versions..."
echo "Node version: $(node --version)"
echo "pnpm version: $(pnpm --version)"
echo "Python version: $(python3 --version)"
echo "uv version: $(uv --version)"


echo ">>> Installing Node.js dependencies with pnpm..."
if [ -f "pnpm-lock.yaml" ]; then
    pnpm install --frozen-lockfile
else
    echo "Warning: pnpm-lock.yaml not found. Running 'pnpm install'. It is recommended to commit a lockfile."
    pnpm install
fi

echo ">>> Creating Python virtual environment with uv..."
# The Dockerfile sets python3 to python3.11.
# VIRTUAL_ENV=/app/.venv is set in Dockerfile, uv should pick this up or create .venv in current dir.
# Explicitly create .venv in /app which is the WORKDIR
uv venv /app/.venv --python python3.11

echo ">>> Installing Python dependencies into /app/.venv with uv..."
if [ -f "uv.lock" ]; then
    # Use --strict if uv.lock is present and VIRTUAL_ENV is active
    uv sync --strict
else
    echo "Warning: uv.lock not found. Running 'uv sync'. It is recommended to commit a lockfile."
    uv sync
fi

echo ">>> Installing Python dev dependencies into /app/.venv with uv..."
if [ -f "pyproject.toml" ]; then # Check if pyproject.toml exists for dev dependencies
    if [ -f "uv.lock" ]; then
        uv sync --dev --strict
    else
        uv sync --dev
    fi
else
    echo "Skipping dev dependencies: pyproject.toml not found."
fi

echo ">>> Agent install script finished." 