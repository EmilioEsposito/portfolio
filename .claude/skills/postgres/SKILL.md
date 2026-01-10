# Overview

This project uses Postgres as the database. Specifically, we use Neon Postgres, which has database branching. Local development has its own local branch. 

Exceptions: When AI agents run in the cloud, they use a local Postgres instance instead of the Neon instance. Local AI Agents use the Neon instance though. 

All commands should be run from the root of the project.

# Check if the database is reachable
```bash
source .env
psql "$DATABASE_URL" -c "select 1;"
```

# list all tables in the default database and schema
```bash
psql "$DATABASE_URL" -c "\dt"
```

# Run migrations
```bash
uv run alembic upgrade head
```

# Seed the database
```bash
uv run python api/seed_db.py
```