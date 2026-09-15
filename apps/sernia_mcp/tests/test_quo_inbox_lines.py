"""Regression coverage for internal replies hidden by shared-line-only reads."""

from unittest.mock import AsyncMock

import httpx
import pytest

from sernia_mcp.core.quo import contacts as q

A = "+14125550101"


@pytest.mark.asyncio
@pytest.mark.parametrize("internal", [False, True])
async def test_direct_history_uses_contact_sending_line(monkeypatch, internal):
    contacts = [
        {
            "defaultFields": {
                "firstName": "Maintenance",
                "company": q.QUO_INTERNAL_COMPANY if internal else "Tenant",
                "phoneNumbers": [{"value": A}],
            }
        }
    ]
    monkeypatch.setattr(q, "get_all_contacts", AsyncMock(return_value=contacts))
    expected_line = q.QUO_SERNIA_AI_PHONE_ID if internal else q.QUO_SHARED_EXTERNAL_PHONE_ID
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert req.url.params["phoneNumberId"] == expected_line
        assert req.url.params["participants"] == A
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "ACreply",
                        "text": "Repair completed today",
                        "direction": "incoming",
                        "from": A,
                        "createdAt": "2026-09-15T13:17:15Z",
                    }
                ]
                if req.url.path == "/v1/messages"
                else []
            },
        )

    monkeypatch.setattr(
        q,
        "build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
        ),
    )
    result = await q.get_thread_messages_core(A)
    assert set(paths) == {"/v1/messages", "/v1/calls"}
    assert "Repair completed today" in result
    assert ("Inbox: Sernia AI Intern" if internal else "Inbox: Sernia Capital Team") in result


@pytest.mark.asyncio
async def test_inbox_scans_both_lines_and_keeps_snippets_on_their_line(monkeypatch):
    monkeypatch.setattr(q, "get_all_contacts", AsyncMock(return_value=[]))
    seen = set()

    def handler(req):
        if req.url.path == "/v1/conversations":
            assert req.url.params.get_list("phoneNumbers") == [
                q.QUO_SHARED_EXTERNAL_PHONE_ID,
                q.QUO_SERNIA_AI_PHONE_ID,
            ]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "CNteam",
                            "phoneNumberId": q.QUO_SHARED_EXTERNAL_PHONE_ID,
                            "participants": [A],
                            "lastActivityAt": "2026-09-14T13:00:00Z",
                        },
                        {
                            "id": "CNai",
                            "phoneNumberId": q.QUO_SERNIA_AI_PHONE_ID,
                            "participants": [A],
                            "lastActivityAt": "2026-09-15T13:00:00Z",
                        },
                    ]
                },
            )
        line = req.url.params["phoneNumberId"]
        seen.add((req.url.path, line))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "text": "Internal repair reply"
                        if line == q.QUO_SERNIA_AI_PHONE_ID
                        else "Shared-line history",
                        "createdAt": "2026-09-15T13:00:00Z",
                        "direction": "incoming",
                        "from": A,
                    }
                ]
                if req.url.path == "/v1/messages"
                else []
            },
        )

    monkeypatch.setattr(
        q,
        "build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(handler)
        ),
    )
    result = await q.list_active_threads_core(updated_after_days=7)
    assert len(seen) == 4
    first, second = result.split("Thread: ")[1:]
    assert "CNai" in first and "Internal repair reply" in first
    assert "Inbox: Sernia AI Intern" in first
    assert "CNteam" in second and "Shared-line history" in second
    assert "Inbox: Sernia Capital Team" in second
