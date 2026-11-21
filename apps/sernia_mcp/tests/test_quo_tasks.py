"""Exercise task tools over MCP with a recording Quo transport."""

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from sernia_mcp.server import mcp
from sernia_mcp.tools import quo


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/TKmissing"):
            return httpx.Response(404)
        if request.method == "GET" and request.url.path == "/v1/tasks":
            return httpx.Response(200, json={"data": [{"taskId": "TK1"}], "nextPageToken": "more"})
        return httpx.Response(200, json={"data": {"taskId": "TK1", "revision": "r1"}})

    monkeypatch.setattr(
        quo,
        "build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://quo.test", transport=httpx.MockTransport(respond)
        ),
    )
    return calls


async def test_create_and_find_task(requests):
    async with Client(mcp) as client:
        task = {
            "title": "Follow up",
            "description": "Call back",
            "assigned_to": "US1",
            "activity_id": "AC1",
            "due_date": "2026-12-01T15:00:00Z",
        }
        created = await client.call_tool("quo_create_task", {"task": task})
        assert created.data["taskId"] == "TK1"
        assert requests[-1].method == "POST" and requests[-1].url.path == "/v1/tasks"
        assert json.loads(requests[-1].content) == {
            "title": "Follow up",
            "description": "Call back",
            "assignedTo": "US1",
            "activityId": "AC1",
            "dueDate": "2026-12-01T15:00:00Z",
        }
        page = await client.call_tool("quo_list_tasks", {"max_results": 5, "page_token": "prior"})
        assert dict(requests[-1].url.params) == {"maxResults": "5", "pageToken": "prior"}
        assert page.data["nextPageToken"] == "more"
        await client.call_tool("quo_get_task", {"task_id": page.data["data"][0]["taskId"]})
        assert requests[-1].url.path == "/v1/tasks/TK1"
        with pytest.raises(ToolError, match="404"):
            await client.call_tool("quo_get_task", {"task_id": "TKmissing"})


@pytest.mark.parametrize(
    ("change", "method", "suffix", "body"),
    [
        ({"title": "New", "description": ""}, "PUT", "", {"title": "New", "description": ""}),
        ({"completed": True}, "POST", "/complete", None),
        ({"completed": False}, "POST", "/reopen", None),
        ({"due_date": None}, "POST", "/remove-due-date", None),
        (
            {"due_date": "2026-12-01T10:00:00-05:00"},
            "POST",
            "/change-due-date",
            {"dueDate": "2026-12-01T10:00:00-05:00"},
        ),
        ({"action": "assign", "user_id": "US1"}, "POST", "/assign", {"userId": "US1"}),
        ({"action": "unassign", "user_id": "US1"}, "POST", "/unassign", {"userId": "US1"}),
        (
            {"action": "link", "conversation_id": "CN1"},
            "POST",
            "/link-conversation",
            {"conversationId": "CN1"},
        ),
        (
            {"action": "unlink", "conversation_id": "CN1"},
            "POST",
            "/unlink-conversation",
            {"conversationId": "CN1"},
        ),
    ],
)
async def test_update_sends_one_change(requests, change, method, suffix, body):
    async with Client(mcp) as client:
        await client.call_tool("quo_update_task", {"task_id": "TK1", "change": change})
    [request] = requests
    assert request.url.path == "/v1/tasks/TK1" + suffix
    assert request.method == method
    assert (json.loads(request.content) if request.content else None) == body


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "quo_create_task",
            {
                "task": {
                    "title": "Call",
                    "description": "",
                    "activity_id": "AC1",
                    "conversation_id": "CN2",
                }
            },
        ),
        ("quo_update_task", {"task_id": "TK1", "change": {"due_date": None, "completed": True}}),
        ("quo_update_task", {"task_id": "TK1", "change": {"due_date": "2026-12-01T10:00:00"}}),
        ("quo_delete_task", {"task_id": "TK1/../../contacts/C1"}),
    ],
)
async def test_reject_invalid_inputs_without_provider_call(requests, tool, args):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(tool, args)
    assert requests == []
