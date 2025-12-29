#!/bin/sh
echo "DB_MIGRATION: STARTING..."

# Install dependencies using uv (if needed in this context - often dependencies are already in the image)
# echo "DB_MIGRATION: INSTALLING DEPENDENCIES..."
# uv sync --quiet # Assuming uv is available and pyproject.toml/uv.lock are present

# Set PYTHONPATH (adjust if needed)
# export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run migrations using the virtual environment's Python
echo "DB_MIGRATION: RUNNING MIGRATIONS..."
alembic upgrade head
echo "DB_MIGRATION: COMPLETED"