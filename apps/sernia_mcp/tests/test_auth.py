"""Tests for the email-domain authorization layer.

Covers the ``require_allowed_email_domain`` callable directly (the same
callable that ``AuthMiddleware`` invokes). These tests pin the load-bearing
behavior: authenticated Clerk users from outside ``ALLOWED_EMAIL_DOMAINS``
must be rejected, even if Clerk would have happily issued them a token.

Also pins the ``SerniaAuthMiddleware.on_read_resource`` override that lets
FastMCP's Prefab/Apps ``ui://`` synthesis run without being short-circuited
by the parent's resource-existence precheck.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import AuthorizationError


@dataclass
class _FakeToken:
    """Minimal stand-in for ``fastmcp.server.auth.auth.AccessToken``."""

    claims: dict


@dataclass
class _FakeCtx:
    token: _FakeToken | None


def test_allows_email_from_internal_domain():
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=_FakeToken(claims={"email": "emilio@serniacapital.com"}))
    assert require_allowed_email_domain(ctx) is True


def test_allows_uppercase_and_whitespace():
    """Domain check is case-insensitive and tolerant of stray whitespace."""
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=_FakeToken(claims={"email": "  Emilio@SerniaCapital.com  "}))
    assert require_allowed_email_domain(ctx) is True


def test_rejects_external_domain():
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=_FakeToken(claims={"email": "stranger@example.com"}))
    with pytest.raises(AuthorizationError, match="not in the allowlist"):
        require_allowed_email_domain(ctx)


def test_rejects_missing_email_claim():
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=_FakeToken(claims={}))
    with pytest.raises(AuthorizationError, match="no email claim"):
        require_allowed_email_domain(ctx)


def test_rejects_malformed_email():
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=_FakeToken(claims={"email": "not-an-email"}))
    with pytest.raises(AuthorizationError, match="malformed"):
        require_allowed_email_domain(ctx)


def test_passes_through_when_no_token():
    """Unauth dev mode: provider didn't validate a token, so this layer
    has nothing to authorize. The auth provider gates that case.
    """
    from sernia_mcp.auth import require_allowed_email_domain

    ctx = _FakeCtx(token=None)
    assert require_allowed_email_domain(ctx) is True


@pytest.mark.asyncio
async def test_sernia_auth_middleware_bypasses_ui_resources():
    """``ui://`` resources are FastMCP-internal (Prefab/Apps UI) and are
    synthesized on demand. The parent's resource-existence precheck would
    fail them with 'resource not found'; our subclass must skip the check.
    """
    from sernia_mcp.auth import SerniaAuthMiddleware

    middleware = SerniaAuthMiddleware(auth=lambda ctx: True)
    next_call = AsyncMock(return_value="rendered-html")
    context = SimpleNamespace(
        message=SimpleNamespace(uri="ui://prefab/tool/abc123/renderer.html"),
        fastmcp_context=None,  # parent would crash on this; we should never reach parent
    )

    result = await middleware.on_read_resource(context, next_call)

    assert result == "rendered-html"
    next_call.assert_awaited_once_with(context)


@pytest.mark.asyncio
async def test_sernia_auth_middleware_delegates_non_ui_resources(monkeypatch):
    """Non-``ui://`` URIs must still go through the parent's auth check."""
    from sernia_mcp.auth import SerniaAuthMiddleware

    parent_called_with = {}

    async def fake_super(self, context, call_next):
        parent_called_with["uri"] = str(context.message.uri)
        return "parent-result"

    middleware = SerniaAuthMiddleware(auth=lambda ctx: True)
    monkeypatch.setattr(
        "fastmcp.server.middleware.AuthMiddleware.on_read_resource", fake_super
    )

    context = SimpleNamespace(
        message=SimpleNamespace(uri="file:///some/regular/resource.md"),
    )
    result = await middleware.on_read_resource(context, AsyncMock())

    assert result == "parent-result"
    assert parent_called_with["uri"] == "file:///some/regular/resource.md"


def test_respects_env_var_for_extra_domains(monkeypatch):
    """Operators can extend the allowlist via SERNIA_MCP_ALLOWED_EMAIL_DOMAINS."""
    monkeypatch.setenv(
        "SERNIA_MCP_ALLOWED_EMAIL_DOMAINS", "serniacapital.com,partner.example"
    )
    import importlib

    import sernia_mcp.auth as _auth
    import sernia_mcp.config as _config

    importlib.reload(_config)
    importlib.reload(_auth)

    ctx_partner = _FakeCtx(token=_FakeToken(claims={"email": "x@partner.example"}))
    assert _auth.require_allowed_email_domain(ctx_partner) is True

    ctx_outsider = _FakeCtx(token=_FakeToken(claims={"email": "x@evil.example"}))
    with pytest.raises(AuthorizationError):
        _auth.require_allowed_email_domain(ctx_outsider)
