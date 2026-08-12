"""Read-only ClickUp tools — list browsing, list/view tasks, custom-field reference.

Lifted from ``api/src/sernia_ai/tools/clickup_tools.py``. The maintenance
custom-field reference is fetched live from ClickUp in both copies, so
rotating a field's options in the ClickUp UI needs no code change. The two
implementations stay duplicated until the sernia_ai → sernia_mcp migration
completes (see ``apps/sernia_mcp/TODOS.md``).
"""

from __future__ import annotations

import time
from datetime import datetime

from sernia_mcp.config import (
    CLICKUP_MAINTENANCE_LIST_ID,
    CLICKUP_TEAM_ID,
    DEFAULT_CLICKUP_VIEW_ID,
)
from sernia_mcp.core.clickup._client import clickup_request
from sernia_mcp.core.errors import ExternalServiceError

# ---------------------------------------------------------------------------
# Workspace browse
# ---------------------------------------------------------------------------


async def list_clickup_lists_core() -> str:
    """List all spaces, folders, and lists in the ClickUp workspace.

    Returns a formatted hierarchy with list IDs so the agent can pick the
    right list ID before calling ``clickup_create_task``.
    """
    resp = await clickup_request("GET", f"/team/{CLICKUP_TEAM_ID}/space")
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code} fetching spaces: {resp.text[:200]}"
        )
    spaces = resp.json().get("spaces", [])

    lines: list[str] = []
    for space in spaces:
        space_name = space.get("name", "(unnamed)")
        space_id = space["id"]
        lines.append(f"## {space_name}")

        resp_folders = await clickup_request("GET", f"/space/{space_id}/folder")
        if resp_folders.status_code == 200:
            for folder in resp_folders.json().get("folders", []):
                folder_name = folder.get("name", "(unnamed)")
                lines.append(f"  📁 {folder_name}")
                for lst in folder.get("lists", []):
                    task_count = lst.get("task_count", "?")
                    lines.append(f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})")

        resp_lists = await clickup_request("GET", f"/space/{space_id}/list")
        if resp_lists.status_code == 200:
            folderless = resp_lists.json().get("lists", [])
            if folderless:
                lines.append("  📁 (no folder)")
                for lst in folderless:
                    task_count = lst.get("task_count", "?")
                    lines.append(f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})")

        lines.append("")

    return "\n".join(lines) if lines else "No spaces found."


# ---------------------------------------------------------------------------
# Task list (by list or view)
# ---------------------------------------------------------------------------


async def get_tasks_core(list_or_view_id: str | None = None) -> str:
    """Get tasks from a ClickUp list or view.

    List IDs are numeric; view IDs contain hyphens/letters. If
    ``list_or_view_id`` is omitted, defaults to ``DEFAULT_CLICKUP_VIEW_ID``
    (the Sernia "Peppino View"). For broader cross-workspace search with
    fuzzy matching, prefer ``clickup_search_tasks``.
    """
    target_id = list_or_view_id or DEFAULT_CLICKUP_VIEW_ID

    if target_id.isdigit():
        resp = await clickup_request("GET", f"/list/{target_id}/task")
    else:
        resp = await clickup_request("GET", f"/view/{target_id}/task")

    if resp.status_code != 200:
        raise ExternalServiceError(f"ClickUp API HTTP {resp.status_code}: {resp.text[:200]}")

    tasks = resp.json().get("tasks", [])
    if not tasks:
        return "No tasks found."

    lines: list[str] = []
    for task in tasks:
        name = task.get("name", "(untitled)")
        task_id = task.get("id", "?")
        status = task.get("status", {}).get("status", "?")
        priority = task.get("priority")
        priority_str = priority.get("priority", "none") if priority else "none"
        due_date = task.get("due_date")
        due_str = (
            datetime.fromtimestamp(int(due_date) / 1000).strftime("%Y-%m-%d")
            if due_date
            else "no due date"
        )
        url_link = task.get("url", "")
        lines.append(
            f"- {name} (id: {task_id})\n"
            f"  Status: {status} | Priority: {priority_str} | Due: {due_str}\n"
            f"  URL: {url_link}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Maintenance custom fields — fetched live from ClickUp
# ---------------------------------------------------------------------------
#
# These used to be a hardcoded dict. The field IDs in it were real, but every
# drop_down ``options`` map was placeholder data (fake option IDs like
# "opt-req-plumbing", and labels for properties/units that don't exist). The
# agent passed those straight through to ClickUp, which rejected every
# drop_down write with HTTP 400 FIELD_011 "Value must be an option index or
# uuid". Fetching from the API instead means the option UUIDs are always real
# and survive anyone editing the field options in the ClickUp UI.

MAINTENANCE_FIELD_CACHE_TTL_SECONDS = 300
_maintenance_fields_cache: tuple[float, str] | None = None


def _format_custom_fields(fields: list[dict]) -> str:
    """Render ClickUp's custom-field payload as an agent-readable reference."""
    lines: list[str] = [
        f"Maintenance list ID: {CLICKUP_MAINTENANCE_LIST_ID}",
        "",
    ]
    for field in fields:
        lines.append(
            f"**{field.get('name', '(unnamed)')}** "
            f"(id: {field.get('id')}, type: {field.get('type')})"
        )
        options = (field.get("type_config") or {}).get("options") or []
        for option in options:
            # drop_down options carry "name"; labels-type fields carry "label".
            label = option.get("name") or option.get("label") or "(unnamed)"
            lines.append(f"  - {label} → {option.get('id')}")
        lines.append("")
    return "\n".join(lines)


async def get_maintenance_field_options_core() -> str:
    """Return the maintenance list's custom-field IDs and dropdown UUIDs.

    Fetched live from ClickUp and cached briefly. Pairs with
    ``clickup_create_task`` / ``clickup_set_task_custom_field``: drop_down
    values must be the option UUID, not the human label.
    """
    global _maintenance_fields_cache

    if _maintenance_fields_cache is not None:
        cached_at, cached_text = _maintenance_fields_cache
        if time.monotonic() - cached_at < MAINTENANCE_FIELD_CACHE_TTL_SECONDS:
            return cached_text

    resp = await clickup_request("GET", f"/list/{CLICKUP_MAINTENANCE_LIST_ID}/field")
    if resp.status_code != 200:
        # Don't cache failures — the next call should retry.
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code} fetching custom fields: {resp.text[:200]}"
        )

    rendered = _format_custom_fields(resp.json().get("fields", []))
    _maintenance_fields_cache = (time.monotonic(), rendered)
    return rendered
