"""ClickUp assignments are optional on create and incremental on update."""

from unittest.mock import AsyncMock

import httpx
import pytest

from api.src.sernia_ai.tools import clickup_tools as tools

pytestmark = pytest.mark.asyncio


@pytest.fixture
def request_mock(monkeypatch):
    response = httpx.Response(
        200, json={"id": "task1", "assignees": [{"id": 27, "username": "Alex"}]}
    )
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr(tools, "_clickup_request", mock)
    return mock


@pytest.mark.parametrize("ids", [None, [], [27, 39]])
async def test_create_assignment(request_mock, ids):
    result = await tools.create_task(None, "123", "Fix sink", assignee_ids=ids)
    expected = {"name": "Fix sink"}
    if ids:
        expected["assignees"] = ids
    request_mock.assert_awaited_once_with("POST", "/list/123/task", json=expected)
    assert "Assignees: Alex (id: 27)" in result


@pytest.mark.parametrize(
    ("fields", "payload"),
    [
        ({"add_assignee_ids": [27]}, {"assignees": {"add": [27], "rem": []}}),
        ({"remove_assignee_ids": [39]}, {"assignees": {"add": [], "rem": [39]}}),
        (
            {"add_assignee_ids": [27], "remove_assignee_ids": [39], "priority": 2},
            {"assignees": {"add": [27], "rem": [39]}, "priority": 2},
        ),
        ({"priority": 2}, {"priority": 2}),
    ],
)
async def test_update_assignment(request_mock, fields, payload):
    result = await tools.update_task(None, "task1", **fields)
    request_mock.assert_awaited_once_with("PUT", "/task/task1", json=payload)
    assert "Assignees: Alex (id: 27)" in result


async def test_conflicting_edit_skips_write(request_mock):
    result = await tools.update_task(None, "task1", add_assignee_ids=[27], remove_assignee_ids=[27])
    assert "both added and removed" in result
    request_mock.assert_not_awaited()


async def test_member_lookup_scoped_to_sernia(request_mock):
    request_mock.return_value = httpx.Response(
        200,
        json={
            "teams": [
                {"id": "other", "members": [{"user": {"id": 1, "username": "Alex"}}]},
                {
                    "id": tools.CLICKUP_TEAM_ID,
                    "members": [
                        {"user": {"id": 27, "username": "Alex", "email": "alex@example.test"}}
                    ],
                },
            ]
        },
    )
    assert await tools.list_members(None) == "Alex <alex@example.test> (id: 27)"
    request_mock.assert_awaited_once_with("GET", "/team")
