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

    fake_messages = {"data": [{"direction": "incoming", "text": "hello", "createdAt": "2026-04-26T00:00:00Z"}]}
    fake_calls = {"data": []}

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url == "/v1/conversations":
            resp.json = lambda: fake_conversations
        elif url == "/v1/messages":
            resp.json = lambda: fake_messages
        elif url == "/v1/calls":
            resp.json = lambda: fake_calls
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


@pytest.mark.asyncio
async def test_list_active_threads_surfaces_call_id_when_call_is_latest():
    """When a thread's most recent activity is a call (not an SMS), the
    snippet line must include the Call ID so the caller can chain to a
    transcript fetch."""
    from sernia_mcp.core.quo.contacts import list_active_threads_core

    fake_conversations = {
        "data": [
            {
                "id": "conv-with-call",
                "participants": ["+15550001234"],
                "lastActivityAt": "2026-04-30T00:00:00Z",
                "snoozedUntil": None,
            },
        ],
        "nextPageToken": None,
    }
    # SMS is older than the call → call wins as the snippet.
    fake_messages = {
        "data": [{"direction": "outgoing", "text": "old text", "createdAt": "2026-04-28T00:00:00Z"}]
    }
    fake_calls = {
        "data": [{
            "id": "ACcall123",
            "direction": "incoming",
            "duration": 42,
            "status": "completed",
            "createdAt": "2026-04-30T00:00:00Z",
        }]
    }

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url == "/v1/conversations":
            resp.json = lambda: fake_conversations
        elif url == "/v1/messages":
            resp.json = lambda: fake_messages
        elif url == "/v1/calls":
            resp.json = lambda: fake_calls
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with (
        patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client),
        patch(
            "sernia_mcp.core.quo.contacts.get_all_contacts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await list_active_threads_core(max_results=20)

    assert "Call (incoming, 42s)" in result, result
    assert "Call ID ACcall123" in result, result


@pytest.mark.asyncio
async def test_get_thread_messages_interleaves_calls_with_id():
    """get_thread_messages_core must return SMS + calls interleaved
    chronologically, with Call ID surfaced for each call."""
    from sernia_mcp.core.quo.contacts import get_thread_messages_core

    fake_messages = {
        "data": [
            {
                "direction": "outgoing",
                "text": "Reply text",
                "createdAt": "2026-05-03T15:00:00Z",
                "from": "+19990001111",
            },
        ]
    }
    fake_calls = {
        "data": [
            {
                "id": "ACcallabc",
                "direction": "incoming",
                "duration": 72,
                "status": "completed",
                "createdAt": "2026-05-02T10:00:00Z",
            },
        ]
    }

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url == "/v1/messages":
            resp.json = lambda: fake_messages
        elif url == "/v1/calls":
            resp.json = lambda: fake_calls
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with (
        patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client),
        patch(
            "sernia_mcp.core.quo.contacts.get_all_contacts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await get_thread_messages_core("+15550009999", max_results=10)

    assert "1 message, 1 call" in result, result
    assert "CALL" in result, result
    assert "Call ID ACcallabc" in result, result
    # Call (May 2) should appear before SMS (May 3) — chronological order.
    assert result.index("CALL") < result.index("Reply text"), result


@pytest.mark.asyncio
async def test_get_call_details_renders_summary_and_transcript():
    """get_call_details_core should render a markdown blob with both
    ## Summary and ## Transcript sections, and surface the Call ID."""
    from sernia_mcp.core.quo.contacts import get_call_details_core

    fake_call = {
        "data": {
            "id": "ACcall1",
            "direction": "incoming",
            "duration": 72,
            "status": "completed",
            "createdAt": "2026-05-02T17:58:25Z",
            "participants": ["+15551112222", "+15553334444"],
        }
    }
    fake_summary = {
        "data": {
            "summary": ["Caller asked about a tour.", "Tour scheduled at 4pm."],
            "nextSteps": ["Show up at 4pm."],
        }
    }
    fake_transcript = {
        "data": {
            "dialogue": [
                {"start": 0.5, "content": "Hello?", "identifier": "+15553334444", "userId": "U1"},
                {"start": 2.0, "content": "Hi, calling about the tour.", "identifier": "+15551112222", "userId": None},
            ]
        }
    }

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url.startswith("/v1/calls/"):
            resp.json = lambda: fake_call
        elif url.startswith("/v1/call-summaries/"):
            resp.json = lambda: fake_summary
        elif url.startswith("/v1/call-transcripts/"):
            resp.json = lambda: fake_transcript
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with (
        patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client),
        patch(
            "sernia_mcp.core.quo.contacts.get_all_contacts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await get_call_details_core("ACcall1")

    assert "# Call ACcall1" in result
    assert "## Summary" in result
    assert "Tour scheduled at 4pm." in result
    assert "### Next Steps" in result
    assert "## Transcript" in result
    assert "Hi, calling about the tour." in result
    # Team tag for the speaker with a userId
    assert "(team)" in result


@pytest.mark.asyncio
async def test_get_call_details_truncation_marker():
    """When transcript exceeds the limit, the truncation marker must appear."""
    from sernia_mcp.core.quo.contacts import get_call_details_core

    long_dialogue = [
        {"start": float(i), "content": "This is a long line of dialogue " * 5,
         "identifier": "+15551112222", "userId": None}
        for i in range(50)
    ]
    fake_call = {"data": {"id": "ACcall2", "direction": "incoming"}}
    fake_summary = {"data": {"summary": ["s"], "nextSteps": []}}
    fake_transcript = {"data": {"dialogue": long_dialogue}}

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        if url.startswith("/v1/calls/"):
            resp.json = lambda: fake_call
        elif url.startswith("/v1/call-summaries/"):
            resp.json = lambda: fake_summary
        elif url.startswith("/v1/call-transcripts/"):
            resp.json = lambda: fake_transcript
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with (
        patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client),
        patch(
            "sernia_mcp.core.quo.contacts.get_all_contacts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await get_call_details_core("ACcall2", transcript_max_chars=200)

    assert "transcript truncated at 200 chars" in result
    assert "transcript_max_chars" in result


@pytest.mark.asyncio
async def test_get_call_details_unknown_returns_friendly():
    """All three upstream fetches failing → friendly not-found string."""
    import httpx as _httpx

    from sernia_mcp.core.quo.contacts import get_call_details_core

    async def fake_get(url, params=None):
        raise _httpx.HTTPError("404")

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client):
        result = await get_call_details_core("ACdoesnotexist")

    assert "No call found" in result


@pytest.mark.asyncio
async def test_get_thread_messages_empty_returns_friendly():
    """No messages and no calls → friendly empty message."""
    from sernia_mcp.core.quo.contacts import get_thread_messages_core

    async def fake_get(url, params=None):
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"data": []}
        return resp

    fake_client = AsyncMock()
    fake_client.get = fake_get
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None

    with patch("sernia_mcp.core.quo.contacts.build_quo_client", return_value=fake_client):
        result = await get_thread_messages_core("+19999999999")

    assert "No messages or calls found" in result


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
