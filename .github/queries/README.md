# Railway GraphQL Queries

This directory contains GraphQL query files for Railway API operations used in GitHub Actions workflows.

## Files

- `railway_upsert_database_vars.graphql` - Query for updating database environment variables (used in Neon branch workflow)

## Usage

The script `railway_upsert_database_vars.sh` is used in the GitHub Actions workflow to update database URLs when Neon branches are created.

### Testing Locally

You can test the script locally:

```bash
export RAILWAY_API_TOKEN="your-token"
export RAILWAY_PROJECT_ID="your-project-id"
export RAILWAY_ENV_ID="your-environment-id"
export RAILWAY_FASTAPI_SERVICE_ID="your-service-id"
export DB_URL_POOLED="postgresql://..."
export DB_URL_UNPOOLED="postgresql://..."
export INFORMATIONAL_NEON_BRANCH_NAME="pr-123-feature-branch"

./.github/scripts/railway_upsert_database_vars.sh
```

## Notes

- The `jq` tool is used to properly escape JSON (handles quotes, newlines, etc.)
- Variable substitution uses `sed` for portability (works in GitHub Actions and most Unix systems)
- The GraphQL query file uses `${VARIABLE_NAME}` syntax for placeholders
- The script validates required environment variables before execution

