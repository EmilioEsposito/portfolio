# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Build stage — install dependencies into a self-contained venv with uv.
#
# Uses Astral's official uv-on-python image so uv is preinstalled at a known
# version (no Nixpacks-style "discover uv at build time" failure mode). The
# venv ends up at /app/.venv and is copied into the final stage as-is.
# -----------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cache-friendly: re-runs only when lockfile changes).
# --no-install-project skips installing our own package; we add source after.
# README.md is copied because pyproject.toml's `readme = "README.md"` is
# validated by hatchling at build time, even with --no-install-project.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,id=sernia-mcp-uv,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Then the app source — small change → small layer.
COPY src/ ./src/
RUN --mount=type=cache,id=sernia-mcp-uv,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Runtime stage — minimal Python image with the prebuilt venv on PATH.
#
# We don't carry uv into the runtime; we just point at the venv. Smaller image,
# faster cold starts.
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

# git is needed at runtime: ensure_repo / pull_workspace / commit_and_push
# shell out to `git` to keep the workspace in sync with the sernia-knowledge
# repo. Without this the workspace becomes ephemeral on each redeploy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Shell form so $PORT (set by Railway) is expanded at runtime. Default 8080
# matches the local-dev launchers.
CMD uvicorn sernia_mcp.app:app --host 0.0.0.0 --port "${PORT:-8080}"
