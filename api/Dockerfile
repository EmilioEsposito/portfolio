# Base image for the fastapi
FROM python:3.11-slim

RUN echo ">>> Building BACKEND..."

# git is needed by sernia_ai workspace sync (git_sync.py)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# https://docs.astral.sh/uv/guides/integration/docker/
# Install uv by copying the binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Define path for the virtual environment relative to WORKDIR & create it
# uv sync seems to prefer/default to `.venv` in the project root
ENV VENV_PATH=.venv 
RUN uv venv $VENV_PATH

# Set VIRTUAL_ENV so uv targets it automatically
ENV VIRTUAL_ENV=$VENV_PATH 
# Add the venv's bin directory to the PATH 
ENV PATH="$VENV_PATH/bin:$PATH"

# Copy project files
COPY pyproject.toml uv.lock* ./
COPY api/ ./api/
COPY alembic.ini ./

ENV PYTHONPATH=/app

# Install dependencies into the virtual environment using uv sync
# VIRTUAL_ENV is set to .venv, uv sync should install here
# Exclude dev dependencies for production builds
RUN uv sync --no-dev

# Copy and make startup script executable
COPY api/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Expose the port (the PORT on Railway app is hardcoded to 8000)
EXPOSE 8000

# Use startup script that runs migrations then starts the server
# This ensures migrations run as part of the Docker process and logs are visible
CMD ["/app/start.sh"] 