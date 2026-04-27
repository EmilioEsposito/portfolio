"""ClickUp task write tools — create, update, set custom field.

Lifted from ``api/src/sernia_ai/tools/clickup_tools.py``. These do NOT
require HITL approval in sernia_ai (only ``delete_task`` does), so the
MCP wrappers expose them directly. Per the auth model, both Clerk-OAuth
human callers AND internal-bearer service callers may invoke these.
"""
from __future__ import annotations

from datetime import datetime

from sernia_mcp.core.clickup._client import clickup_request
from sernia_mcp.core.errors import ExternalServiceError, ValidationError


def _due_date_to_ms(due_date: str) -> int:
    """Convert an ISO date string to ClickUp's required ms-since-epoch."""
    try:
        dt = datetime.fromisoformat(due_date)
    except ValueError as exc:
        raise ValidationError(
            f"due_date must be ISO 8601 (e.g. '2026-04-30' or "
            f"'2026-04-30T17:00:00'); got {due_date!r}"
        ) from exc
    return int(dt.timestamp() * 1000)


async def create_task_core(
    list_id: str,
    name: str,
    *,
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
        status: Optional status string.
        priority: Optional 1-4 (1=urgent, 2=high, 3=normal, 4=low).
        due_date: Optional ISO date or datetime.
        custom_fields: Optional ``[{"id": "<uuid>", "value": ...}]`` list.
    """
    body: dict = {"name": name}
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if due_date is not None:
        body["due_date"] = _due_date_to_ms(due_date)
    if custom_fields is not None:
        body["custom_fields"] = custom_fields

    resp = await clickup_request("POST", f"/list/{list_id}/task", json=body)
    if resp.status_code not in (200, 201):
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    return (
        f"Task created: {data.get('name')} (id: {data.get('id')})\n"
        f"URL: {data.get('url', 'N/A')}"
    )


async def update_task_core(
    task_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
) -> str:
    """Update an existing ClickUp task.

    Pass empty string for ``due_date`` to CLEAR the due date. Pass None to
    leave it unchanged.
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
        body["due_date"] = None if due_date == "" else _due_date_to_ms(due_date)

    if not body:
        return "No fields to update."

    resp = await clickup_request("PUT", f"/task/{task_id}", json=body)
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    return (
        f"Task updated: {data.get('name')} (id: {data.get('id')})\n"
        f"URL: {data.get('url', 'N/A')}"
    )


async def set_task_custom_field_core(
    task_id: str,
    field_id: str,
    value: str | int | float | bool | dict | list | None,
) -> str:
    """Set or update a single custom field on an existing ClickUp task.

    For drop-down fields, ``value`` must be the option UUID, not the label.
    """
    resp = await clickup_request(
        "POST", f"/task/{task_id}/field/{field_id}", json={"value": value}
    )
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"ClickUp API HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return f"Custom field {field_id} set on task {task_id}."
