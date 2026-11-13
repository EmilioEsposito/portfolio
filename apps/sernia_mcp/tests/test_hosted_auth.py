"""Fresh-process startup and real ASGI requests, without host/provider credentials."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CLERK = {
    "FASTMCP_SERVER_AUTH_CLERK_DOMAIN": "test.clerk.accounts.dev",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID": "test-client",
    "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET": "test-secret",
    "SERNIA_MCP_BASE_URL": "https://mcp.example.test",
}
BEARER = {"SERNIA_MCP_INTERNAL_BEARER_TOKEN": "synthetic-test-token-" * 3}


def run_app(tmp_path: Path, settings: dict[str, str], code: str):
    """Allowlist the process environment, disable dotenv, and use disposable storage."""
    (tmp_path / "MEMORY.md").write_text("SYNTHETIC_AUTH_REGRESSION_MARKER")
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        PYTHON_DOTENV_DISABLED="1",
        HOME=str(tmp_path),
        FASTMCP_HOME=str(tmp_path / "fastmcp"),
        SERNIA_MCP_WORKSPACE_PATH=str(tmp_path),
        LOGFIRE_SEND_TO_LOGFIRE="false",
    )
    env.update(settings)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "settings,error",
    [
        ({}, "Authentication is required"),
        ({"RAILWAY_ENVIRONMENT_NAME": "production"}, "Production requires Clerk OAuth only"),
        ({"RAILWAY_ENVIRONMENT_NAME": "development"}, "Authentication is required"),
        ({"RAILWAY_ENVIRONMENT_NAME": "portfolio-pr-123"}, "Authentication is required"),
        ({"RAILWAY_ENVIRONMENT_ID": "synthetic-env"}, "Authentication is required"),
        ({"SERNIA_MCP_INTERNAL_BEARER_TOKEN": "short"}, "at least 32 characters"),
        ({"SERNIA_MCP_INTERNAL_BEARER_TOKEN": "   "}, "Authentication is required"),
        (
            {**BEARER, "RAILWAY_ENVIRONMENT_NAME": "production"},
            "Production requires Clerk OAuth only",
        ),
        (
            {**CLERK, **BEARER, "RAILWAY_ENVIRONMENT_NAME": "production"},
            "Production requires Clerk OAuth only",
        ),
        ({"FASTMCP_SERVER_AUTH_CLERK_DOMAIN": "test.clerk.accounts.dev"}, "Incomplete Clerk OAuth"),
        (
            {
                "SERNIA_MCP_DISABLE_AUTH": "true",
                "SERNIA_MCP_BASE_URL": "https://public.example.test",
            },
            "requires a loopback",
        ),
    ],
)
def test_unsafe_startup_is_rejected(tmp_path, settings, error):
    result = run_app(tmp_path, settings, "from sernia_mcp.app import app")
    assert result.returncode != 0
    assert error in result.stderr


@pytest.mark.parametrize("missing", CLERK)
@pytest.mark.parametrize("bearer", [{}, BEARER], ids=["no-bearer", "with-bearer"])
def test_partial_clerk_cannot_downgrade(tmp_path, missing, bearer):
    settings = {**CLERK, **bearer, missing: "   ", "RAILWAY_ENVIRONMENT_NAME": "development"}
    result = run_app(tmp_path, settings, "from sernia_mcp.app import app")
    assert result.returncode != 0
    assert "Incomplete Clerk OAuth" in result.stderr
    assert missing in result.stderr


@pytest.mark.parametrize(
    "marker",
    [
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_SERVICE_ID",
        "RAILWAY_DEPLOYMENT_ID",
        "RAILWAY_REPLICA_ID",
        "RAILWAY_PUBLIC_DOMAIN",
        "RAILWAY_PRIVATE_DOMAIN",
    ],
)
def test_hosted_markers_forbid_bypass_even_when_empty(tmp_path, marker):
    result = run_app(
        tmp_path,
        {"SERNIA_MCP_DISABLE_AUTH": "true", marker: ""},
        "from sernia_mcp.app import app",
    )
    assert result.returncode != 0
    assert "must not be set in a hosted env" in result.stderr


HTTP_CHECK = """
import asyncio
import httpx
from sernia_mcp.app import app

async def check():
    async with app.lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200, health.text
            assert health.json()["auth"] == EXPECTED_LABEL, health.text
            requests = [
                {"method": "tools/list", "params": {}},
                {"method": "tools/call", "params": {"name": "read_resource", "arguments": {"uri": "memory://current"}}},
                {"method": "resources/read", "params": {"uri": "memory://current"}},
            ]
            for headers, status in CASES:
                for request in requests:
                    response = await client.post(
                        "/mcp", json={"jsonrpc": "2.0", "id": 1, **request},
                        headers={"Accept": "application/json, text/event-stream", **headers},
                    )
                    assert response.status_code == status, (status, response.status_code, response.text)
                    if status == 401:
                        assert "SYNTHETIC_AUTH_REGRESSION_MARKER" not in response.text
                        assert "read_resource" not in response.text
                        assert "www-authenticate" in response.headers
                    elif request["method"] != "tools/list":
                        assert "SYNTHETIC_AUTH_REGRESSION_MARKER" in response.text, response.text
                    else:
                        assert "read_resource" in response.text, response.text
asyncio.run(check())
"""


@pytest.mark.parametrize(
    "settings,label,cases",
    [
        ({**CLERK, "RAILWAY_ENVIRONMENT_NAME": "production"}, "clerk-oauth", [({}, 401)]),
        (
            {**BEARER, "RAILWAY_ENVIRONMENT_NAME": "development"},
            "bearer",
            [
                ({}, 401),
                ({"Authorization": "Bearer invalid"}, 401),
                ({"Authorization": "Bearer " + BEARER["SERNIA_MCP_INTERNAL_BEARER_TOKEN"]}, 200),
            ],
        ),
        ({"SERNIA_MCP_DISABLE_AUTH": "true"}, "unauthenticated", [({}, 200)]),
    ],
)
def test_real_http_auth_boundary(tmp_path, settings, label, cases):
    code = f"EXPECTED_LABEL = {label!r}\nCASES = {cases!r}\n" + HTTP_CHECK
    result = run_app(tmp_path, settings, code)
    assert result.returncode == 0, result.stdout + result.stderr


def test_health_fails_when_hosted_provider_is_unavailable(tmp_path):
    code = """
import asyncio
import httpx
from sernia_mcp import server
from sernia_mcp.app import app
# Simulate a future construction regression: flags still say Clerk is configured.
server.mcp.auth = None
async def check():
    async with app.lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 503, response.text
            assert response.json()["status"] == "error"
asyncio.run(check())
"""
    result = run_app(tmp_path, {**CLERK, "RAILWAY_ENVIRONMENT_NAME": "production"}, code)
    assert result.returncode == 0, result.stdout + result.stderr
