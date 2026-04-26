"""Tests for the internal bearer-token auth path.

Pins the secondary auth path that lets internal services (the api/ Sernia AI
agent, future tools) call the MCP server without doing the Clerk OAuth dance.
The bearer is a single shared secret; matches synthesize an ``AccessToken``
that *passes* the email-domain authorization layer rather than bypassing it.
"""
from __future__ import annotations

import importlib

import pytest


_VALID_TOKEN = "X" * 48  # >= 32 chars, length-only check
_OTHER_TOKEN = "Y" * 48


def _reload_bearer():
    """Reload bearer + config so env-var changes take effect.

    The ``_isolate_environment`` autouse fixture in conftest already sets
    ``SERNIA_MCP_INTERNAL_BEARER_TOKEN=""`` and reloads config. Tests that
    set a different value must reload the modules that read from env at
    import time.
    """
    import sernia_mcp.config as _config

    importlib.reload(_config)
    import sernia_mcp.bearer as _bearer

    importlib.reload(_bearer)
    return _bearer


def test_unconfigured_returns_none(monkeypatch):
    """No bearer env var → verify_internal_bearer returns None for any token."""
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", "")
    bearer = _reload_bearer()

    assert bearer.internal_bearer_configured() is False
    assert bearer.verify_internal_bearer(_VALID_TOKEN) is None
    assert bearer.verify_internal_bearer("") is None


def test_matching_token_returns_synthesized_access_token(monkeypatch):
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()

    assert bearer.internal_bearer_configured() is True

    token = bearer.verify_internal_bearer(_VALID_TOKEN)
    assert token is not None
    assert token.client_id == "service:sernia-ai"
    assert token.scopes == []
    assert token.expires_at is None
    assert token.claims["sub"] == "service:sernia-ai"
    assert token.claims["auth_method"] == "internal_bearer"
    # Email's domain MUST be in the allowlist (otherwise the bearer path
    # would be rejected by require_allowed_email_domain at the next layer).
    assert token.claims["email"].endswith("@serniacapital.com")


def test_mismatched_token_returns_none(monkeypatch):
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()

    assert bearer.verify_internal_bearer(_OTHER_TOKEN) is None
    # Empty string is not a match either.
    assert bearer.verify_internal_bearer("") is None
    # Prefix collision: must not match.
    assert bearer.verify_internal_bearer(_VALID_TOKEN[:-1]) is None


def test_short_token_refused_at_boot(monkeypatch):
    """Tokens under 32 chars are weak — fail fast rather than silently accepting."""
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", "short")
    bearer = _reload_bearer()

    assert bearer.internal_bearer_configured() is False
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        bearer.verify_internal_bearer("short")


def test_synthesized_token_passes_email_domain_check(monkeypatch):
    """End-to-end: bearer-synthesized token must pass require_allowed_email_domain.

    This is the load-bearing assertion of the whole design — without it the
    bearer path would be rejected by the second-layer authorization check.
    """
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()
    import sernia_mcp.auth as _auth

    importlib.reload(_auth)

    from types import SimpleNamespace

    token = bearer.verify_internal_bearer(_VALID_TOKEN)
    assert token is not None
    ctx = SimpleNamespace(token=token)

    assert _auth.require_allowed_email_domain(ctx) is True


def test_synthesized_email_uses_first_allowed_domain(monkeypatch):
    """Operators with a custom allowlist should still get a passing bearer.

    Only the FIRST entry is used — that's the deterministic choice; tests
    pin it so the order isn't accidentally changed.
    """
    monkeypatch.setenv(
        "SERNIA_MCP_ALLOWED_EMAIL_DOMAINS", "partner.example,serniacapital.com"
    )
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()

    token = bearer.verify_internal_bearer(_VALID_TOKEN)
    assert token is not None
    assert token.claims["email"] == "agent@partner.example"


@pytest.mark.asyncio
async def test_bearer_only_provider_verify_token(monkeypatch):
    """``BearerOnlyProvider`` is the standalone provider used when Clerk is
    not configured but bearer is. ``verify_token`` must accept the secret
    and reject everything else.
    """
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()

    provider = bearer.BearerOnlyProvider()
    accepted = await provider.verify_token(_VALID_TOKEN)
    assert accepted is not None
    assert accepted.client_id == "service:sernia-ai"

    rejected = await provider.verify_token(_OTHER_TOKEN)
    assert rejected is None


@pytest.mark.asyncio
async def test_bearer_token_mixin_intercepts_before_super(monkeypatch):
    """The mixin's ``verify_token`` must:
      1. Return the synthesized token when the bearer matches (skipping super).
      2. Delegate to super when the bearer doesn't match.

    This ordering is what makes the bearer path "secondary": OAuth tokens
    are still validated through the wrapped provider, but the cheap bearer
    check runs first.
    """
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", _VALID_TOKEN)
    bearer = _reload_bearer()

    super_calls: list[str] = []

    class _FakeBase:
        async def verify_token(self, token: str):
            super_calls.append(token)
            return None

    class _Combined(bearer.BearerTokenMixin, _FakeBase):
        pass

    combined = _Combined()

    # Bearer match: super must NOT be called.
    matched = await combined.verify_token(_VALID_TOKEN)
    assert matched is not None
    assert matched.claims["auth_method"] == "internal_bearer"
    assert super_calls == []

    # Bearer miss: super IS called, with the unaltered token.
    missed = await combined.verify_token(_OTHER_TOKEN)
    assert missed is None
    assert super_calls == [_OTHER_TOKEN]
