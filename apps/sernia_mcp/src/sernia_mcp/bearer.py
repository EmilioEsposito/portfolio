"""Static bearer-token authentication — secondary auth path alongside Clerk.

Use case: server-to-server calls (e.g. the ``api/src/sernia_ai`` agent calling
this MCP server) where the OAuth dance is friction. The token is a single
shared secret kept in ``SERNIA_MCP_INTERNAL_BEARER_TOKEN`` on this service AND
on the calling service; both ends rotate together.

Design choices:

  - **Constant-time compare** (`secrets.compare_digest`) so token presence
    can't be probed via response-time variation.

  - **Synthesizes an ``AccessToken``** that already passes the existing
    email-domain authorization layer (``require_allowed_email_domain``). The
    bearer path doesn't *bypass* authorization — it produces a token that
    explicitly *passes* it, with a ``service:sernia-ai`` client id so audit
    logs distinguish it from human OAuth users.

  - **Fail-closed when not configured**. If ``SERNIA_MCP_INTERNAL_BEARER_TOKEN``
    is empty/unset, ``verify_internal_bearer`` always returns None — no
    accidental "anything authenticates" path.

  - **Minimum length** (32 chars). Enforced at config-read time; weaker
    secrets are refused at boot rather than papered over at runtime.
"""
from __future__ import annotations

import os
import secrets

from fastmcp.server.auth.auth import AccessToken, AuthProvider

from sernia_mcp.config import ALLOWED_EMAIL_DOMAINS, SERNIA_MCP_BASE_URL

_BEARER_ENV_VAR = "SERNIA_MCP_INTERNAL_BEARER_TOKEN"
_BEARER_MIN_LENGTH = 32

# Synthesized identity for any caller presenting the internal bearer.
# ``client_id`` is the audit-trail signal — anything starting with
# ``service:`` is a non-human caller and should be logged accordingly.
_BEARER_CLIENT_ID = "service:sernia-ai"


def _bearer_email() -> str:
    """Synthesize an email that will pass ``require_allowed_email_domain``.

    Uses the first entry of the allowlist so operators who configure a
    non-default ``SERNIA_MCP_ALLOWED_EMAIL_DOMAINS`` still get a passing
    token. Falls back to ``serniacapital.com`` only if the allowlist is
    somehow empty (shouldn't happen — config defaults guard against it).
    """
    if ALLOWED_EMAIL_DOMAINS:
        return f"agent@{ALLOWED_EMAIL_DOMAINS[0]}"
    return "agent@serniacapital.com"


def internal_bearer_configured() -> bool:
    """True iff ``SERNIA_MCP_INTERNAL_BEARER_TOKEN`` is set and long enough."""
    raw = os.environ.get(_BEARER_ENV_VAR, "").strip()
    return bool(raw) and len(raw) >= _BEARER_MIN_LENGTH


def _expected_token() -> str | None:
    """Return the configured bearer token, or None if not configured.

    Raises ``RuntimeError`` if the env var is set but shorter than
    ``_BEARER_MIN_LENGTH`` — fail-fast on weak secrets.
    """
    raw = os.environ.get(_BEARER_ENV_VAR, "").strip()
    if not raw:
        return None
    if len(raw) < _BEARER_MIN_LENGTH:
        raise RuntimeError(
            f"{_BEARER_ENV_VAR} must be at least {_BEARER_MIN_LENGTH} characters; "
            f"got {len(raw)}. Generate with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    return raw


def verify_internal_bearer(token: str) -> AccessToken | None:
    """Constant-time check against the configured internal bearer token.

    Returns a synthesized ``AccessToken`` (with claims that pass the
    email-domain allowlist) if the token matches, else None. Returns None
    when the bearer auth path is not configured — never raises on input.
    """
    expected = _expected_token()
    if expected is None:
        return None
    if not secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        return None

    return AccessToken(
        token=token,
        client_id=_BEARER_CLIENT_ID,
        scopes=[],
        expires_at=None,
        claims={
            "sub": _BEARER_CLIENT_ID,
            "email": _bearer_email(),
            "auth_method": "internal_bearer",
        },
    )


class BearerTokenMixin:
    """Mixin that adds bearer-token verification ahead of the wrapped provider.

    Override order: bearer check first (cheap, constant-time), then delegate
    to ``super().verify_token`` for the OAuth path. If bearer is unconfigured
    the cheap check returns None and we fall straight through — no observable
    cost when the secondary path is disabled.
    """

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        synth = verify_internal_bearer(token)
        if synth is not None:
            return synth
        return await super().verify_token(token)  # type: ignore[misc]


class BearerOnlyProvider(AuthProvider):
    """Standalone provider for the bearer-only case (no Clerk configured).

    Use when ``clerk_oauth_configured()`` is False but ``internal_bearer_configured()``
    is True — e.g. a deploy that only fronts service-to-service calls and
    doesn't need the OAuth UI flow. Exposes no OAuth routes; ``verify_token``
    returns None for anything but the internal bearer.
    """

    def __init__(self) -> None:
        super().__init__(base_url=SERNIA_MCP_BASE_URL)

    async def verify_token(self, token: str) -> AccessToken | None:
        return verify_internal_bearer(token)
