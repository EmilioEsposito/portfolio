"""ClickUp task search core function."""
from __future__ import annotations

from datetime import datetime

from sernia_mcp.clients._fuzzy import fuzzy_filter_json
from sernia_mcp.config import CLICKUP_TEAM_ID
from sernia_mcp.core.clickup._client import clickup_request
from sernia_mcp.core.errors import ExternalServiceError


async def search_tasks_core(
    query: str | None = None,
    *,
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
    """Search ClickUp tasks with filters + optional fuzzy text match."""
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
        params["due_date_gt"] = str(int(datetime.fromisoformat(due_date_gt).timestamp() * 1000))
    if due_date_lt:
        params["due_date_lt"] = str(int(datetime.fromisoformat(due_date_lt).timestamp() * 1000))
    if order_by:
        params["order_by"] = order_by
    if subtasks:
        params["subtasks"] = "true"

    resp = await clickup_request(
        "GET", f"/team/{CLICKUP_TEAM_ID}/task", params=params
    )
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code}: {resp.text[:200]}"
        )

    tasks = resp.json().get("tasks", [])
    if not tasks:
        return "No tasks found matching the filters."

    if query:
        return fuzzy_filter_json(tasks, query, top_n=10)

    lines: list[str] = []
    for task in tasks:
        name = task.get("name", "(untitled)")
        task_id = task.get("id", "?")
        status = task.get("status", {}).get("status", "?")
        priority_obj = task.get("priority")
        priority_str = priority_obj.get("priority", "none") if priority_obj else "none"
        due_date = task.get("due_date")
        due_str = (
            datetime.fromtimestamp(int(due_date) / 1000).strftime("%Y-%m-%d")
            if due_date
            else "no due date"
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
