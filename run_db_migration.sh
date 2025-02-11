#!/bin/bash
echo "DB_MIGRATION: STARTING..."

# Install dependencies
echo "DB_MIGRATION: INSTALLING DEPENDENCIES..."
pip3 install -r requirements.txt 1> /dev/null # suppress non-error output

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run migrations
cd api_src/database
python3 -m alembic upgrade head 1> /dev/null # suppress non-error output

echo "DB_MIGRATION: COMPLETED"