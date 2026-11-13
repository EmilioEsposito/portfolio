"""Environment-driven config for the Sernia MCP server.

Loads ``.env`` once on import. All constants are read from env with sensible
defaults; unauthenticated local development requires an explicit opt-in. See ``.env.example``
for the full set of variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Load .env from CWD only — never walk parent directories. python-dotenv's
# default ``find_dotenv()`` walks up, which would pick up the monorepo's
# root `.env` and break the self-contained model. Specifying an explicit
# path keeps us pinned to ``./.env``; in production (Railway) env vars are
# set on the service directly and no .env file exists.
load_dotenv(dotenv_path=".env", override=False)

# ---- Public service URL ----------------------------------------------------
# What Claude/ChatGPT connect to. Used to construct OAuth redirect URIs.
SERNIA_MCP_BASE_URL: str = os.environ.get("SERNIA_MCP_BASE_URL", "http://localhost:8080")

# ---- Clerk OAuth -----------------------------------------------------------
_CLERK_ENV_VARS: tuple[str, ...] = (
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
    "SERNIA_MCP_BASE_URL",
)


def clerk_oauth_configured() -> bool:
    """True iff all four Clerk OAuth env vars are present and non-empty."""
    return all(os.environ.get(k, "").strip() for k in _CLERK_ENV_VARS)


# ---- Local-dev auth bypass --------------------------------------------------
# Setting this to a truthy value disables Clerk auth on the MCP server, even
# when the four Clerk env vars are configured. Intended for local exploration
# (MCP Inspector, curl, etc.) where the OAuth dance is just friction. The
# server raises at boot if this flag is set in a hosted environment — see
# ``server._disable_auth_requested``.
SERNIA_MCP_DISABLE_AUTH: bool = os.environ.get("SERNIA_MCP_DISABLE_AUTH", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


# Presence (even an empty value) is deployment evidence; do not rely on a
# single, potentially missing environment name to protect the local bypass.
RAILWAY_HOSTED_VARS = (
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_DEPLOYMENT_ID",
    "RAILWAY_REPLICA_ID",
    "RAILWAY_PUBLIC_DOMAIN",
    "RAILWAY_PRIVATE_DOMAIN",
)


def is_hosted() -> bool:
    return any(key in os.environ for key in RAILWAY_HOSTED_VARS)


def local_auth_bypass_enabled() -> bool:
    """Explicit unauthenticated development, with no deployment indicators."""
    return (
        SERNIA_MCP_DISABLE_AUTH
        and not is_hosted()
        and urlsplit(SERNIA_MCP_BASE_URL).hostname in {"localhost", "127.0.0.1", "::1"}
    )


def validate_clerk_configuration() -> None:
    """A partial OAuth configuration must never downgrade to another mode."""
    if any(os.environ.get(k, "").strip() for k in _CLERK_ENV_VARS[:3]):
        missing = [k for k in _CLERK_ENV_VARS if not os.environ.get(k, "").strip()]
        if missing:
            raise RuntimeError(
                "Incomplete Clerk OAuth configuration; missing: " + ", ".join(missing)
            )


# ---- Identity --------------------------------------------------------------
GOOGLE_DELEGATION_EMAIL: str = os.environ.get("SERNIA_MCP_DEFAULT_USER", "emilio@serniacapital.com")

# ---- Workspace storage -----------------------------------------------------
# Default puts a ./workspace dir next to the CWD. In production / during the
# migration window, set SERNIA_MCP_WORKSPACE_PATH to the monorepo's shared
# `api/src/sernia_ai/workspace/` so the agent and MCP read/write the same files.
WORKSPACE_PATH: Path = Path(os.environ.get("SERNIA_MCP_WORKSPACE_PATH", "./workspace")).resolve()

# ---- Portfolio API ---------------------------------------------------------
# Where the rental listing tools send their requests, e.g.
# https://fastapi-production-xxxx.up.railway.app. The API records each change
# under the signed-in user's name. Empty turns the rental listing tools off.
SERNIA_MCP_API_BASE_URL: str = os.environ.get("SERNIA_MCP_API_BASE_URL", "").strip()

# ---- Quo (OpenPhone) routing -----------------------------------------------
QUO_SERNIA_AI_PHONE_ID: str = os.environ.get("QUO_SERNIA_AI_PHONE_ID", "PNWvNqsFFy")
QUO_SHARED_EXTERNAL_PHONE_ID: str = os.environ.get("QUO_SHARED_EXTERNAL_PHONE_ID", "PNpTZEJ7la")
QUO_INTERNAL_COMPANY: str = os.environ.get("QUO_INTERNAL_COMPANY", "Sernia Capital LLC")

# ---- ClickUp ---------------------------------------------------------------
CLICKUP_TEAM_ID: str = os.environ.get("CLICKUP_TEAM_ID", "90131316997")
# Default ClickUp view used by ``clickup_get_tasks`` when no list_or_view_id
# is passed. "Peppino View" — the curated cross-list task feed.
DEFAULT_CLICKUP_VIEW_ID: str = os.environ.get("DEFAULT_CLICKUP_VIEW_ID", "2ky3xg85-573")
# Sernia maintenance list — backs ``clickup_get_maintenance_field_options``.
CLICKUP_MAINTENANCE_LIST_ID: str = os.environ.get("CLICKUP_MAINTENANCE_LIST_ID", "901312027371")

# ---- Sernia constants ------------------------------------------------------
INTERNAL_EMAIL_DOMAIN: str = os.environ.get("INTERNAL_EMAIL_DOMAIN", "serniacapital.com")

# ---- Authorization allowlist -----------------------------------------------
# Only authenticated users whose email matches one of these domains are
# allowed to call MCP tools. Authentication (Clerk OAuth) is layered on top —
# this enforces *authorization* (who is allowed) on top of authentication
# (who they are). Comma-separated; defaults to INTERNAL_EMAIL_DOMAIN.
ALLOWED_EMAIL_DOMAINS: tuple[str, ...] = tuple(
    d.strip().lower()
    for d in os.environ.get("SERNIA_MCP_ALLOWED_EMAIL_DOMAINS", INTERNAL_EMAIL_DOMAIN).split(",")
    if d.strip()
)

# ---- SMS limits ------------------------------------------------------------
SMS_SPLIT_THRESHOLD: int = 500
SMS_MAX_LENGTH: int = 1000
# Group SMS: max recipients per message — matches the Quo API's `to` array
# limit (maxItems: 10 on POST /v1/messages), verified live 2026-08-13.
GROUP_SMS_MAX_RECIPIENTS: int = 10
