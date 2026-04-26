"""Tests for the git_sync wiring around resource writes and app startup.

We don't exercise the actual git operations here — those live in the
vendored ``clients/git_sync.py`` and are tested upstream. These tests pin
two integration points:

  1. ``write_resource`` and ``edit_resource`` both schedule a fire-and-forget
     ``commit_and_push`` task. Failed writes don't.
  2. ``ensure_repo`` is invoked during ASGI startup (via the lifespan wrap),
     and a failure there does NOT crash the server.

Both rely on the standard PAT-unset → no-op contract, so no real network
or filesystem mutation happens in tests beyond what other tests already do.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_write_resource_schedules_commit_and_push():
    """A successful ``write_resource`` call should kick off ``commit_and_push``."""
    from fastmcp import Client

    from sernia_mcp.server import mcp

    with patch(
        "sernia_mcp.tools.context.commit_and_push", new=AsyncMock()
    ) as push:
        async with Client(mcp) as c:
            await c.call_tool(
                "write_resource",
                {"uri": "memory://current", "content": "hello"},
            )
        # Give the fire-and-forget task one event-loop tick to run.
        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)

    push.assert_awaited()


@pytest.mark.asyncio
async def test_edit_resource_schedules_commit_and_push():
    """A successful string-substitution edit also kicks off commit_and_push."""
    from fastmcp import Client

    from sernia_mcp.core.skills import write_memory
    from sernia_mcp.server import mcp

    write_memory("hello world")

    with patch(
        "sernia_mcp.tools.context.commit_and_push", new=AsyncMock()
    ) as push:
        async with Client(mcp) as c:
            await c.call_tool(
                "edit_resource",
                {
                    "uri": "memory://current",
                    "old_string": "world",
                    "new_string": "earth",
                },
            )
        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)

    push.assert_awaited()


@pytest.mark.asyncio
async def test_write_resource_skips_git_sync_on_failure():
    """If ``write_resource`` fails (bad URI), no commit_and_push should fire."""
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from sernia_mcp.server import mcp

    with patch(
        "sernia_mcp.tools.context.commit_and_push", new=AsyncMock()
    ) as push:
        async with Client(mcp) as c:
            with pytest.raises(ToolError, match="unsupported URI"):
                await c.call_tool(
                    "write_resource",
                    {"uri": "weird://thing", "content": "x"},
                )

    push.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_invokes_ensure_repo():
    """Wired ``ensure_repo`` must run before FastMCP's lifespan yields."""
    from sernia_mcp.app import app as application

    with patch(
        "sernia_mcp.app.ensure_repo", new=AsyncMock()
    ) as ensure:
        async with application.lifespan(application):
            pass

    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_continues_when_ensure_repo_raises():
    """A git_sync failure must NOT crash the server — the lifespan wrap
    catches the exception, logs it, and proceeds to FastMCP's inner lifespan.
    """
    from sernia_mcp.app import app as application

    with patch(
        "sernia_mcp.app.ensure_repo",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        # If the wrap doesn't swallow the exception, this raises.
        async with application.lifespan(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Server should still respond to /health despite git_sync failing.
                resp = await client.get("/health")
                assert resp.status_code == 200
