# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Build stage — install dependencies into a self-contained venv with uv.
#
# Copies pinned official uv onto the same patched Python base as the runtime. The
# venv ends up at /app/.venv and is copied into the final stage as-is.
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS trusted-python

COPY trust/ /opt/python-trust/
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && sh /opt/python-trust/distrust-emsign.sh \
    && sh /opt/python-trust/bootstrap-pip.sh /usr/local/bin/python \
    && python /opt/python-trust/check_python_trust.py \
    && rm -rf /var/lib/apt/lists/*

FROM trusted-python AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/
RUN python /opt/python-trust/check_python_trust.py --require-uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cache-friendly via Docker layer caching:
# this RUN re-executes only when pyproject.toml or uv.lock changes, since
# they're the only inputs to the layer above). --no-install-project skips
# installing our own package; we add source after.
#
# We deliberately do NOT use --mount=type=cache here. Railway's BuildKit
# requires its own cacheKey prefix on the `id` (e.g. id=s/<service-id>-...),
# which would hardcode a Railway-specific service ID into the Dockerfile
# and break local + cross-platform builds. Layer caching alone is enough
# for our dep set.
#
# README.md is copied because pyproject.toml's `readme = "README.md"` is
# validated by hatchling at build time, even with --no-install-project.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

# Then the app source — small change → small layer.
COPY src/ ./src/
RUN uv sync --locked --no-dev

# -----------------------------------------------------------------------------
# Runtime stage — minimal Python image with the prebuilt venv on PATH.
#
# We don't carry uv into the runtime; we just point at the venv. Smaller image,
# faster cold starts.
# -----------------------------------------------------------------------------
FROM trusted-python

# git is needed at runtime: ensure_repo / pull_workspace / commit_and_push
# shell out to `git` to keep the workspace in sync with the sernia-knowledge
# repo. Without this the workspace becomes ephemeral on each redeploy.
# git and the refreshed trust store are inherited from trusted-python.

WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN python /opt/python-trust/check_python_trust.py --require-certifi \
    && /usr/local/bin/python /opt/python-trust/check_python_trust.py

EXPOSE 8080

# Shell form so $PORT (set by Railway) is expanded at runtime. Default 8080
# matches the local-dev launchers.
CMD uvicorn sernia_mcp.app:app --host 0.0.0.0 --port "${PORT:-8080}"
