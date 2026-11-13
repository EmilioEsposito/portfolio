"""Shared pytest fixtures + asyncio defaults.

Tests run unauthenticated and isolated from the host environment — we strip
any inherited Clerk OAuth vars and any inherited workspace path so the suite
is deterministic regardless of the developer's local ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

_CLERK_VARS = (
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
)


def _set_test_environment(monkeypatch, workspace: Path) -> None:
    """Set explicit local mode before imports, without loading service secrets."""
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in tuple(os.environ):
        if key.startswith("RAILWAY_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SERNIA_MCP_DISABLE_AUTH", "true")
    monkeypatch.setenv("SERNIA_MCP_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", "")
    monkeypatch.setenv("LOGFIRE_TOKEN", "")
    for var in _CLERK_VARS:
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("SERNIA_MCP_INTERNAL_BEARER_TOKEN", "")
    monkeypatch.setenv("SERNIA_MCP_WORKSPACE_PATH", str(workspace))


def pytest_configure(config):
    # Approval tests import the server during collection, before fixtures run.
    # Establish the same isolated mode there so a clean CI runner needs no .env.
    workspace = TemporaryDirectory(prefix="sernia-mcp-tests-")
    patch = pytest.MonkeyPatch()
    config.add_cleanup(workspace.cleanup)
    config.add_cleanup(patch.undo)
    _set_test_environment(patch, Path(workspace.name))


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch):
    """Reset config and workspace for each test; fresh-process tests opt out explicitly."""
    _set_test_environment(monkeypatch, tmp_path)

    import importlib

    import sernia_mcp.config as _config

    importlib.reload(_config)
    # Modules that captured ``WORKSPACE_PATH`` at import time also need a reload.
    import sernia_mcp.core.skills as _skills

    importlib.reload(_skills)
    yield
    os.environ.pop("SERNIA_MCP_WORKSPACE_PATH", None)
