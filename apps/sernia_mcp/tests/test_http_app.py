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
    """The /mcp/ endpoint should respond (not 404) — even unauthenticated.

    Without OAuth configured we expect a 200 or 4xx (e.g. method-not-allowed
    on a bare GET); we just want to confirm the route is mounted and the
    ASGI lifespan started cleanly.
    """
    from sernia_mcp.server import mcp

    app = mcp.http_app(stateless_http=True)
    transport = httpx.ASGITransport(app=app)

    async with app.lifespan(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/mcp/")
            assert resp.status_code != 404, (
                f"/mcp/ should be mounted, got 404. Headers: {dict(resp.headers)}"
            )
