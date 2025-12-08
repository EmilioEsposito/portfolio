# Base image for Prefect worker
FROM python:3.11-slim

RUN echo ">>> Building PREFECT WORKER..."

# https://docs.astral.sh/uv/guides/integration/docker/
# Install uv by copying the binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Define path for the virtual environment with absolute path
ENV VENV_PATH=/app/.venv
RUN uv venv $VENV_PATH

# Set VIRTUAL_ENV so uv targets it automatically
ENV VIRTUAL_ENV=$VENV_PATH
# Add the venv's bin directory to the PATH
ENV PATH="$VENV_PATH/bin:$PATH"

# Copy project files
COPY pyproject.toml uv.lock* ./
COPY apps/prefect/ ./apps/prefect/
# Also copy apps/api since prefect flows import from it
COPY apps/api/ ./apps/api/
COPY alembic.ini ./

ENV PYTHONPATH=/app

# Install dependencies for prefect-app (which includes api-app dependencies via shared venv)
# Using --all-packages since we have both apps in the container
RUN uv sync --all-packages --no-dev

# Expose port for Prefect UI (if running local server)
EXPOSE 4200

# Start Prefect worker that connects to Prefect Cloud
# Requires env vars: PREFECT_API_URL, PREFECT_API_KEY
# The worker polls for scheduled work and executes flows
CMD ["prefect", "worker", "start", "--pool", "railway-pool"]
