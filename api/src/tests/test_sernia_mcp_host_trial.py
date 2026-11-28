"""TRIAL, not for merge: one hostname serves the MCP app, the rest serve the API.

A stand-in MCP app is used, so these run without Clerk, FastMCP or a network.
"""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from api.src.sernia_mcp_host.trial import McpHostDispatch, with_mcp_lifespan

MCP_HOST = "mcp.example.test"
API_HOST = "api.example.test"


def _apps() -> tuple[FastAPI, list[str]]:
    events: list[str] = []

    @asynccontextmanager
    async def mcp_lifespan(_app):
        events.append("mcp up")
        yield
        events.append("mcp down")

    async def mcp_endpoint(_request):
        return PlainTextResponse("mcp")

    mcp_app = Starlette(
        routes=[
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/.well-known/oauth-protected-resource/mcp", mcp_endpoint),
            Route("/health", mcp_endpoint),
        ],
        lifespan=mcp_lifespan,
    )

    @asynccontextmanager
    async def api_lifespan(_app):
        events.append("api up")
        yield
        events.append("api down")

    api = FastAPI(lifespan=with_mcp_lifespan(api_lifespan, mcp_app))

    @api.get("/api/health")
    async def health():
        return {"status": "healthy"}

    api.add_middleware(McpHostDispatch, mcp_app=mcp_app, hostname=MCP_HOST)
    return api, events


@pytest.mark.parametrize("host", [MCP_HOST, f"{MCP_HOST}:8443", MCP_HOST.upper(), f"{MCP_HOST}."])
def test_the_mcp_hostname_serves_the_mcp_paths_at_its_root(host):
    api, _ = _apps()
    with TestClient(api) as client:
        headers = {"host": host}
        # No redirect on /mcp: a redirect can drop the Authorization header.
        posted = client.post("/mcp", headers=headers, follow_redirects=False)
        assert (posted.status_code, posted.text) == (200, "mcp")
        assert (
            client.get("/.well-known/oauth-protected-resource/mcp", headers=headers).status_code
            == 200
        )
        assert client.get("/health", headers=headers).status_code == 200


@pytest.mark.parametrize("path", ["/api/health", "/api/docs", "/api/openapi.json", "/docs", "/"])
def test_the_mcp_hostname_never_reaches_the_api(path):
    api, _ = _apps()
    with TestClient(api) as client:
        assert client.get(path, headers={"host": MCP_HOST}).status_code == 404


@pytest.mark.parametrize(
    "host", [API_HOST, "localhost:8000", f"evil-{MCP_HOST}", f"{MCP_HOST}.evil"]
)
def test_other_hostnames_never_serve_the_mcp_paths(host):
    api, _ = _apps()
    with TestClient(api) as client:
        headers = {"host": host}
        assert client.get("/api/health", headers=headers).status_code == 200
        assert client.post("/mcp", headers=headers).status_code == 404
        assert (
            client.get("/.well-known/oauth-protected-resource/mcp", headers=headers).status_code
            == 404
        )
        assert client.get("/health", headers=headers).status_code == 404


def test_a_forwarded_host_header_does_not_choose_the_app():
    api, _ = _apps()
    with TestClient(api) as client:
        forwarded = {"host": API_HOST, "x-forwarded-host": MCP_HOST}
        assert client.post("/mcp", headers=forwarded).status_code == 404
        assert client.get("/api/health", headers=forwarded).status_code == 200


def test_both_lifespans_run_and_the_mcp_one_is_inside_the_api_one():
    api, events = _apps()
    with TestClient(api):
        assert events == ["api up", "mcp up"]
    assert events == ["api up", "mcp up", "mcp down", "api down"]


@pytest.mark.asyncio
async def test_two_different_host_headers_are_refused():
    api, _ = _apps()
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "headers": [(b"host", MCP_HOST.encode()), (b"host", API_HOST.encode())],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    await api(scope, receive, send)
    assert sent[0]["status"] == 400
