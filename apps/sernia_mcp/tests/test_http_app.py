"""Boot the ASGI ``http_app`` in-process and assert basic endpoints respond.

Uses httpx's ASGITransport so we don't need a running uvicorn — the test
runs the full FastMCP HTTP request/response pipeline against the in-process
app, which is the same pipeline production traffic hits.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_mcp_endpoint_no_redirect_on_canonical_path():
    """``POST /mcp`` must respond directly (not 307) — Claude posts there.

    A 307 here would cause the connector to fail post-auth: many HTTP clients
    drop the Authorization header when following redirects, so the redirected
    request would arrive unauthenticated and the bearer challenge would loop.
    """
    from sernia_mcp.app import app as application

    transport = httpx.ASGITransport(app=application)

    async with application.lifespan(application):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/mcp", content=b"{}")
            assert resp.status_code != 307, (
                f"POST /mcp should not redirect, got 307 → {resp.headers.get('location')}"
            )
            assert resp.status_code != 404, "POST /mcp should be mounted, got 404"


@pytest.mark.asyncio
async def test_icon_endpoint_serves_png():
    """``/icon.png`` must serve the PNG referenced by the MCP server icons field."""
    from sernia_mcp.app import app as application

    transport = httpx.ASGITransport(app=application)

    async with application.lifespan(application):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/icon.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_health_endpoint_returns_200():
    """Railway healthcheck hits this — must respond 200 OK on GET."""
    from sernia_mcp.app import app as application

    transport = httpx.ASGITransport(app=application)

    async with application.lifespan(application):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.status_code == 200, f"/health returned {resp.status_code}"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sernia-mcp"
    assert "version" in body
    assert body["auth"] in ("clerk-oauth", "unauthenticated")
