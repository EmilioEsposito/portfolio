"""Current Quo group API contract; no live HTTP or SMS."""

from unittest.mock import AsyncMock

import httpx
import pytest

from api.src.sernia_ai.tools import quo_tools as q

A, B = "+14125550101", "+14125550102"
CONV = {"id": "CNgroup", "phoneNumberId": q.QUO_SHARED_EXTERNAL_PHONE_ID, "participants": [A, B]}


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_thread", [False, True])
async def test_group_filter_and_conversation_validation(wrong_thread):
    def handler(req):
        assert req.url.params.get_list("participants") == [A, B]
        assert "participants[]" not in req.url.params
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "AC2",
                        "conversationId": "CNprivate" if wrong_thread else "CNgroup",
                        "createdAt": "2026-09-10T12:01:00Z",
                    },
                    {"id": "AC1", "conversationId": "CNgroup", "createdAt": "2026-09-10T12:00:00Z"},
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
    ) as c:
        result = await q._fetch_group_messages(c, CONV, [B, A], 20)
    if wrong_thread:
        assert result is None
    else:
        assert [m["id"] for m in result] == ["AC1", "AC2"]


@pytest.mark.asyncio
async def test_group_reader_uses_api_before_webhook_fallback(monkeypatch):
    monkeypatch.setattr(q, "get_all_contacts", AsyncMock(return_value=[]))
    monkeypatch.setattr(q, "_find_group_conversation", AsyncMock(return_value=CONV))
    fallback = AsyncMock()
    monkeypatch.setattr(q, "_fetch_group_thread_from_events_table", fallback)

    def handler(req):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "AC1",
                        "conversationId": "CNgroup",
                        "from": A,
                        "to": [B],
                        "text": "Group update",
                        "createdAt": "2026-09-10T12:00:00Z",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
    ) as c:
        result = await q.get_thread_messages_impl(c, [A, B])
    assert "Group update" in result
    assert "1:1 thread with" not in result
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_recent_thread_on_later_page_is_not_hidden(monkeypatch):
    monkeypatch.setattr(q, "get_all_contacts", AsyncMock(return_value=[]))
    pages = []

    def handler(req):
        if req.url.path == "/v1/conversations":
            assert req.url.params["phoneNumbers"] == q.QUO_SHARED_EXTERNAL_PHONE_ID
            assert "phoneNumbers[]" not in req.url.params
            pages.append(req.url.params.get("pageToken"))
            if len(pages) == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [CONV | {"id": "CNold", "lastActivityAt": "2025-01-01T12:00:00Z"}],
                        "nextPageToken": "next",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [CONV | {"id": "CNnew", "lastActivityAt": "2026-09-10T12:00:00Z"}],
                    "nextPageToken": None,
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
    ) as c:
        result = await q.list_active_threads_impl(c, max_results=1)
    assert len(pages) == 2
    assert "CNnew" in result and "CNold" not in result
