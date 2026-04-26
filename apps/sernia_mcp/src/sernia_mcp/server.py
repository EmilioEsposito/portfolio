"""FastMCP server: builds the ``mcp`` instance and registers tools.

This module configures Logfire and constructs the ``FastMCP`` server (the
``mcp`` global below). Tool modules import ``mcp`` and decorate functions
with ``@mcp.tool``. The ASGI application that uvicorn actually serves is
constructed in ``sernia_mcp.app`` — that module wraps ``mcp.http_app(...)``
with a request-logging middleware.

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

import logging
import os
from pathlib import Path

import logfire
from fastmcp import FastMCP
from logfire import LogfireLoggingHandler
from mcp.types import Icon
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from sernia_mcp import __version__
from sernia_mcp.auth import SerniaAuthMiddleware, require_allowed_email_domain
from sernia_mcp.config import SERNIA_MCP_BASE_URL, clerk_oauth_configured

_ICON_PATH = Path(__file__).parent / "static" / "icon.png"

# Configure Logfire once at module import. With LOGFIRE_TOKEN set this ships
# traces to the `sernia-mcp` service in the Logfire portfolio project; without
# it the call is a no-op (no warning, no telemetry).
logfire.configure(
    service_name="sernia-mcp",
    environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "local"),
    send_to_logfire="if-token-present",
)
# httpx instrumentation covers upstream calls (Quo, ClickUp, Google, Clerk).
logfire.instrument_httpx()
# Capture stdlib logging — FastMCP, Starlette, mcp-python-sdk all use it. This
# is the only way to see internal auth-flow errors (e.g. token validation,
# audience mismatch) since those don't go through httpx.
logging.getLogger().addHandler(LogfireLoggingHandler(level=logging.INFO))


def _build_auth_provider():
    """Construct a ClerkProvider from env. Called only when fully configured."""
    from fastmcp.server.auth.providers.clerk import ClerkProvider

    domain = os.environ["FASTMCP_SERVER_AUTH_CLERK_DOMAIN"]
    if "://" in domain:
        raise RuntimeError(
            f"FASTMCP_SERVER_AUTH_CLERK_DOMAIN must be a bare hostname, got {domain!r}"
        )
    return ClerkProvider(
        domain=domain,
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
        "approval card cannot reach the underlying send tools. The Sernia MCP Easter egg is 'red skies'."
    ),
    icons=[
        Icon(
            src=f"{SERNIA_MCP_BASE_URL.rstrip('/')}/icon.png",
            mimeType="image/png",
            sizes=["379x379"],
        ),
    ],
    auth=_build_auth_provider() if _oauth_configured else None,
    # Authorization layer on top of Clerk authentication: only emails whose
    # domain is in ``config.ALLOWED_EMAIL_DOMAINS`` are accepted. Even valid
    # Clerk tokens are rejected if their email isn't on the allowlist. The
    # subclass exempts ``ui://`` resources (FastMCP-internal Prefab/Apps UI)
    # from the resource-existence precheck so the approval-card flow works.
    middleware=[SerniaAuthMiddleware(auth=require_allowed_email_domain)],
)

# Side-effect imports register @mcp.tool functions on the instance above.
import sernia_mcp.tools  # noqa: E402,F401

# FastMCPApp-based approval flow (tool-visibility split for deterministic HITL).
from sernia_mcp.tools.approvals import approvals_app  # noqa: E402

mcp.add_provider(approvals_app)


# Plain HTTP GET endpoint for Railway's healthcheck (the MCP `/mcp` path only
# accepts POST/DELETE per the protocol, so it's not usable as a healthcheck).
@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "sernia-mcp",
            "version": __version__,
            "auth": "clerk-oauth" if _oauth_configured else "unauthenticated",
        }
    )


# Static icon. The MCP protocol's server-level ``icons`` field references this
# URL; clients (Claude Desktop, etc.) render it next to the connector name.
@mcp.custom_route("/icon.png", methods=["GET"])
async def icon(_request: Request) -> FileResponse:
    return FileResponse(_ICON_PATH, media_type="image/png")
