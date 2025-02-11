#!/bin/bash
echo "DB_MIGRATION: STARTING..."

# Install dependencies
echo "DB_MIGRATION: INSTALLING DEPENDENCIES..."
pip3 install -r requirements.txt --quiet --disable-pip-version-check

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run migrations
cd api_src/database
python3 -m alembic --quiet upgrade head

echo "DB_MIGRATION: COMPLETED"