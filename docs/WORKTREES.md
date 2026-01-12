# Git Worktrees

Git worktrees allow parallel development with fully isolated environments. Each worktree has its own ports, database, and dependencies while sharing the same git history.

## Quick Start

```bash
# Create
./scripts/worktree-create.sh feature-auth

# Remove (accepts either format)
./scripts/worktree-remove.sh feature-auth
./scripts/worktree-remove.sh portfolio-feature-auth
```

## What Gets Created

| Resource | Main | Worktree |
|----------|------|----------|
| Directory | `portfolio/` | `portfolio-<desc>/` |
| Branch | `main` | `<desc>` (branched from current) |
| FastAPI Port | 8000 | 8000 + offset |
| Frontend Port | 5173 | 5173 + offset |
| Database | `portfolio` | `portfolio_<desc>` |

The new branch is created from whatever branch you're currently on. Port offset is deterministic (hash-based), so the same description always gets the same ports.

## Architecture

```
portfolio/                    # Main worktree
├── .git/                     # Shared git directory
├── .env                      # PORT=8000, DATABASE_URL=.../portfolio
└── apps/web-react-router/
    └── .env                  # BACKEND_PORT=8000

portfolio-feature-auth/       # Worktree (sibling folder)
├── .git → ../portfolio/.git/worktrees/feature-auth
├── .env                      # PORT=8340, DATABASE_URL=.../portfolio_feature_auth
├── .venv/                    # Isolated Python environment
├── node_modules/             # Isolated Node dependencies
└── apps/web-react-router/
    └── .env                  # BACKEND_PORT=8340
```

## Environment Variables

### Root `.env` (FastAPI)
```bash
PORT=8340                     # Server port (read by api/index.py)
DATABASE_URL=postgresql://portfolio:portfolio@localhost:5432/portfolio_feature_auth
DATABASE_REQUIRE_SSL=false    # Always false for local postgres
```

### `apps/web-react-router/.env` (Frontend)
```bash
BACKEND_PORT=8340             # Where vite proxies /api requests
VITE_PORT=5513                # Vite dev server port
```

## Database Setup

Worktrees share a single PostgreSQL instance (from main's `docker compose up -d postgres`) but use separate databases.

The create script attempts to copy data from main:
```bash
pg_dump portfolio | psql portfolio_feature_auth
```

If that fails (e.g., main DB doesn't exist), it runs:
```bash
alembic upgrade head
python api/seed_db.py
```

## How Ports Work

Ports are computed from a hash of the description:
```bash
offset = ((hash(description) % 99) + 1) * 10  # 10, 20, 30, ..., 990 (never 0)
fastapi_port = 8000 + offset
frontend_port = 5173 + offset
```

This ensures:
- Same description always gets same ports
- Offset is never 0, avoiding conflicts with main (8000/5173)
- Low collision probability for different descriptions
- Ports stay in reasonable ranges (8010-8990, 5183-6163)

## VS Code / Cursor Integration

The create script runs `cursor --add <worktree_dir>` to add the folder to your workspace. The remove script runs `cursor --remove`.

Debug configurations work automatically because:
- `.vscode/launch.json` runs `python api/index.py` with `envFile: "${workspaceFolder}/.env"`
- `api/index.py` reads `PORT` from environment in its `if __name__ == "__main__"` block
- `vite.config.ts` reads `BACKEND_PORT` and `VITE_PORT` from environment

## Requirements

- **Docker**: Postgres container is started automatically
- **Cursor CLI**: Optional, for workspace integration
- **Run from main**: Scripts must be run from main worktree, not from within a worktree

## Cleanup

The remove script:
1. Drops the database (`portfolio_<desc>`)
2. Removes folder from Cursor workspace
3. Removes git worktree
4. Optionally deletes the branch

## Troubleshooting

**"PostgreSQL failed to start"**
- Ensure Docker is running
- Check if port 5432 is available: `lsof -i :5432`

**"This appears to be a worktree"**
- Run scripts from the main `portfolio/` directory, not from a worktree

**Ports conflict**
- Two descriptions hashed to same offset (rare)
- Manually set different `PORT` and `BACKEND_PORT` in the worktree's `.env` files

**Database copy failed**
- Main database may not exist or be empty
- Script falls back to migrations + seed automatically
