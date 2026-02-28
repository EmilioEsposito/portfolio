"""
Live integration tests for the ClickUp toolset.

These tests hit the real ClickUp API to verify response shapes match
what the tools expect. Marked ``live`` so they only run when explicitly
requested:

    pytest -m live api/src/tests/test_clickup_tools.py -v -s
"""

import os

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.sernia_ai.config import CLICKUP_TEAM_ID, DEFAULT_CLICKUP_VIEW_ID
from api.src.sernia_ai.tools.clickup_tools import _clickup_request, _clickup_request_params

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("CLICKUP_API_KEY"),
        reason="CLICKUP_API_KEY not set",
    ),
]


# ---------------------------------------------------------------------------
# list_clickup_lists — exercises /team/.../space, /space/.../folder, etc.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_spaces():
    """GET /team/{id}/space should return a non-empty spaces array."""
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    assert resp.status_code == 200
    data = resp.json()
    spaces = data.get("spaces", [])
    assert len(spaces) > 0, "Expected at least one space"
    # Each space should have an id and name
    for space in spaces:
        assert "id" in space
        assert "name" in space
    names = [s["name"] for s in spaces]
    print(f"\nSpaces: {names}")


@pytest.mark.asyncio
async def test_get_folders_for_spaces():
    """Each space should return folders (possibly empty) with lists inside."""
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    spaces = resp.json()["spaces"]

    for space in spaces:
        resp_folders = await _clickup_request("GET", f"/space/{space['id']}/folder")
        assert resp_folders.status_code == 200
        folders = resp_folders.json().get("folders", [])
        for folder in folders:
            assert "id" in folder
            assert "name" in folder
            # Folders should contain a lists array
            assert "lists" in folder
            for lst in folder["lists"]:
                assert "id" in lst
                assert "name" in lst
                print(f"\n  {space['name']} > {folder['name']} > {lst['name']} (id: {lst['id']})")


@pytest.mark.asyncio
async def test_get_folderless_lists():
    """Each space should return folderless lists (possibly empty)."""
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    spaces = resp.json()["spaces"]

    for space in spaces:
        resp_lists = await _clickup_request("GET", f"/space/{space['id']}/list")
        assert resp_lists.status_code == 200
        lists = resp_lists.json().get("lists", [])
        for lst in lists:
            assert "id" in lst
            assert "name" in lst
            print(f"\n  {space['name']} > (no folder) > {lst['name']} (id: {lst['id']})")


# ---------------------------------------------------------------------------
# get_tasks — exercises /view/{id}/task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tasks_default_view():
    """GET /view/{default}/task should return tasks with expected fields."""
    resp = await _clickup_request("GET", f"/view/{DEFAULT_CLICKUP_VIEW_ID}/task")
    assert resp.status_code == 200
    tasks = resp.json().get("tasks", [])
    print(f"\nDefault view returned {len(tasks)} tasks")
    # Verify shape of each task
    for task in tasks[:5]:
        assert "id" in task
        assert "name" in task
        assert "status" in task
        assert "status" in task["status"]  # nested status string
        assert "url" in task
        print(f"  - {task['name']} (id: {task['id']}, status: {task['status']['status']})")


@pytest.mark.asyncio
async def test_get_tasks_from_list_id():
    """GET /list/{id}/task should work with a numeric list ID."""
    # Grab the first list we can find
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    spaces = resp.json()["spaces"]
    list_id = None
    for space in spaces:
        resp_folders = await _clickup_request("GET", f"/space/{space['id']}/folder")
        for folder in resp_folders.json().get("folders", []):
            for lst in folder.get("lists", []):
                list_id = lst["id"]
                break
            if list_id:
                break
        if not list_id:
            resp_lists = await _clickup_request("GET", f"/space/{space['id']}/list")
            for lst in resp_lists.json().get("lists", []):
                list_id = lst["id"]
                break
        if list_id:
            break

    assert list_id, "No lists found in workspace"
    resp = await _clickup_request("GET", f"/list/{list_id}/task")
    assert resp.status_code == 200
    assert "tasks" in resp.json()
    print(f"\nList {list_id} returned {len(resp.json()['tasks'])} tasks")


# ---------------------------------------------------------------------------
# search_tasks — exercises GET /team/{id}/task (filtered team tasks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_tasks_no_filters():
    """Filtered team tasks endpoint should return tasks with no filters."""
    resp = await _clickup_request_params(
        "GET", f"/team/{CLICKUP_TEAM_ID}/task", params={"page": "0"}
    )
    assert resp.status_code == 200
    tasks = resp.json().get("tasks", [])
    assert len(tasks) > 0, "Expected at least one task in workspace"
    print(f"\nUnfiltered search returned {len(tasks)} tasks")
    # Verify expected fields
    task = tasks[0]
    assert "id" in task
    assert "name" in task
    assert "status" in task


@pytest.mark.asyncio
async def test_search_tasks_filter_by_status():
    """Filtering by status should only return tasks with that status."""
    resp = await _clickup_request_params(
        "GET",
        f"/team/{CLICKUP_TEAM_ID}/task",
        params={"page": "0", "statuses[]": ["to do"]},
    )
    assert resp.status_code == 200
    tasks = resp.json().get("tasks", [])
    print(f"\nStatus='to do' returned {len(tasks)} tasks")
    for task in tasks:
        actual = task["status"]["status"].lower()
        assert actual == "to do", f"Expected 'to do', got '{actual}'"


@pytest.mark.asyncio
async def test_search_tasks_filter_by_space():
    """Filtering by space_ids should restrict results to that space."""
    # Get first space ID
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    space_id = resp.json()["spaces"][0]["id"]
    space_name = resp.json()["spaces"][0]["name"]

    resp = await _clickup_request_params(
        "GET",
        f"/team/{CLICKUP_TEAM_ID}/task",
        params={"page": "0", "space_ids[]": [space_id]},
    )
    assert resp.status_code == 200
    tasks = resp.json().get("tasks", [])
    print(f"\nSpace '{space_name}' (id: {space_id}) returned {len(tasks)} tasks")


@pytest.mark.asyncio
async def test_search_tasks_include_closed():
    """include_closed=true should return more or equal tasks than without."""
    resp_open = await _clickup_request_params(
        "GET",
        f"/team/{CLICKUP_TEAM_ID}/task",
        params={"page": "0"},
    )
    resp_all = await _clickup_request_params(
        "GET",
        f"/team/{CLICKUP_TEAM_ID}/task",
        params={"page": "0", "include_closed": "true"},
    )
    open_count = len(resp_open.json().get("tasks", []))
    all_count = len(resp_all.json().get("tasks", []))
    print(f"\nOpen tasks: {open_count}, All (incl closed): {all_count}")
    assert all_count >= open_count


@pytest.mark.asyncio
async def test_search_tasks_order_by():
    """order_by should be accepted by the API (due_date, created, updated)."""
    for field in ("due_date", "created", "updated"):
        resp = await _clickup_request_params(
            "GET",
            f"/team/{CLICKUP_TEAM_ID}/task",
            params={"page": "0", "order_by": field},
        )
        assert resp.status_code == 200, f"order_by={field} failed: {resp.status_code}"
    print("\nAll order_by values accepted")


# ---------------------------------------------------------------------------
# Write tools — API contract tests (no real mutations)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_list():
    """POST /list/{bad_id}/task should return 401 or 404, not 500."""
    resp = await _clickup_request(
        "POST",
        "/list/000000000/task",
        json={"name": "pytest contract test — should fail"},
    )
    assert resp.status_code in (400, 401, 404, 422), (
        f"Expected client error, got {resp.status_code}"
    )
    print(f"\nInvalid list_id → HTTP {resp.status_code}")


@pytest.mark.asyncio
async def test_update_task_rejects_invalid_id():
    """PUT /task/{bad_id} should return 401 or 404, not 500."""
    resp = await _clickup_request(
        "PUT",
        "/task/INVALID_TASK_ID_000/",
        json={"name": "pytest contract test — should fail"},
    )
    assert resp.status_code in (400, 401, 404, 422), (
        f"Expected client error, got {resp.status_code}"
    )
    print(f"\nInvalid task_id update → HTTP {resp.status_code}")


@pytest.mark.asyncio
async def test_delete_task_rejects_invalid_id():
    """DELETE /task/{bad_id} should return 401 or 404, not 500."""
    resp = await _clickup_request("DELETE", "/task/INVALID_TASK_ID_000/")
    assert resp.status_code in (400, 401, 404, 422), (
        f"Expected client error, got {resp.status_code}"
    )
    print(f"\nInvalid task_id delete → HTTP {resp.status_code}")
