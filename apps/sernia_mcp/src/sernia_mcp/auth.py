"""Authorization layered on top of Clerk authentication.

Two distinct concerns:

  - **Authentication** (who are you?) — handled by Clerk OAuth via FastMCP's
    ``ClerkProvider``. The bearer token is validated against Clerk's
    introspection + userinfo endpoints; an authenticated request reaches this
    module only after that validation succeeded.

  - **Authorization** (are you allowed?) — enforced here. Even with a valid
    Clerk token, a request is rejected unless the user's email domain is in
    ``config.ALLOWED_EMAIL_DOMAINS``. This is a defense-in-depth check on top
    of any restrictions configured in the Clerk dashboard.

The check runs as a FastMCP ``AuthMiddleware`` — it filters list responses
(tools, resources, prompts) and rejects calls before they reach the handler.
Without this, any authenticated Clerk user — including users who signed in to
the same Clerk instance for an unrelated app — could call MCP tools.
"""
from __future__ import annotations

from typing import cast

import mcp.types as mt
from fastmcp.exceptions import AuthorizationError
from fastmcp.resources.base import ResourceResult
from fastmcp.server.auth.authorization import AuthContext, run_auth_checks
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.server.providers.addressing import parse_hashed_backend_name
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.components import FastMCPComponent

from sernia_mcp.config import ALLOWED_EMAIL_DOMAINS


def require_allowed_email_domain(ctx: AuthContext) -> bool:
    """Allow only authenticated users whose email is in an allowed domain.

    If ``ctx.token`` is None, no auth is configured (local dev / tests). We
    let the request through here — gating that case is the responsibility of
    the auth provider, not this authorization layer.

    Raises:
        AuthorizationError: when the token has no email claim, or when the
            email's domain is not in ``ALLOWED_EMAIL_DOMAINS``.
    """
    token = ctx.token
    if token is None:
        return True

    email = (token.claims or {}).get("email", "")
    if not isinstance(email, str) or not email:
        raise AuthorizationError("authenticated token has no email claim")
    email = email.strip().lower()
    if "@" not in email:
        raise AuthorizationError(f"malformed email claim: {email!r}")
    domain = email.rsplit("@", 1)[1]
    if domain not in ALLOWED_EMAIL_DOMAINS:
        raise AuthorizationError(
            f"access denied: email domain {domain!r} is not in the allowlist"
        )
    return True


class SerniaAuthMiddleware(AuthMiddleware):
    """``AuthMiddleware`` that bypasses the resource-existence check for
    FastMCP's own ``ui://`` resources.

    The parent middleware looks up the resource via ``get_resource(uri)`` and
    raises ``"resource not found"`` when the lookup returns None. That breaks
    the FastMCP Apps approval flow: Prefab renderer resources at
    ``ui://prefab/tool/<hash>/renderer.html`` aren't statically registered —
    they're synthesized on demand by ``server/providers/prefab_synthesis.py``
    when the read request reaches the handler. The lookup returning None
    isn't an authorization failure; it's the wrong layer trying to enforce
    something that doesn't apply.

    ``ui://`` is reserved for FastMCP-internal UI rendering (Apps + Generative
    UI). User-defined resources use ``file://``, ``https://``, ``data://``,
    etc., so bypassing the ``get_resource`` precheck for ``ui://`` URIs
    doesn't widen the trust boundary — those URIs aren't user-addressable
    surface anyway. The tool call that produced the renderer URI has already
    passed the email-domain authorization check via the same middleware's
    ``on_call_tool`` hook.
    """

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        if str(context.message.uri).startswith("ui://"):
            return await call_next(context)
        return await super().on_read_resource(context, call_next)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Authorize tool calls, including hashed Apps backend tools.

        FastMCP's Apps protocol routes ``CallTool`` actions fired from Prefab
        UI buttons via *hashed backend names* — ``<12-hex>_<local_name>`` —
        rather than the local tool name. The parent's ``on_call_tool`` does
        ``get_tool(name)``, which doesn't resolve the hashed form, and rejects
        the call with ``"tool not found"`` before FastMCP's app-tool
        dispatcher can route it. That breaks every Prefab approval-card
        button click under any ``AuthMiddleware``.

        For hashed backend names we still run the global email-domain auth
        check (``run_auth_checks``) — the user identity check shouldn't be
        skipped — but we don't pre-resolve a component, since the dispatcher
        in ``call_next`` resolves and routes via the hash.
        """
        if parse_hashed_backend_name(context.message.name) is not None:
            token = get_access_token()
            # No component to resolve here — the hashed dispatcher in
            # ``call_next`` does that. The check function (require_allowed_email_domain)
            # only inspects ``ctx.token``; the cast keeps ty happy.
            ctx = AuthContext(token=token, component=cast(FastMCPComponent, None))
            if not await run_auth_checks(self.auth, ctx):
                raise AuthorizationError(
                    f"Authorization failed for app tool '{context.message.name}': "
                    "insufficient permissions"
                )
            return await call_next(context)
        return await super().on_call_tool(context, call_next)
