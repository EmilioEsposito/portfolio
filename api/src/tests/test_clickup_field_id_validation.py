"""Unit tests for local custom-field ID validation in the ClickUp tools.

Production saw the agent splice segments of two neighbouring field UUIDs
(e.g. Property Address ``56c7f3d6-9cac-...`` + Permission to Enter
``...-c006-4a84-9e90-4705ff13446a``) into an ID that doesn't exist, and
ClickUp rejected the write with HTTP 404 FIELD_058 / FIELD_343. The tools now
check IDs against the list's real fields before writing, and fail open when
that lookup itself fails.

No network: the shared ``_clickup_request`` helper is patched.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import api.src.sernia_ai.tools.clickup_tools as clickup_tools
from api.src.sernia_ai.tools.clickup_tools import create_task, set_task_custom_field

LIST_ID = "901312027371"
PROPERTY_FIELD = "56c7f3d6-9cac-4e41-8be4-4c91b057fcfa"
PERMISSION_FIELD = "a06fd20c-c006-4a84-9e90-4705ff13446a"
SPLICED_FIELD = "56c7f3d6-c006-4a84-9e90-4705ff13446a"

FIELDS_PAYLOAD = {
    "fields": [
        {"id": PROPERTY_FIELD, "name": "Property Address", "type": "drop_down"},
        {"id": PERMISSION_FIELD, "name": "Permission to Enter?", "type": "drop_down"},
    ]
}


def _resp(status_code: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = payload or {}
    resp.text = "error"
    return resp


@pytest.fixture(autouse=True)
def clear_field_id_cache():
    clickup_tools._list_field_ids_cache.clear()
    yield
    clickup_tools._list_field_ids_cache.clear()


class TestCreateTaskFieldValidation:
    @pytest.mark.asyncio
    async def test_unknown_field_id_is_rejected_without_post(self):
        mock = AsyncMock(return_value=_resp(200, FIELDS_PAYLOAD))
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await create_task(
                MagicMock(),
                list_id=LIST_ID,
                name="Task",
                custom_fields=[{"id": SPLICED_FIELD, "value": "x"}],
            )

        assert "Not sent to ClickUp" in out
        assert SPLICED_FIELD in out
        assert f"Property Address: {PROPERTY_FIELD}" in out
        assert [c.args[0] for c in mock.await_args_list] == ["GET"]

    @pytest.mark.asyncio
    async def test_valid_field_ids_are_posted(self):
        mock = AsyncMock(
            side_effect=[
                _resp(200, FIELDS_PAYLOAD),
                _resp(200, {"id": "t1", "name": "Task", "url": "u"}),
            ]
        )
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await create_task(
                MagicMock(),
                list_id=LIST_ID,
                name="Task",
                custom_fields=[{"id": PROPERTY_FIELD, "value": "opt"}],
            )

        assert out.startswith("Task created")
        post = mock.await_args_list[1]
        assert post.args == ("POST", f"/list/{LIST_ID}/task")
        assert post.kwargs["json"]["custom_fields"] == [{"id": PROPERTY_FIELD, "value": "opt"}]

    @pytest.mark.asyncio
    async def test_field_lookup_failure_fails_open(self):
        mock = AsyncMock(
            side_effect=[_resp(500), _resp(200, {"id": "t1", "name": "Task", "url": "u"})]
        )
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await create_task(
                MagicMock(),
                list_id=LIST_ID,
                name="Task",
                custom_fields=[{"id": SPLICED_FIELD, "value": "x"}],
            )

        assert out.startswith("Task created")

    @pytest.mark.asyncio
    async def test_no_custom_fields_skips_lookup(self):
        mock = AsyncMock(return_value=_resp(200, {"id": "t1", "name": "Task", "url": "u"}))
        with patch.object(clickup_tools, "_clickup_request", mock):
            await create_task(MagicMock(), list_id=LIST_ID, name="Task")

        assert mock.await_count == 1

    @pytest.mark.asyncio
    async def test_field_ids_are_cached_per_list(self):
        mock = AsyncMock(return_value=_resp(200, FIELDS_PAYLOAD))
        with patch.object(clickup_tools, "_clickup_request", mock):
            for _ in range(2):
                await create_task(
                    MagicMock(),
                    list_id=LIST_ID,
                    name="Task",
                    custom_fields=[{"id": SPLICED_FIELD, "value": "x"}],
                )

        assert mock.await_count == 1


class TestSetTaskCustomFieldValidation:
    @pytest.mark.asyncio
    async def test_unknown_field_id_is_rejected_without_post(self):
        mock = AsyncMock(
            side_effect=[_resp(200, {"list": {"id": LIST_ID}}), _resp(200, FIELDS_PAYLOAD)]
        )
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await set_task_custom_field(
                MagicMock(), task_id="t1", field_id=SPLICED_FIELD, value="opt"
            )

        assert "Not sent to ClickUp" in out
        assert all(c.args[0] == "GET" for c in mock.await_args_list)

    @pytest.mark.asyncio
    async def test_valid_field_id_is_posted(self):
        mock = AsyncMock(
            side_effect=[
                _resp(200, {"list": {"id": LIST_ID}}),
                _resp(200, FIELDS_PAYLOAD),
                _resp(200, {}),
            ]
        )
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await set_task_custom_field(
                MagicMock(), task_id="t1", field_id=PROPERTY_FIELD, value="opt"
            )

        assert out == f"Custom field {PROPERTY_FIELD} set on task t1."
        assert mock.await_args_list[-1].args == ("POST", f"/task/t1/field/{PROPERTY_FIELD}")

    @pytest.mark.asyncio
    async def test_task_lookup_error_fails_open(self):
        mock = AsyncMock(side_effect=[httpx.ConnectError("boom"), _resp(200, {})])
        with patch.object(clickup_tools, "_clickup_request", mock):
            out = await set_task_custom_field(
                MagicMock(), task_id="t1", field_id=SPLICED_FIELD, value="opt"
            )

        assert out.startswith("Custom field")
