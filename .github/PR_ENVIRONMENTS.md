# PR Environment Database Branching

Each pull request automatically gets an isolated Neon database branch and Railway environment.

## How It Works

**Workflow**: `.github/workflows/neon_workflow.yml`

### On PR Open/Synchronize

1. Creates a Neon database branch: `pr-{number}-{branch-name}`
2. Finds the Railway PR environment (pattern: `portfolio-pr-{number}` or `pr-{number}`)
3. Updates Railway environment variables:
   - `DATABASE_URL` (pooled)
   - `DATABASE_URL_UNPOOLED` (direct)
   - `INFORMATIONAL_NEON_BRANCH_NAME`
4. Railway redeploys based on `skipDeploys` setting:
   - If `skipDeploys=true`: Waits for CI to complete (if "Wait for CI" is enabled)
   - If `skipDeploys=false`: Triggers immediate redeploy
   - After redeploy, runs migrations via predeploy hook

### On PR Close

- Deletes the Neon database branch
- Railway auto-cleans up the PR environment

## Required Secrets & Variables

| Type | Name | Description |
|------|------|-------------|
| Secret | `NEON_API_KEY` | Neon API key for branch management |
| Secret | `RAILWAY_GHA_TOKEN` | Railway Account Token (not Project Token) |
| Variable | `NEON_PROJECT_ID` | Neon project ID |
| Variable | `RAILWAY_PROJECT_ID` | Railway project ID |
| Variable | `RAILWAY_FASTAPI_SERVICE_ID` | Railway FastAPI service ID |

## Neon Branch Details

- **Pattern**: `pr-{PR_NUMBER}-{branch-name}`
- **Expiration**: 14 days after creation
- **Type**: Full data copy (not schema-only)

## Railway Environment Matching

The workflow searches for environments matching:
- `portfolio-pr-{number}`
- `portfolio-pr-{number}-{hash}`
- `pr-{number}`
- `pr-{number}-{hash}`

Retries up to 6 times (10s apart) if not found immediately.

## Database Migrations

Migrations run automatically via Railway's predeploy phase:
- Config: `api/railway_fastapi.json`
- Command: `source .venv/bin/activate && alembic upgrade head`
- Uses `DATABASE_URL_UNPOOLED` for direct connection

## Debugging

```bash
# Check Neon branches
# Neon Console → Project → Branches

# Check Railway environments
# Railway Dashboard → Project → Environments

# View workflow logs
# GitHub → Actions → "Create/Delete Branch for Pull Request"
```

## Skipped PRs

PRs from `dev` → `main` are skipped (no separate database branch needed).
