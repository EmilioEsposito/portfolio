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
    """Per-test isolation: temp workspace + cleared Clerk OAuth vars."""
    for var in _CLERK_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SERNIA_MCP_WORKSPACE_PATH", str(tmp_path))

    import importlib

    import sernia_mcp.config as _config

    importlib.reload(_config)
    import sernia_mcp.core.workspace.files as _files

    importlib.reload(_files)
    yield
    os.environ.pop("SERNIA_MCP_WORKSPACE_PATH", None)
