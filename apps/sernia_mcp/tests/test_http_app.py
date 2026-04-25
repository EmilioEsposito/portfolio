"""Boot the ASGI ``http_app`` in-process and assert basic endpoints respond.

Uses httpx's ASGITransport so we don't need a running uvicorn — the test
runs the full FastMCP HTTP request/response pipeline against the in-process
app, which is the same pipeline production traffic hits.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_http_app_mcp_endpoint_responds():
    """The /mcp/ endpoint must be mounted (POST/DELETE only — GET returns 405)."""
    from sernia_mcp.server import mcp

    app = mcp.http_app(stateless_http=True)
    transport = httpx.ASGITransport(app=app)

    async with app.lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/mcp/")
            assert resp.status_code != 404, (
                f"/mcp/ should be mounted, got 404. Headers: {dict(resp.headers)}"
            )


@pytest.mark.asyncio
async def test_health_endpoint_returns_200():
    """Railway healthcheck hits this — must respond 200 OK on GET."""
    from sernia_mcp.server import mcp

    app = mcp.http_app(stateless_http=True)
    transport = httpx.ASGITransport(app=app)

    async with app.lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.status_code == 200, f"/health returned {resp.status_code}"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sernia-mcp"
    assert "version" in body
    assert body["auth"] in ("clerk-oauth", "unauthenticated")
