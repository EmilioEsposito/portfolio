"""
ClickUp tools — list browsing, task search, task CRUD.

Uses ClickUp REST API v2. Write operations (create, update, delete) require
human-in-the-loop approval via ``requires_approval=True``.
"""

import os
from datetime import datetime

import httpx
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.config import CLICKUP_TEAM_ID, DEFAULT_CLICKUP_VIEW_ID
from api.src.sernia_ai.deps import SerniaDeps
from api.src.utils.fuzzy_json import fuzzy_filter_json

clickup_toolset = FunctionToolset()

CLICKUP_API_KEY = os.getenv("CLICKUP_API_KEY", "")
_BASE = "https://api.clickup.com/api/v2"


async def _clickup_request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    """Send an authenticated request to the ClickUp API."""
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": CLICKUP_API_KEY,
    }
    async with httpx.AsyncClient() as client:
        return await client.request(
            method,
            f"{_BASE}{path}",
            headers=headers,
            json=json,
            timeout=20,
        )


async def _clickup_request_params(
    method: str,
    path: str,
    *,
    params: dict | None = None,
) -> httpx.Response:
    """Send an authenticated request with query parameters to the ClickUp API."""
    headers = {
        "accept": "application/json",
        "Authorization": CLICKUP_API_KEY,
    }
    async with httpx.AsyncClient() as client:
        return await client.request(
            method,
            f"{_BASE}{path}",
            headers=headers,
            params=params,
            timeout=20,
        )


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


@clickup_toolset.tool
async def list_clickup_lists(ctx: RunContext[SerniaDeps]) -> str:
    """List all spaces, folders, and lists in the ClickUp workspace.

    Returns a formatted hierarchy with list IDs so you can target the right
    list for task creation or browsing.
    """
    # 1. Get all spaces
    resp = await _clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    if resp.status_code != 200:
        return f"ClickUp API error fetching spaces (HTTP {resp.status_code}): {resp.text[:200]}"
    spaces = resp.json().get("spaces", [])

    lines: list[str] = []
    for space in spaces:
        space_name = space.get("name", "(unnamed)")
        space_id = space["id"]
        lines.append(f"## {space_name}")

        # 2a. Folders in this space
        resp_folders = await _clickup_request("GET", f"/space/{space_id}/folder")
        if resp_folders.status_code == 200:
            for folder in resp_folders.json().get("folders", []):
                folder_name = folder.get("name", "(unnamed)")
                lines.append(f"  📁 {folder_name}")

                # 3. Lists inside each folder
                for lst in folder.get("lists", []):
                    task_count = lst.get("task_count", "?")
                    lines.append(
                        f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})"
                    )

        # 2b. Folderless lists
        resp_lists = await _clickup_request("GET", f"/space/{space_id}/list")
        if resp_lists.status_code == 200:
            folderless = resp_lists.json().get("lists", [])
            if folderless:
                lines.append("  📁 (no folder)")
                for lst in folderless:
                    task_count = lst.get("task_count", "?")
                    lines.append(
                        f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})"
                    )

        lines.append("")  # blank line between spaces

    return "\n".join(lines) if lines else "No spaces found."


@clickup_toolset.tool
async def get_tasks(
    ctx: RunContext[SerniaDeps],
    list_or_view_id: str | None = None,
) -> str:
    """Get tasks from a ClickUp list or view.

    Args:
        list_or_view_id: A ClickUp list ID (numeric, from list_clickup_lists) or
            view ID. Defaults to the main Sernia task view if omitted.
    """
    target_id = list_or_view_id or DEFAULT_CLICKUP_VIEW_ID

    # List IDs are numeric; view IDs contain hyphens/letters.
    if target_id.isdigit():
        response = await _clickup_request("GET", f"/list/{target_id}/task")
    else:
        response = await _clickup_request("GET", f"/view/{target_id}/task")

    if response.status_code != 200:
        return f"ClickUp API error (HTTP {response.status_code}): {response.text[:200]}"

    tasks = response.json().get("tasks", [])
    if not tasks:
        return "No tasks found."

    lines = []
    for task in tasks:
        name = task.get("name", "(untitled)")
        task_id = task.get("id", "?")
        status = task.get("status", {}).get("status", "?")
        priority = task.get("priority")
        priority_str = priority.get("priority", "none") if priority else "none"
        due_date = task.get("due_date")
        due_str = "no due date"
        if due_date:
            due_str = datetime.fromtimestamp(int(due_date) / 1000).strftime(
                "%Y-%m-%d"
            )
        url_link = task.get("url", "")

        lines.append(
            f"- {name} (id: {task_id})\n"
            f"  Status: {status} | Priority: {priority_str} | Due: {due_str}\n"
            f"  URL: {url_link}"
        )
    return "\n".join(lines)


@clickup_toolset.tool
async def search_tasks(
    ctx: RunContext[SerniaDeps],
    query: str | None = None,
    statuses: list[str] | None = None,
    assignee_ids: list[int] | None = None,
    tags: list[str] | None = None,
    list_ids: list[str] | None = None,
    space_ids: list[str] | None = None,
    include_closed: bool = False,
    due_date_gt: str | None = None,
    due_date_lt: str | None = None,
    order_by: str | None = None,
    subtasks: bool = False,
    page: int = 0,
) -> str:
    """Search tasks across the entire ClickUp workspace with filters.

    Combines server-side filters (fast, deterministic) with an optional fuzzy
    text query applied client-side to the results.

    Args:
        query: Optional text to fuzzy-match against task names, descriptions, and
            other fields. Applied client-side after API filters. Tolerates typos.
        statuses: Filter by status names (e.g. ["to do", "in progress"]).
        assignee_ids: Filter by assignee user IDs.
        tags: Filter by tag names.
        list_ids: Filter to specific lists by ID.
        space_ids: Filter to specific spaces by ID.
        include_closed: Include closed/completed tasks (default false).
        due_date_gt: Only tasks due after this ISO date (e.g. "2025-07-01").
        due_date_lt: Only tasks due before this ISO date.
        order_by: Sort field — "created", "updated", or "due_date".
        subtasks: Include subtasks in results (default false).
        page: Page number for pagination (0-indexed, 100 tasks per page).
    """
    params: dict[str, str | list[str]] = {"page": str(page)}
    if statuses:
        params["statuses[]"] = statuses
    if assignee_ids:
        params["assignees[]"] = [str(a) for a in assignee_ids]
    if tags:
        params["tags[]"] = tags
    if list_ids:
        params["list_ids[]"] = list_ids
    if space_ids:
        params["space_ids[]"] = space_ids
    if include_closed:
        params["include_closed"] = "true"
    if due_date_gt:
        dt = datetime.fromisoformat(due_date_gt)
        params["due_date_gt"] = str(int(dt.timestamp() * 1000))
    if due_date_lt:
        dt = datetime.fromisoformat(due_date_lt)
        params["due_date_lt"] = str(int(dt.timestamp() * 1000))
    if order_by:
        params["order_by"] = order_by
    if subtasks:
        params["subtasks"] = "true"

    resp = await _clickup_request_params(
        "GET", f"/team/{CLICKUP_TEAM_ID}/task", params=params
    )
    if resp.status_code != 200:
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:200]}"

    tasks = resp.json().get("tasks", [])
    if not tasks:
        return "No tasks found matching the filters."

    # If a fuzzy query is provided, filter results client-side
    if query:
        return fuzzy_filter_json(tasks, query, top_n=10)

    # No query — return all results formatted
    lines = []
    for task in tasks:
        name = task.get("name", "(untitled)")
        task_id = task.get("id", "?")
        status = task.get("status", {}).get("status", "?")
        priority = task.get("priority")
        priority_str = priority.get("priority", "none") if priority else "none"
        due_date = task.get("due_date")
        due_str = "no due date"
        if due_date:
            due_str = datetime.fromtimestamp(int(due_date) / 1000).strftime(
                "%Y-%m-%d"
            )
        assignees = task.get("assignees", [])
        assignee_str = (
            ", ".join(a.get("username", "?") for a in assignees)
            if assignees
            else "unassigned"
        )
        url_link = task.get("url", "")

        lines.append(
            f"- {name} (id: {task_id})\n"
            f"  Status: {status} | Priority: {priority_str} | Due: {due_str}\n"
            f"  Assignees: {assignee_str}\n"
            f"  URL: {url_link}"
        )
    result = "\n".join(lines)
    if len(tasks) == 100:
        result += f"\n\n(Page {page} — 100 tasks returned, more may exist on page {page + 1})"
    return result


# ---------------------------------------------------------------------------
# Write tools (require approval)
# ---------------------------------------------------------------------------


@clickup_toolset.tool(requires_approval=True)
async def create_task(
    ctx: RunContext[SerniaDeps],
    list_id: str,
    name: str,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
) -> str:
    """Create a new task in a ClickUp list.

    Args:
        list_id: The list to create the task in (use list_clickup_lists to find IDs).
        name: Task name.
        description: Optional task description (supports markdown).
        status: Optional status string (e.g. "to do", "in progress").
        priority: Optional priority 1-4 (1=urgent, 2=high, 3=normal, 4=low).
        due_date: Optional due date as ISO string (e.g. "2025-07-15").
    """
    body: dict = {"name": name}
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if due_date is not None:
        # ClickUp expects millisecond Unix timestamp
        dt = datetime.fromisoformat(due_date)
        body["due_date"] = int(dt.timestamp() * 1000)

    resp = await _clickup_request("POST", f"/list/{list_id}/task", json=body)
    if resp.status_code not in (200, 201):
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:300]}"

    data = resp.json()
    return (
        f"Task created: {data.get('name')} (id: {data.get('id')})\n"
        f"URL: {data.get('url', 'N/A')}"
    )


@clickup_toolset.tool(requires_approval=True)
async def update_task(
    ctx: RunContext[SerniaDeps],
    task_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
) -> str:
    """Update an existing ClickUp task.

    Args:
        task_id: The task ID to update.
        name: New task name.
        description: New description.
        status: New status string.
        priority: New priority 1-4 (1=urgent, 2=high, 3=normal, 4=low).
        due_date: New due date as ISO string, or empty string to clear.
    """
    body: dict = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if due_date is not None:
        if due_date == "":
            body["due_date"] = None
        else:
            dt = datetime.fromisoformat(due_date)
            body["due_date"] = int(dt.timestamp() * 1000)

    if not body:
        return "No fields to update."

    resp = await _clickup_request("PUT", f"/task/{task_id}", json=body)
    if resp.status_code != 200:
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:300]}"

    data = resp.json()
    return (
        f"Task updated: {data.get('name')} (id: {data.get('id')})\n"
        f"URL: {data.get('url', 'N/A')}"
    )


@clickup_toolset.tool(requires_approval=True)
async def delete_task(
    ctx: RunContext[SerniaDeps],
    task_id: str,
) -> str:
    """Delete a ClickUp task.

    Args:
        task_id: The task ID to delete.
    """
    resp = await _clickup_request("DELETE", f"/task/{task_id}")
    if resp.status_code != 200:
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:300]}"
    return f"Task {task_id} deleted."
