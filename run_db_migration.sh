#!/bin/bash
echo "DB_MIGRATION: STARTING..."

# Install dependencies
echo "DB_MIGRATION: INSTALLING DEPENDENCIES..."
pip3 install -r requirements.txt --quiet --disable-pip-version-check --root-user-action=ignore

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Suppress SQLAlchemy warnings
export SQLALCHEMY_WARN_20=1
export PYTHONWARNINGS="ignore:.*:sqlalchemy.exc.SAWarning"

# Run migrations
cd api_src/database
python3 -m alembic --quiet upgrade head

echo "DB_MIGRATION: COMPLETED"