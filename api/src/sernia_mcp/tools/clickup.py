"""MCP wrappers for ClickUp tools."""
from fastmcp.exceptions import ToolError

from api.src.sernia_mcp.server import mcp
from api.src.tool_core.clickup.tasks import search_tasks_core
from api.src.tool_core.errors import CoreError


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
