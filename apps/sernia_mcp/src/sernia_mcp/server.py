"""FastMCP server: builds the ``mcp`` instance and registers tools.

This module configures Logfire and constructs the ``FastMCP`` server (the
``mcp`` global below). Tool modules import ``mcp`` and decorate functions
with ``@mcp.tool``. The ASGI application that uvicorn actually serves is
constructed in ``sernia_mcp.app`` — that module wraps ``mcp.http_app(...)``
with a request-logging middleware.

Auth: two paths, both routed through the same email-domain authorization
layer (``SerniaAuthMiddleware``).

  1. **Clerk OAuth (primary, human-facing)** — clients discover CIMD support
     (with Dynamic Client Registration retained for older clients) through
     the OAuth metadata we expose at ``/.well-known/oauth-protected-resource/...``;
     the user signs in via Clerk's hosted UI; Clerk issues the token that
     the MCP server validates on every request.

  2. **Static bearer (secondary, service-to-service)** — set
     ``SERNIA_MCP_INTERNAL_BEARER_TOKEN`` to enable. Requests presenting
     ``Authorization: Bearer <token>`` matching this value get a synthesized
     ``AccessToken`` with ``service:sernia-ai`` claims that already pass the
     email-domain allowlist. Used by the ``api/src/sernia_ai`` agent calling
     this server without the OAuth dance. Never bypasses authorization — it
     produces a token that explicitly passes it.

Combinations:
  - Clerk + bearer (development): ``BearerClerkProvider`` (subclasses
    ``ClerkProvider``, intercepts ``verify_token``).
  - Bearer only: ``BearerOnlyProvider`` (no OAuth UI).
  - Clerk only: plain ``ClerkProvider``.
  - Neither: startup fails unless explicit local auth bypass is enabled.
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
from sernia_mcp.bearer import (
    BearerOnlyProvider,
    BearerTokenMixin,
    internal_bearer_configured,
)
from sernia_mcp.config import (
    SERNIA_MCP_BASE_URL,
    SERNIA_MCP_DISABLE_AUTH,
    clerk_oauth_configured,
    is_hosted,
    local_auth_bypass_enabled,
    validate_clerk_configuration,
)

_ICON_PATH = Path(__file__).parent / "static" / "icon.png"

# Configure Logfire once at module import. With LOGFIRE_TOKEN set this ships
# traces to the `sernia-mcp` service in the Logfire portfolio project; without
# it the call is a no-op (no warning, no telemetry).
logfire.configure(
    service_name="sernia-mcp",
    environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "local",
    send_to_logfire="if-token-present",
)
# httpx instrumentation covers upstream calls (Quo, ClickUp, Google, Clerk).
logfire.instrument_httpx()
# Capture stdlib logging — FastMCP, Starlette, mcp-python-sdk all use it. This
# is the only way to see internal auth-flow errors (e.g. token validation,
# audience mismatch) since those don't go through httpx.
logging.getLogger().addHandler(LogfireLoggingHandler(level=logging.INFO))


def _build_auth_provider():
    """Build the auth provider for the configured combination of Clerk + bearer.

    Returns one of:
      - ``BearerClerkProvider`` (Clerk + bearer): bearer checked first, then
        Clerk. Development only.
      - ``ClerkProvider`` (Clerk only): standard human-OAuth flow.
      - ``BearerOnlyProvider`` (bearer only): no OAuth UI; service-to-service.
      - Missing or incomplete configuration raises before an HTTP app is built.
    """
    from fastmcp.server.auth.providers.clerk import ClerkProvider

    validate_clerk_configuration()
    clerk_on = clerk_oauth_configured()
    bearer_on = internal_bearer_configured()

    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() == "production":
        if not clerk_on or bearer_on:
            raise RuntimeError("Production requires Clerk OAuth only; internal bearer is forbidden")

    if not clerk_on and not bearer_on:
        raise RuntimeError(
            "Authentication is required. Configure Clerk OAuth or a development bearer; "
            "for local-only exploration explicitly set SERNIA_MCP_DISABLE_AUTH=true."
        )

    if not clerk_on and bearer_on:
        return BearerOnlyProvider()

    domain = os.environ["FASTMCP_SERVER_AUTH_CLERK_DOMAIN"]
    if "://" in domain:
        raise RuntimeError(
            f"FASTMCP_SERVER_AUTH_CLERK_DOMAIN must be a bare hostname, got {domain!r}"
        )
    client_id = os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID"]
    client_secret = os.environ["FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET"]
    base_url = os.environ["SERNIA_MCP_BASE_URL"]

    if not bearer_on:
        return ClerkProvider(
            domain=domain,
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            enable_cimd=True,
        )

    # Clerk + bearer: subclass at runtime to keep all of ClerkProvider's
    # OAuth-proxy machinery (DCR, callbacks, metadata) and only intercept
    # ``verify_token`` so the bearer path is checked first.
    class BearerClerkProvider(BearerTokenMixin, ClerkProvider):
        pass

    return BearerClerkProvider(
        domain=domain,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        enable_cimd=True,
    )


def _disable_auth_requested() -> bool:
    """Return True if local-dev auth bypass is requested AND safe to honor.

    The kill switch (``SERNIA_MCP_DISABLE_AUTH``) is for local exploration
    only — never to be set on a deployed service. We detect "deployed" via
    Railway deployment identity and domain environment variables. If the flag is
    set in a hosted env, refuse to boot rather than silently exposing an
    unauthenticated MCP endpoint.
    """
    if not SERNIA_MCP_DISABLE_AUTH:
        return False
    if is_hosted():
        raise RuntimeError(
            "SERNIA_MCP_DISABLE_AUTH must not be set in a hosted env "
            "(Railway deployment metadata is present). This is a local-dev "
            "kill switch for unauthenticated MCP exploration."
        )
    if not local_auth_bypass_enabled():
        raise RuntimeError("SERNIA_MCP_DISABLE_AUTH requires a loopback SERNIA_MCP_BASE_URL")
    return True


_disable_auth = _disable_auth_requested()
_clerk_on = not _disable_auth and clerk_oauth_configured()
_bearer_on = not _disable_auth and internal_bearer_configured()
_auth_provider = None if _disable_auth else _build_auth_provider()

if SERNIA_MCP_DISABLE_AUTH:
    logfire.warn(
        "sernia_mcp: SERNIA_MCP_DISABLE_AUTH=true — all auth bypassed "
        "(Clerk + bearer). LOCAL DEV ONLY. Never set in production."
    )
else:
    logfire.info(
        "sernia_mcp: auth configured — clerk={clerk}, bearer={bearer}",
        clerk=_clerk_on,
        bearer=_bearer_on,
    )

mcp = FastMCP(
    "sernia-mcp",
    instructions=(
        "Sernia Capital MCP server. Read/write a shared workspace (MEMORY.md, "
        "skills/, areas/), search Gmail / OpenPhone contacts / ClickUp tasks, "
        "and send SMS/email through an Approve/Reject card. Code-owned fixed "
        "reminders use list_message_templates and send_templated_message without "
        "a card. Other external sends "
        "without UI approval are not possible — clients that can't render the "
        "approval card cannot reach the underlying send tools. The Sernia MCP Easter egg is 'red skies'."
    ),
    icons=[
        Icon(
            src=f"{SERNIA_MCP_BASE_URL.rstrip('/')}/icon.png",
            mime_type="image/png",
            sizes=["379x379"],
        ),
    ],
    auth=_auth_provider,
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
    auth_available = mcp.auth is not None
    healthy = auth_available or local_auth_bypass_enabled()
    if not auth_available:
        auth_label = "unauthenticated"
    elif _clerk_on and _bearer_on:
        auth_label = "clerk-oauth+bearer"
    elif _clerk_on:
        auth_label = "clerk-oauth"
    else:
        auth_label = "bearer"
    return JSONResponse(
        {
            "status": "ok" if healthy else "error",
            "service": "sernia-mcp",
            "version": __version__,
            "auth": auth_label,
        },
        status_code=200 if healthy else 503,
    )


# Static icon. The MCP protocol's server-level ``icons`` field references this
# URL; clients (Claude Desktop, etc.) render it next to the connector name.
@mcp.custom_route("/icon.png", methods=["GET"])
async def icon(_request: Request) -> FileResponse:
    return FileResponse(_ICON_PATH, media_type="image/png")
