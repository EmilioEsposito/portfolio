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

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.authorization import AuthContext

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
