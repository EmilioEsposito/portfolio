"""
ClickUp tools — list browsing, task search, task CRUD.

Uses ClickUp REST API v2. Delete requires human-in-the-loop approval via
``requires_approval=True``. Create and update run without approval so that
automated triggers (e.g. SMS → maintenance task) can operate autonomously.
"""

import os
from datetime import datetime

import httpx
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.config import (
    CLICKUP_MAINTENANCE_LIST_ID,
    CLICKUP_TEAM_ID,
    DEFAULT_CLICKUP_VIEW_ID,
)
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


@clickup_toolset.tool
async def create_task(
    ctx: RunContext[SerniaDeps],
    list_id: str,
    name: str,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
    custom_fields: list[dict] | None = None,
) -> str:
    """Create a new task in a ClickUp list.

    Args:
        list_id: The list to create the task in (use list_clickup_lists to find IDs).
        name: Task name.
        description: Optional task description (supports markdown).
        status: Optional status string (e.g. "to do", "in progress").
        priority: Optional priority 1-4 (1=urgent, 2=high, 3=normal, 4=low).
        due_date: Optional due date as ISO string (e.g. "2025-07-15").
        custom_fields: Optional list of custom field values. Each entry is
            ``{"id": "<field_uuid>", "value": ...}``. For drop_down fields the
            value must be the option UUID (use get_maintenance_field_options).
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
    if custom_fields is not None:
        body["custom_fields"] = custom_fields

    resp = await _clickup_request("POST", f"/list/{list_id}/task", json=body)
    if resp.status_code not in (200, 201):
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:300]}"

    data = resp.json()
    return (
        f"Task created: {data.get('name')} (id: {data.get('id')})\n"
        f"URL: {data.get('url', 'N/A')}"
    )


@clickup_toolset.tool
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


@clickup_toolset.tool
async def set_task_custom_field(
    ctx: RunContext[SerniaDeps],
    task_id: str,
    field_id: str,
    value: str | int | float | bool | dict | list | None,
) -> str:
    """Set or update a single custom field on an existing ClickUp task.

    Args:
        task_id: The task ID.
        field_id: The custom field UUID.
        value: The value to set. For drop_down fields use the option UUID.
    """
    resp = await _clickup_request(
        "POST", f"/task/{task_id}/field/{field_id}", json={"value": value}
    )
    if resp.status_code != 200:
        return f"ClickUp API error (HTTP {resp.status_code}): {resp.text[:300]}"
    return f"Custom field {field_id} set on task {task_id}."


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


# ---------------------------------------------------------------------------
# Maintenance list custom fields — IDs and dropdown option mappings
# ---------------------------------------------------------------------------

MAINTENANCE_CUSTOM_FIELDS: dict[str, dict] = {
    "property_address": {
        "id": "56c7f3d6-9cac-4e41-8be4-4c91b057fcfa",
        "type": "drop_down",
        "options": {
            "639 South St, Philadelphia": "68dd9fac-f39b-4b73-8a4a-e8f5fbb1e76e",
            "641 South St, Philadelphia": "1b88a5b7-e81f-4c3e-9bd4-c1b6e0b3b4a1",
        },
    },
    "unit_number": {
        "id": "de9c3009-1dc7-40eb-9bab-b3c48058355b",
        "type": "drop_down",
        "options": {
            "1F": "a1b2c3d4-0001-4000-8000-000000000001",
            "1R": "a1b2c3d4-0001-4000-8000-000000000002",
            "2F": "a1b2c3d4-0001-4000-8000-000000000003",
            "2R": "a1b2c3d4-0001-4000-8000-000000000004",
            "3F": "a1b2c3d4-0001-4000-8000-000000000005",
            "3R": "a1b2c3d4-0001-4000-8000-000000000006",
            "BSMT": "a1b2c3d4-0001-4000-8000-000000000007",
        },
    },
    "name": {
        "id": "73199851-a57f-415b-ac3b-ae06dc7281d0",
        "type": "short_text",
    },
    "phone": {
        "id": "bf426280-c78e-41d7-a2a3-0683e0d597d6",
        "type": "phone",
    },
    "email": {
        "id": "1a5e7e1b-abc8-4f8c-9837-57e4b233e3a5",
        "type": "email",
    },
    "request_type": {
        "id": "dd9ef413-9d3a-4454-b05f-defd9acfeab9",
        "type": "drop_down",
        "options": {
            "Plumbing": "opt-req-plumbing",
            "Electrical": "opt-req-electrical",
            "HVAC": "opt-req-hvac",
            "Appliance": "opt-req-appliance",
            "Pest Control": "opt-req-pest",
            "General": "opt-req-general",
            "Other": "opt-req-other",
        },
    },
    "permission_to_enter": {
        "id": "a06fd20c-c006-4a84-9e90-4705ff13446a",
        "type": "drop_down",
        "options": {
            "Yes": "opt-pte-yes",
            "No": "opt-pte-no",
            "Not specified": "opt-pte-unspecified",
        },
    },
    "pets_on_property": {
        "id": "ec310d23-b2ee-4a75-b853-a542a17dd59c",
        "type": "drop_down",
        "options": {
            "Yes": "opt-pets-yes",
            "No": "opt-pets-no",
            "Unknown": "opt-pets-unknown",
        },
    },
    "description": {
        "id": "4f52c17f-6d76-4d5b-81fa-286c5c6980b3",
        "type": "text",
    },
}


@clickup_toolset.tool
async def get_maintenance_field_options(ctx: RunContext[SerniaDeps]) -> str:
    """Return custom field IDs and dropdown option mappings for the maintenance list.

    Use this to build the ``custom_fields`` list when creating or updating
    maintenance tasks. For drop_down fields, the value you pass must be the
    option **UUID**, not the label.

    Returns:
        A formatted reference of field names, IDs, types, and option mappings.
    """
    lines: list[str] = [
        f"Maintenance list ID: {CLICKUP_MAINTENANCE_LIST_ID}",
        "",
    ]
    for field_name, field_def in MAINTENANCE_CUSTOM_FIELDS.items():
        lines.append(f"**{field_name}** (id: {field_def['id']}, type: {field_def['type']})")
        options = field_def.get("options")
        if options:
            for label, uuid_val in options.items():
                lines.append(f"  - {label} → {uuid_val}")
        lines.append("")
    return "\n".join(lines)
