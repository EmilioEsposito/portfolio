"""MCP wrappers for ClickUp tools."""
from fastmcp.exceptions import ToolError

from sernia_mcp.core.clickup.reads import (
    get_maintenance_field_options_core,
    get_tasks_core,
    list_clickup_lists_core,
)
from sernia_mcp.core.clickup.tasks import search_tasks_core
from sernia_mcp.core.clickup.writes import (
    create_task_core,
    set_task_custom_field_core,
    update_task_core,
)
from sernia_mcp.core.errors import CoreError
from sernia_mcp.server import mcp


@mcp.tool
async def clickup_search_tasks(
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
    """Search ClickUp tasks across the workspace with filters + optional fuzzy text.

    Args:
        query: Optional fuzzy text match against task names/descriptions.
        statuses: Filter by status names (e.g. ["to do", "in progress"]).
        assignee_ids: Filter by assignee user IDs.
        tags: Filter by tag names.
        list_ids: Restrict to specific lists by ID.
        space_ids: Restrict to specific spaces by ID.
        include_closed: Include closed/completed tasks (default false).
        due_date_gt: ISO date — tasks due after this.
        due_date_lt: ISO date — tasks due before this.
        order_by: "created" | "updated" | "due_date".
        subtasks: Include subtasks (default false).
        page: 0-indexed page (100 tasks per page).
    """
    try:
        return await search_tasks_core(
            query,
            statuses=statuses,
            assignee_ids=assignee_ids,
            tags=tags,
            list_ids=list_ids,
            space_ids=space_ids,
            include_closed=include_closed,
            due_date_gt=due_date_gt,
            due_date_lt=due_date_lt,
            order_by=order_by,
            subtasks=subtasks,
            page=page,
        )
    except CoreError as e:
        raise ToolError(f"clickup_search_tasks failed: {e}") from e


@mcp.tool
async def clickup_create_task(
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
        list_id: The list to create the task in.
        name: Task name.
        description: Optional task description (markdown supported).
        status: Optional status string (e.g. "to do", "in progress").
        priority: Optional priority 1-4 (1=urgent, 2=high, 3=normal, 4=low).
        due_date: Optional ISO date or datetime (e.g. "2026-04-30",
            "2026-04-30T17:00:00").
        custom_fields: Optional list of ``{"id": "<uuid>", "value": ...}``
            entries. For drop_down fields, ``value`` must be the option UUID.
    """
    try:
        return await create_task_core(
            list_id,
            name,
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
            custom_fields=custom_fields,
        )
    except CoreError as e:
        raise ToolError(f"clickup_create_task failed: {e}") from e


@mcp.tool
async def clickup_update_task(
    task_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
) -> str:
    """Update an existing ClickUp task.

    Pass only the fields you want to change. Pass ``due_date=""`` to clear
    the due date; pass ``due_date=None`` (or omit) to leave it unchanged.

    Args:
        task_id: The task ID to update.
        name: New task name.
        description: New description (markdown supported).
        status: New status string.
        priority: New priority 1-4.
        due_date: New ISO date/datetime, ``""`` to clear, or omit to keep.
    """
    try:
        return await update_task_core(
            task_id,
            name=name,
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
        )
    except CoreError as e:
        raise ToolError(f"clickup_update_task failed: {e}") from e


@mcp.tool
async def clickup_set_task_custom_field(
    task_id: str,
    field_id: str,
    value: str | int | float | bool | dict | list | None,
) -> str:
    """Set or update a single custom field on an existing ClickUp task.

    Args:
        task_id: The task ID.
        field_id: The custom field UUID.
        value: The value. For drop_down fields, pass the option UUID
            (not the label).
    """
    try:
        return await set_task_custom_field_core(task_id, field_id, value)
    except CoreError as e:
        raise ToolError(f"clickup_set_task_custom_field failed: {e}") from e


@mcp.tool
async def clickup_list_lists() -> str:
    """List all spaces, folders, and lists in the ClickUp workspace.

    Returns a formatted hierarchy with each list's ID and task count.
    Use the IDs as the ``list_id`` argument to ``clickup_create_task``
    or as ``list_or_view_id`` to ``clickup_get_tasks``.
    """
    try:
        return await list_clickup_lists_core()
    except CoreError as e:
        raise ToolError(f"clickup_list_lists failed: {e}") from e


@mcp.tool
async def clickup_get_tasks(list_or_view_id: str | None = None) -> str:
    """Get tasks from a ClickUp list or view.

    For broader cross-workspace search with fuzzy matching, prefer
    ``clickup_search_tasks``. This tool is the right choice when you
    already have a specific list/view ID.

    Args:
        list_or_view_id: ClickUp list ID (numeric) or view ID (contains
            hyphens/letters). Defaults to the curated Sernia view if omitted.
    """
    try:
        return await get_tasks_core(list_or_view_id)
    except CoreError as e:
        raise ToolError(f"clickup_get_tasks failed: {e}") from e


@mcp.tool
async def clickup_get_maintenance_field_options() -> str:
    """Return the maintenance list's custom-field IDs and dropdown UUIDs.

    Use this before calling ``clickup_create_task`` or
    ``clickup_set_task_custom_field`` on the maintenance list — drop_down
    custom-field values must be the option UUID, not the human label.
    Pure formatter; no API call.
    """
    try:
        return await get_maintenance_field_options_core()
    except CoreError as e:
        raise ToolError(f"clickup_get_maintenance_field_options failed: {e}") from e
