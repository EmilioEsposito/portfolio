"""Unit tests for ``core.clickup.writes`` — create / update / set custom field.

Patches ``clickup_request`` (the shared httpx caller) so each test pins the
exact request shape sent to ClickUp's v2 API. No network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sernia_mcp.core.errors import ExternalServiceError, ValidationError


def _ok_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


def _err_response(status: int = 500, text: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.side_effect = AssertionError("should not be parsed on failure")
    return resp


# ---------------------------------------------------------------------------
# create_task_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_minimal():
    fake = AsyncMock(
        return_value=_ok_response(
            {
                "id": "abc",
                "name": "Fix sink",
                "url": "https://app.clickup.com/t/abc",
            },
            status=201,
        )
    )
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import create_task_core

        result = await create_task_core("L1", "Fix sink")

    assert "Fix sink" in result and "abc" in result
    fake.assert_awaited_once_with("POST", "/list/L1/task", json={"name": "Fix sink"})


@pytest.mark.asyncio
async def test_create_task_includes_optional_fields_and_due_date():
    fake = AsyncMock(
        return_value=_ok_response({"id": "x", "name": "Task", "url": "u"})
    )
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import create_task_core

        await create_task_core(
            "L2",
            "Task",
            description="Markdown body",
            status="in progress",
            priority=2,
            due_date="2026-04-30",
            custom_fields=[{"id": "cf1", "value": "v"}],
        )

    sent = fake.await_args.kwargs["json"]
    assert sent["name"] == "Task"
    assert sent["description"] == "Markdown body"
    assert sent["status"] == "in progress"
    assert sent["priority"] == 2
    assert isinstance(sent["due_date"], int)  # ms-since-epoch
    assert sent["custom_fields"] == [{"id": "cf1", "value": "v"}]


@pytest.mark.asyncio
async def test_create_task_invalid_due_date_raises_validation():
    """Bad ISO strings shouldn't reach ClickUp — fail fast with ValidationError."""
    with patch(
        "sernia_mcp.core.clickup.writes.clickup_request", AsyncMock()
    ) as fake:
        from sernia_mcp.core.clickup.writes import create_task_core

        with pytest.raises(ValidationError, match="ISO 8601"):
            await create_task_core("L1", "x", due_date="next Tuesday")
        fake.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_http_failure_raises_external_service():
    fake = AsyncMock(return_value=_err_response(500, "Internal"))
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import create_task_core

        with pytest.raises(ExternalServiceError, match="HTTP 500"):
            await create_task_core("L1", "x")


# ---------------------------------------------------------------------------
# update_task_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_with_all_fields():
    fake = AsyncMock(
        return_value=_ok_response(
            {"id": "T1", "name": "Renamed", "url": "https://x"}
        )
    )
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import update_task_core

        result = await update_task_core(
            "T1",
            name="Renamed",
            description="d",
            status="done",
            priority=4,
            due_date="2026-05-01",
        )

    assert "Renamed" in result
    sent = fake.await_args.kwargs["json"]
    assert set(sent.keys()) == {"name", "description", "status", "priority", "due_date"}
    assert isinstance(sent["due_date"], int)
    fake.assert_awaited_once()
    args, _ = fake.await_args
    assert args[0] == "PUT"
    assert args[1] == "/task/T1"


@pytest.mark.asyncio
async def test_update_task_due_date_empty_string_clears():
    """Per sernia_ai contract: empty string clears the due date (sends None)."""
    fake = AsyncMock(
        return_value=_ok_response({"id": "T1", "name": "x", "url": "u"})
    )
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import update_task_core

        await update_task_core("T1", due_date="")

    assert fake.await_args.kwargs["json"] == {"due_date": None}


@pytest.mark.asyncio
async def test_update_task_no_fields_returns_message_without_calling_api():
    fake = AsyncMock()
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import update_task_core

        result = await update_task_core("T1")

    assert result == "No fields to update."
    fake.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_task_custom_field_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_custom_field_sends_post_with_value():
    fake = AsyncMock(return_value=_ok_response({"ok": True}))
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import set_task_custom_field_core

        result = await set_task_custom_field_core("T1", "field-uuid", "opt-uuid")

    assert "Custom field field-uuid set on task T1." in result
    fake.assert_awaited_once_with(
        "POST", "/task/T1/field/field-uuid", json={"value": "opt-uuid"}
    )


@pytest.mark.asyncio
async def test_set_custom_field_http_failure_raises():
    fake = AsyncMock(return_value=_err_response(403, "no perms"))
    with patch("sernia_mcp.core.clickup.writes.clickup_request", fake):
        from sernia_mcp.core.clickup.writes import set_task_custom_field_core

        with pytest.raises(ExternalServiceError, match="HTTP 403"):
            await set_task_custom_field_core("T1", "f", "v")
