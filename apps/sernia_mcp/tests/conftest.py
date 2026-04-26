"""Shared pytest fixtures + asyncio defaults.

Tests run unauthenticated and isolated from the host environment — we strip
any inherited Clerk OAuth vars and any inherited workspace path so the suite
is deterministic regardless of the developer's local ``.env``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_CLERK_VARS = (
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
)


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch):
    """Per-test isolation: temp workspace + cleared Clerk OAuth vars.

    We set the Clerk vars to empty strings rather than deleting them so that
    ``load_dotenv(..., override=False)`` (which runs again on the config
    reload below) cannot reload values from a developer's local ``.env``.
    ``clerk_oauth_configured()`` treats empty strings as not-configured.
    """
    for var in _CLERK_VARS:
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", "")
    monkeypatch.setenv("SERNIA_MCP_WORKSPACE_PATH", str(tmp_path))

    import importlib

    import sernia_mcp.config as _config

    importlib.reload(_config)
    # Modules that captured ``WORKSPACE_PATH`` at import time also need a reload.
    import sernia_mcp.core.skills as _skills

    importlib.reload(_skills)
    yield
    os.environ.pop("SERNIA_MCP_WORKSPACE_PATH", None)
