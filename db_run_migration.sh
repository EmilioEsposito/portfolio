#!/bin/bash
echo "DB_MIGRATION: STARTING..."

# Install dependencies
echo "DB_MIGRATION: INSTALLING DEPENDENCIES..."
pip3 install -r requirements.txt --quiet --disable-pip-version-check --root-user-action=ignore

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run migrations
echo "DB_MIGRATION: RUNNING MIGRATIONS..."
python3 -m alembic upgrade head
echo "DB_MIGRATION: COMPLETED"