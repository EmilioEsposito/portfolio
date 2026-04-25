"""Environment-driven config for the Sernia MCP server.

Loads ``.env`` once on import. All constants are read from env with sensible
defaults so the server boots in dev with minimal config. See ``.env.example``
for the full set of variables.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from CWD only — never walk parent directories. python-dotenv's
# default ``find_dotenv()`` walks up, which would pick up the monorepo's
# root `.env` and break the self-contained model. Specifying an explicit
# path keeps us pinned to ``./.env``; in production (Railway) env vars are
# set on the service directly and no .env file exists.
load_dotenv(dotenv_path=".env", override=False)

# ---- Public service URL ----------------------------------------------------
# What Claude/ChatGPT connect to. Used to construct OAuth redirect URIs.
SERNIA_MCP_BASE_URL: str = os.environ.get(
    "SERNIA_MCP_BASE_URL", "http://localhost:8080"
)

# ---- Clerk OAuth -----------------------------------------------------------
_CLERK_ENV_VARS: tuple[str, ...] = (
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
    "SERNIA_MCP_BASE_URL",
)


def clerk_oauth_configured() -> bool:
    """True iff all four Clerk OAuth env vars are present and non-empty."""
    return all(os.environ.get(k) for k in _CLERK_ENV_VARS)


# ---- Identity --------------------------------------------------------------
GOOGLE_DELEGATION_EMAIL: str = os.environ.get(
    "SERNIA_MCP_DEFAULT_USER", "emilio@serniacapital.com"
)

# ---- Workspace storage -----------------------------------------------------
# Default puts a ./workspace dir next to the CWD. In production / during the
# migration window, set SERNIA_MCP_WORKSPACE_PATH to the monorepo's shared
# `api/src/sernia_ai/workspace/` so the agent and MCP read/write the same files.
WORKSPACE_PATH: Path = Path(
    os.environ.get("SERNIA_MCP_WORKSPACE_PATH", "./workspace")
).resolve()

# ---- Quo (OpenPhone) routing -----------------------------------------------
QUO_SERNIA_AI_PHONE_ID: str = os.environ.get("QUO_SERNIA_AI_PHONE_ID", "PNWvNqsFFy")
QUO_SHARED_EXTERNAL_PHONE_ID: str = os.environ.get(
    "QUO_SHARED_EXTERNAL_PHONE_ID", "PNpTZEJ7la"
)
QUO_INTERNAL_COMPANY: str = os.environ.get(
    "QUO_INTERNAL_COMPANY", "Sernia Capital LLC"
)

# ---- ClickUp ---------------------------------------------------------------
CLICKUP_TEAM_ID: str = os.environ.get("CLICKUP_TEAM_ID", "90131316997")

# ---- Sernia constants ------------------------------------------------------
INTERNAL_EMAIL_DOMAIN: str = os.environ.get(
    "INTERNAL_EMAIL_DOMAIN", "serniacapital.com"
)

# ---- SMS limits ------------------------------------------------------------
SMS_SPLIT_THRESHOLD: int = 500
SMS_MAX_LENGTH: int = 1000
