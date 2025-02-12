#!/bin/bash
cd api_src/database

# ask user for migration name
read -p "Enter migration name: " migration_name

alembic revision --autogenerate -m "$migration_name"

# open migration file in vscode - updated path to migrations/versions
code migrations/versions/$(ls migrations/versions/ | grep -E "^\d{4}_.*\.py$" | tail -1)

# ask user if they want to commit the migration
read -p "Commit migration? (y/n): " commit_migration

if [ "$commit_migration" = "y" ]; then
    alembic upgrade head
    echo "Migration committed"
else
    echo "Migration not committed. Run 'bash db_run_migration.sh' to commit the migration"
fi
