"""Read-only ClickUp tools — list browsing, list/view tasks, custom-field reference.

Lifted from ``api/src/sernia_ai/tools/clickup_tools.py``. The maintenance
custom-field map is hardcoded here (same values as sernia_ai); if Quo or
ClickUp ever rotates these UUIDs, both copies need updating until the
sernia_ai → sernia_mcp migration completes (see ``apps/sernia_mcp/TODOS.md``).
"""
from __future__ import annotations

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
                    lines.append(
                        f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})"
                    )

        resp_lists = await clickup_request("GET", f"/space/{space_id}/list")
        if resp_lists.status_code == 200:
            folderless = resp_lists.json().get("lists", [])
            if folderless:
                lines.append("  📁 (no folder)")
                for lst in folderless:
                    task_count = lst.get("task_count", "?")
                    lines.append(
                        f"    - {lst['name']} (id: {lst['id']}, tasks: {task_count})"
                    )

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
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code}: {resp.text[:200]}"
        )

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
# Maintenance custom fields — hardcoded UUID reference
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


async def get_maintenance_field_options_core() -> str:
    """Return the maintenance list's custom-field IDs and dropdown UUIDs.

    Pure formatter — no API call. Pairs with ``clickup_create_task`` /
    ``clickup_set_task_custom_field``: drop_down values must be the option
    UUID, not the human label.
    """
    lines: list[str] = [
        f"Maintenance list ID: {CLICKUP_MAINTENANCE_LIST_ID}",
        "",
    ]
    for field_name, field_def in MAINTENANCE_CUSTOM_FIELDS.items():
        lines.append(
            f"**{field_name}** (id: {field_def['id']}, type: {field_def['type']})"
        )
        options = field_def.get("options")
        if options:
            for label, uuid_val in options.items():
                lines.append(f"  - {label} → {uuid_val}")
        lines.append("")
    return "\n".join(lines)
