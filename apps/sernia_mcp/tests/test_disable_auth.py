"""Tests for the ``SERNIA_MCP_DISABLE_AUTH`` local-dev kill switch.

The conftest's ``_isolate_environment`` fixture clears Clerk env vars and
sets ``SERNIA_MCP_WORKSPACE_PATH`` per-test. We layer additional env tweaks
on top via ``monkeypatch`` and reload ``sernia_mcp.config`` + the
``_disable_auth_requested`` callable to pick them up.
"""
from __future__ import annotations

import importlib

import pytest


def _reload_modules():
    """Re-import config + server so they pick up the current env."""
    import sernia_mcp.config as _config

    importlib.reload(_config)
    # Don't reload server.py — it constructs the FastMCP instance and would
    # break other tests sharing the same module. We import the helper fresh.
    return _config


def test_flag_unset_means_no_bypass(monkeypatch):
    monkeypatch.delenv("SERNIA_MCP_DISABLE_AUTH", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    cfg = _reload_modules()
    assert cfg.SERNIA_MCP_DISABLE_AUTH is False


def test_flag_truthy_values_recognized(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    for truthy in ("true", "True", "TRUE", "1", "yes", "  true  "):
        monkeypatch.setenv("SERNIA_MCP_DISABLE_AUTH", truthy)
        cfg = _reload_modules()
        assert cfg.SERNIA_MCP_DISABLE_AUTH is True, f"failed for {truthy!r}"


def test_flag_falsy_values_recognized(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    for falsy in ("", "0", "false", "no", "anything-else"):
        monkeypatch.setenv("SERNIA_MCP_DISABLE_AUTH", falsy)
        cfg = _reload_modules()
        assert cfg.SERNIA_MCP_DISABLE_AUTH is False, f"failed for {falsy!r}"


def test_disable_auth_requested_returns_true_locally(monkeypatch):
    monkeypatch.setenv("SERNIA_MCP_DISABLE_AUTH", "true")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    _reload_modules()

    # Server module captured the OLD value of SERNIA_MCP_DISABLE_AUTH at import.
    # Patch the module attribute directly to simulate fresh import.
    import sernia_mcp.server as _server
    from sernia_mcp.server import _disable_auth_requested

    monkeypatch.setattr(_server, "SERNIA_MCP_DISABLE_AUTH", True)
    assert _disable_auth_requested() is True


def test_disable_auth_requested_returns_false_when_flag_unset(monkeypatch):
    import sernia_mcp.server as _server

    monkeypatch.setattr(_server, "SERNIA_MCP_DISABLE_AUTH", False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    assert _server._disable_auth_requested() is False


def test_disable_auth_requested_raises_in_hosted_env(monkeypatch):
    """The kill switch must fail loudly if accidentally set on Railway."""
    import sernia_mcp.server as _server

    monkeypatch.setattr(_server, "SERNIA_MCP_DISABLE_AUTH", True)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    with pytest.raises(RuntimeError, match="must not be set in a hosted env"):
        _server._disable_auth_requested()


def test_disable_auth_requested_raises_in_dev_env_too(monkeypatch):
    """Any RAILWAY_ENVIRONMENT_NAME counts as hosted — including PR previews."""
    import sernia_mcp.server as _server

    monkeypatch.setattr(_server, "SERNIA_MCP_DISABLE_AUTH", True)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "development")

    with pytest.raises(RuntimeError, match="must not be set in a hosted env"):
        _server._disable_auth_requested()
