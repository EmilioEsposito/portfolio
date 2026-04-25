"""ASGI application entrypoint.

Run via uvicorn from Railway / local dev::

    uv run uvicorn sernia_mcp.app:app --host 0.0.0.0 --port 8080

We bypass ``fastmcp run`` because that command builds the Starlette ASGI app
internally and gives us no hook to add inbound-request middleware. Without
inbound-request logging, auth-flow failures are invisible (only the upstream
Clerk httpx calls show in Logfire).

This module exposes a single ``app`` callable that:

  1. Wraps the FastMCP HTTP app with a request-logging middleware so every
     inbound request surfaces in Logfire with method, path, status, latency.
  2. Logs unhandled exceptions explicitly (Starlette swallows some).
"""
from __future__ import annotations

import time

import logfire
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from sernia_mcp.server import mcp


class _RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        with logfire.span(
            "{method} {path}",
            method=request.method,
            path=request.url.path,
        ) as span:
            try:
                response = await call_next(request)
            except Exception:
                logfire.exception(
                    "unhandled exception in request",
                    method=request.method,
                    path=request.url.path,
                )
                raise
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("duration_ms", int((time.monotonic() - start) * 1000))
            return response


app = mcp.http_app(
    path="/mcp/",
    stateless_http=True,
    transport="http",
    middleware=[Middleware(_RequestLogMiddleware)],
)
