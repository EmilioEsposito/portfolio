#!/bin/bash

# Install dependencies
pip3 install -r requirements.txt

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Run migrations
cd api_src/database
python3 -m alembic upgrade head