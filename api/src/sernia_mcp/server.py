"""
FastMCP HTTP server for Sernia tools.

Mounted under /api/mcp in api/index.py.

Auth is via Clerk OAuth (FastMCP's ClerkProvider). Claude Desktop /
Claude.ai custom connectors do DCR against the OAuth metadata we expose;
user signs in via Clerk's hosted UI; Clerk issues the token that the MCP
server validates on every request.

**Clerk dashboard setup required** (one-time, see api/src/sernia_mcp/README.md):
  1. Create an OAuth Application in Clerk.
  2. Add authorized redirect URI: {SERNIA_MCP_BASE_URL}/auth/callback
  3. Copy Client ID + Client Secret into .env as the three vars below.

If the three Clerk env vars are not all set, the MCP endpoint is NOT
mounted (api/index.py handles this). The rest of the FastAPI app still
boots — this is intentional so the Sernia AI web chat keeps working even
when MCP is unconfigured.
"""
import os

from fastmcp import FastMCP

_CLERK_ENV_VARS = (
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
    "SERNIA_MCP_BASE_URL",
)


def _clerk_env_configured() -> bool:
    """Return True iff all Clerk OAuth env vars are present and non-empty."""
    return all(os.environ.get(k) for k in _CLERK_ENV_VARS)


def _build_auth_provider():
    """Construct a ClerkProvider from env vars. Called only when all vars set."""
    from fastmcp.server.auth.providers.clerk import ClerkProvider

    return ClerkProvider(
        domain=os.environ["FASTMCP_SERVER_AUTH_CLERK_DOMAIN"],
        client_id=os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID"],
        client_secret=os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET"],
        base_url=os.environ["SERNIA_MCP_BASE_URL"],
    )


is_configured = _clerk_env_configured()

mcp = FastMCP(
    "sernia-mcp",
    instructions=(
        "Sernia Capital MCP server. Read/write a shared workspace (MEMORY.md, "
        "skills/, areas/), search Gmail / OpenPhone contacts / ClickUp tasks, "
        "and send internal-only SMS/email. External sends are not supported via "
        "MCP — use the Sernia AI web chat for those (HITL approval required)."
    ),
    auth=_build_auth_provider() if is_configured else None,
)

# Side-effect imports register @mcp.tool functions.
from api.src.sernia_mcp.tools import clickup, google, quo, workspace  # noqa: E402,F401

# FastMCPApp-based approval flow (tool-visibility split for deterministic HITL).
from api.src.sernia_mcp.tools.approvals import approvals_app  # noqa: E402

mcp.add_provider(approvals_app)
