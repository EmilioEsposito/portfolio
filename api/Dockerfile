# Base image for the fastapi
FROM python:3.11-slim

RUN echo ">>> Building BACKEND..."

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

ENV PYTHONPATH=/app

# Install dependencies into the virtual environment using uv sync
# VIRTUAL_ENV is set to .venv, uv sync should install here
# Exclude dev dependencies for production builds
RUN uv sync --no-dev

# Expose the port (the PORT on Railway app is hardcoded to 8000)
EXPOSE 8000

# Command to run the Hypercorn server
# It will find api.index:app starting from the PYTHONPATH (/app)
# Uses the PORT environment variable if set by the platform (e.g., Railway), otherwise defaults to 8000.
# Binds to IPv6 ([::]) as required for Railway Private Networking.
CMD ["sh", "-c", "hypercorn api.index:app --bind [::]:${PORT:-8000} --keep-alive 120"] 