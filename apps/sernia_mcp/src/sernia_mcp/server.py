"""FastMCP server entry point.

The ``mcp`` instance below is the entrypoint declared in ``fastmcp.json``,
so ``fastmcp run`` (and ``fastmcp dev``) will pick it up automatically. For
production we run via ``fastmcp run`` over HTTP — see CLAUDE.md.

Auth: Clerk OAuth via ``ClerkProvider``. Claude Desktop / Claude.ai custom
connectors do Dynamic Client Registration (RFC 7591) against the OAuth
metadata we expose at ``/.well-known/oauth-protected-resource/...``; the
user signs in via Clerk's hosted UI; Clerk issues the token that the MCP
server validates on every request.

If the four Clerk env vars (``FASTMCP_SERVER_AUTH_CLERK_DOMAIN``,
``_CLIENT_ID``, ``_CLIENT_SECRET``, ``SERNIA_MCP_BASE_URL``) are not all set,
the server boots **without** auth — useful for local dev and tests, never
expose this state to a public network.
"""
from __future__ import annotations

import os

import logfire
from fastmcp import FastMCP

from sernia_mcp.config import clerk_oauth_configured


def _build_auth_provider():
    """Construct a ClerkProvider from env. Called only when fully configured."""
    from fastmcp.server.auth.providers.clerk import ClerkProvider

    return ClerkProvider(
        domain=os.environ["FASTMCP_SERVER_AUTH_CLERK_DOMAIN"],
        client_id=os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID"],
        client_secret=os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET"],
        base_url=os.environ["SERNIA_MCP_BASE_URL"],
    )


_oauth_configured = clerk_oauth_configured()
if not _oauth_configured:
    logfire.warn(
        "sernia_mcp: Clerk OAuth env vars not set — server will run UNAUTHENTICATED. "
        "Acceptable for local dev and tests; never expose this state publicly."
    )

mcp = FastMCP(
    "sernia-mcp",
    instructions=(
        "Sernia Capital MCP server. Read/write a shared workspace (MEMORY.md, "
        "skills/, areas/), search Gmail / OpenPhone contacts / ClickUp tasks, "
        "and send SMS/email through an Approve/Reject card. External sends "
        "without UI approval are not possible — clients that can't render the "
        "approval card cannot reach the underlying send tools."
    ),
    auth=_build_auth_provider() if _oauth_configured else None,
)

# Side-effect imports register @mcp.tool functions on the instance above.
import sernia_mcp.tools  # noqa: E402,F401

# FastMCPApp-based approval flow (tool-visibility split for deterministic HITL).
from sernia_mcp.tools.approvals import approvals_app  # noqa: E402

mcp.add_provider(approvals_app)
