# Cursor Cloud Agent Configuration

This directory contains the configuration for Cursor Cloud Agents.

## Files

### `environment.json`
Main configuration file that defines:
- **snapshot**: Base environment image (Ubuntu 22.04 with Node.js, Python, pnpm, uv, PostgreSQL pre-installed)
- **install**: Script that runs when the agent starts (`./.cursor/install.sh`)
- **ports**: Exposed ports for React Router (5173) and FastAPI (8000)
- **terminals**: Pre-configured terminal processes for development
- **agentCanUpdateSnapshot**: Allows the agent to rebuild the snapshot from Dockerfile when needed

### `Dockerfile`
Defines the **base environment** that gets built into the snapshot. It installs:
1. System utilities (curl, git, sudo, etc.)
2. Node.js 20.x & pnpm
3. Python 3.11 & uv
4. PostgreSQL 16
5. Creates the Python virtual environment at `/home/ubuntu/.venv`

**When to rebuild:** The Dockerfile is used to create/update the snapshot. You typically don't need to rebuild unless you're updating base tools (e.g., upgrading Node, Python, or adding new system packages).

### `install.sh`
Installation script that runs automatically when a Cursor cloud agent starts. It installs **project-specific dependencies**:
1. Installs Node.js project dependencies with `pnpm install`
2. Installs Python project dependencies with `uv sync`
3. Starts PostgreSQL service
4. Creates database user and database
5. Runs database migrations with Alembic
6. Configures PYTHONPATH

## Architecture: Two-Stage Setup

According to the [Cursor docs](https://cursor.com/docs/cloud-agent#base-environment-setup), the setup is split into two stages:

### Stage 1: Base Environment (Dockerfile → Snapshot)
- **What**: System-level tools (Node.js, Python, pnpm, uv, PostgreSQL)
- **When**: One-time or when base tools need updating
- **How**: Dockerfile builds → creates snapshot → snapshot ID saved in `environment.json`
- **Result**: Fast startup for all future agents (heavy dependencies are pre-installed)

### Stage 2: Project Dependencies (install.sh)
- **What**: Project-specific packages (npm modules, Python packages)
- **When**: Every time an agent starts
- **How**: `install.sh` runs using tools from the snapshot
- **Result**: Fresh dependencies, using the cached base environment

## Key Differences from Local Development

The Cursor cloud agent environment has some differences from your local laptop:

1. **No `.env` file**: Environment variables are injected directly by Cursor
2. **PostgreSQL is local**: Uses local PostgreSQL instead of Neon (which isn't reachable from cloud agents)
3. **Virtual environment**: Python venv at `/home/ubuntu/.venv` is automatically activated via `$VIRTUAL_ENV` and `$PATH`
4. **PYTHONPATH**: Set to the repo directory (where Cursor clones your code) to ensure imports work correctly

## Usage

When you launch a Cursor cloud agent:

1. The snapshot environment loads (Ubuntu + tools pre-installed)
2. `install.sh` runs automatically to set up the project
3. Three terminal tabs open automatically:
   - **React Router**: Development server on port 5173
   - **FastAPI**: Backend server on port 8000
   - **ExpoWeb**: Expo web development server

## How to Update the Snapshot

When you need to update base tools (e.g., upgrade Node.js, add system packages), you need to rebuild the snapshot:

1. **Edit the Dockerfile** with your changes (e.g., upgrade Node to 22.x)
2. **Update environment.json** to use build mode temporarily:
   ```json
   {
     "build": {
       "dockerfile": ".cursor/Dockerfile",
       "context": "."
     },
     "agentCanUpdateSnapshot": true,
     ...
   }
   ```
3. **Launch a cloud agent** - it will build from the Dockerfile
4. **Get the new snapshot ID** from the agent's build output
5. **Update environment.json** back to snapshot mode:
   ```json
   {
     "snapshot": "snapshot-XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX",
     "agentCanUpdateSnapshot": true,
     ...
   }
   ```

With `agentCanUpdateSnapshot: true`, Cursor can also rebuild the snapshot automatically when needed.

## Comparison with Codex Cloud Agent

| Aspect | Cursor (`.cursor/`) | Codex (`.codex/`) |
|--------|---------------------|-------------------|
| Config file | `environment.json` | Configuration handled differently |
| Setup script | `install.sh` | `setup.sh` |
| Approach | Snapshot-based (Dockerfile → snapshot) | Similar two-stage setup |
| Purpose | Cursor cloud agent | Codex cloud agent |

Both are similar in functionality but are separate systems. Don't confuse the two!

## Troubleshooting

If the agent fails to start:
1. Check `install.sh` output for errors
2. Verify PostgreSQL started successfully
3. Ensure migrations completed without errors
4. Check that pnpm and uv dependencies installed correctly

## References

- [Cursor Cloud Agent Docs](https://cursor.com/docs/cloud-agent)
- [Environment Schema](https://cursor.com/schemas/environment.schema.json)

