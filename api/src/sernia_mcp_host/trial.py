"""TRIAL, not for merge: serve the Sernia MCP server from the API process.

The question under test is whether the MCP server in ``apps/sernia_mcp`` can
run inside this process and be reached on a hostname of its own, so that its
tools could call the API's service functions directly.

How it is wired:

* ``SERNIA_MCP_HOSTNAME`` names the one hostname that serves MCP, for example
  ``mcp.localhost``. Unset means nothing below runs and the API is unchanged.
* ``McpHostDispatch`` is the outermost middleware. A request whose ``Host`` is
  that hostname goes to the MCP app and never reaches a FastAPI route. Every
  other request goes to FastAPI, which has none of the MCP paths.
* The API's lifespan enters the MCP app's lifespan (``with_mcp_lifespan``),
  which is what starts FastMCP's session manager.

For the trial the MCP package is imported from ``apps/sernia_mcp/src`` as it
is. A real move would bring the package under ``api/src`` and drop the parts
that only a separate process needs (see ``_import_mcp_app``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import logfire
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_MCP_SOURCE = Path(__file__).resolve().parents[3] / "apps" / "sernia_mcp" / "src"


def configured_hostname() -> str | None:
    """The hostname that serves MCP, lower case and without a port, or None."""
    value = os.environ.get("SERNIA_MCP_HOSTNAME", "").strip().lower().rstrip(".")
    return value or None


def _hostname(value: str) -> str:
    """A ``Host`` header value without its port, lower case."""
    host = value.strip().lower()
    if host.startswith("["):  # IPv6 literal, "[::1]:8000"
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0].rstrip(".")


class McpHostDispatch:
    """Send requests for the MCP hostname to the MCP app, the rest to the API.

    The choice is made on the ``Host`` header alone. ``X-Forwarded-Host`` is
    ignored on purpose: the web proxy forwards a browser's own copy of it.
    """

    def __init__(self, app: ASGIApp, *, mcp_app: ASGIApp, hostname: str) -> None:
        self.app = app
        self.mcp_app = mcp_app
        self.hostname = hostname

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan: FastAPI owns it
            return
        hosts = {
            _hostname(value.decode("latin-1"))
            for name, value in scope.get("headers", ())
            if name == b"host"
        }
        if len(hosts) > 1:
            # Two different Host headers: refuse rather than guess which app.
            if scope["type"] == "http":
                await PlainTextResponse("Bad Request", status_code=400)(scope, receive, send)
            return
        if hosts == {self.hostname}:
            await self.mcp_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _import_mcp_app() -> Any:
    """Import ``sernia_mcp.app:app`` from the standalone service's source.

    ``sernia_mcp.server`` configures Logfire for a process of its own at
    import. Here that would replace the API's configuration, so those two
    calls are made no-ops for the import. A real move deletes them, along
    with the service's own request-log middleware and ``.env`` loading.
    """
    if str(_MCP_SOURCE) not in sys.path:
        sys.path.insert(0, str(_MCP_SOURCE))
    configure, instrument_httpx = logfire.configure, logfire.instrument_httpx
    logfire.configure = lambda *args, **kwargs: None  # type: ignore[assignment]
    logfire.instrument_httpx = lambda *args, **kwargs: None  # type: ignore[assignment]
    try:
        from sernia_mcp.app import app as mcp_app
    finally:
        logfire.configure = configure  # type: ignore[assignment]
        logfire.instrument_httpx = instrument_httpx  # type: ignore[assignment]
    return mcp_app


def _register_direct_database_tool() -> None:
    """One tool that reads the API's database with no HTTP hop.

    This is the point of the idea: the service's ``rental_listings_read``
    calls ``/api/rental-listings`` over HTTP with the user's token, because
    the separate service has no database. In process, a tool calls the
    service function the routes and the agent already call.
    """
    from sernia_mcp.server import mcp
    from sqlalchemy import text

    from api.src.database.database import AsyncSessionFactory
    from api.src.rental_listings import service

    @mcp.tool(annotations={"readOnlyHint": True})
    async def trial_rental_listings_direct() -> dict[str, Any]:
        """TRIAL: the units with a rental listing on file, read from the API's database."""
        async with AsyncSessionFactory() as session:
            listings = await service.read_listings(session)
            rules = await service.read_showing_rules(session)
            database = (await session.execute(text("select current_database()"))).scalar_one()
        return {
            "database": database,
            "count": len(listings),
            "units": [listing.unit for listing in listings],
            "showing_rules": rules.model_dump(mode="json"),
        }


def load_mcp_app() -> tuple[Any, str] | None:
    """The MCP ASGI app and its hostname, or None when the trial is off."""
    hostname = configured_hostname()
    if hostname is None:
        return None
    mcp_app = _import_mcp_app()
    _register_direct_database_tool()
    logfire.info("TRIAL: Sernia MCP served in process on host {hostname}", hostname=hostname)
    return mcp_app, hostname


Lifespan = Callable[[Any], AbstractAsyncContextManager[None]]


def with_mcp_lifespan(lifespan: Lifespan, mcp_app: Any | None) -> Lifespan:
    """Run the MCP app's lifespan inside the API's own.

    FastMCP's streamable HTTP transport needs its session manager started,
    and a lifespan nested in a mounted app is never run by the server.
    """
    if mcp_app is None:
        return lifespan

    @asynccontextmanager
    async def combined(app: Any) -> AsyncIterator[None]:
        async with lifespan(app):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield

    return combined
