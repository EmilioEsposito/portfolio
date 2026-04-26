"""Unit tests for ``list_active_threads_core``.

We mock the OpenPhone HTTP client (and ``get_all_contacts``) so the test
exercises the filtering/sorting/enrichment logic without hitting the API.
The actual Quo API integration is covered by the live-marker tests in the
parent monorepo (``api/src/tests/test_quo_tools.py``); duplicating those
here would just couple the MCP service tightly to upstream Quo behavior.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_filters_done_threads_and_sorts_by_recency():
    """Done threads (snoozed past 2100) are filtered; remainder sorted desc."""
    from sernia_mcp.core.quo.contacts import list_active_threads_core

    # Mock the conversations API
    fake_conversations = {
        "data": [
            {
                "id": "conv-old",
                "participants": ["+15550000001"],
                "lastActivityAt": "2026-01-01T00:00:00Z",
                "snoozedUntil": None,
            },
            {
                "id": "conv-done",
                "participants": ["+15550000002"],
                "lastActivityAt": "2026-04-25T00:00:00Z",
                "snoozedUntil": "2125-01-01T00:00:00Z",  # done
            },
            {
                "id": "conv-recent",
                "participants": ["+15550000003"],
                "lastActivityAt": "2026-04-26T00:00:00Z",
                "snoozedUntil": None,
            },
        ],
        "nextPageToken": None,
    }

    fake_messages = {"data": [{"direction": "incoming", "text": "hello"}]}

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url == "/v1/conversations":
            resp.json = lambda: fake_conversations
        elif url == "/v1/messages":
            resp.json = lambda: fake_messages
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with (
        patch(
            "sernia_mcp.core.quo.contacts.build_quo_client",
            return_value=fake_client,
        ),
        patch(
            "sernia_mcp.core.quo.contacts.get_all_contacts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await list_active_threads_core(max_results=20)

    # The done thread is filtered out; remaining two appear in recency order
    assert "conv-done" not in result
    assert "conv-recent" in result
    assert "conv-old" in result
    # Most-recent appears before older one
    assert result.index("conv-recent") < result.index("conv-old")


@pytest.mark.asyncio
async def test_empty_returns_friendly_message():
    from sernia_mcp.core.quo.contacts import list_active_threads_core

    fake_client = AsyncMock()

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"data": [], "nextPageToken": None}
        return resp

    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with patch(
        "sernia_mcp.core.quo.contacts.build_quo_client",
        return_value=fake_client,
    ):
        result = await list_active_threads_core()

    assert "No active conversation threads found" in result


@pytest.mark.asyncio
async def test_http_error_raises_external_service_error():
    import httpx

    from sernia_mcp.core.errors import ExternalServiceError
    from sernia_mcp.core.quo.contacts import list_active_threads_core

    fake_client = AsyncMock()

    async def fake_get(url, params=None):
        raise httpx.HTTPError("network down")

    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with patch(
        "sernia_mcp.core.quo.contacts.build_quo_client",
        return_value=fake_client,
    ):
        with pytest.raises(ExternalServiceError, match="Quo API error"):
            await list_active_threads_core()


def test_is_done_conversation_logic():
    """Private helper but worth pinning — Quo's 'done' marker semantics."""
    from sernia_mcp.core.quo.contacts import _is_done_conversation

    assert _is_done_conversation({"snoozedUntil": "2125-01-01T00:00:00Z"}) is True
    assert _is_done_conversation({"snoozedUntil": "2026-12-31T23:59:59Z"}) is False
    assert _is_done_conversation({"snoozedUntil": None}) is False
    assert _is_done_conversation({}) is False
    # Defensive: malformed input shouldn't crash
    assert _is_done_conversation({"snoozedUntil": ""}) is False


@pytest.mark.asyncio
async def test_tool_exposes_via_mcp_client():
    """Smoke-check: the @mcp.tool wrapper is registered + reachable."""
    from fastmcp import Client

    from sernia_mcp.server import mcp

    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert "quo_list_active_sms_threads" in names
