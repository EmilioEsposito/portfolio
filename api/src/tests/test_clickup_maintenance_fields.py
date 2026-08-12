"""Unit tests for the ClickUp maintenance custom-field reference.

The maintenance field options used to be a hardcoded dict whose drop_down
option IDs were placeholder strings ("opt-req-plumbing", "a1b2c3d4-0001-...").
The agent read them from ``get_maintenance_field_options`` and passed them to
``set_task_custom_field``, so every drop_down write came back HTTP 400
``FIELD_011 Value must be an option index or uuid``. These tests pin the
replacement behavior: options are fetched from ClickUp, cached briefly, and
failures are not cached.

No network — the shared ``_clickup_request`` helper is patched.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.src.sernia_ai.tools.clickup_tools as clickup_tools
from api.src.sernia_ai.tools.clickup_tools import (
    _format_custom_fields,
    get_maintenance_field_options,
)

FIELDS_PAYLOAD = {
    "fields": [
        {
            "id": "56c7f3d6-9cac-4e41-8be4-4c91b057fcfa",
            "name": "Property Address",
            "type": "drop_down",
            "type_config": {
                "options": [
                    {"id": "b45526fd-f602-458d-9f51-620e837dec02", "name": "320 S Mathilda St"},
                    {"id": "a27ec554-e1e3-4819-838a-f34fe84d866c", "name": "324 S Mathilda St"},
                ]
            },
        },
        {
            "id": "bf426280-c78e-41d7-a2a3-0683e0d597d6",
            "name": "Phone",
            "type": "phone",
            "type_config": {},
        },
    ]
}


def _ok_resp(payload: dict) -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.json.return_value = payload
    return resp


@pytest.fixture(autouse=True)
def clear_field_cache():
    """The rendered reference is module-cached — reset around each test."""
    clickup_tools._maintenance_fields_cache = None
    yield
    clickup_tools._maintenance_fields_cache = None


class TestFormatCustomFields:
    def test_renders_label_to_option_uuid(self):
        out = _format_custom_fields(FIELDS_PAYLOAD["fields"])

        assert "320 S Mathilda St → b45526fd-f602-458d-9f51-620e837dec02" in out
        assert "324 S Mathilda St → a27ec554-e1e3-4819-838a-f34fe84d866c" in out

    def test_renders_field_id_and_type(self):
        out = _format_custom_fields(FIELDS_PAYLOAD["fields"])

        assert (
            "**Property Address** (id: 56c7f3d6-9cac-4e41-8be4-4c91b057fcfa, type: drop_down)"
            in out
        )
        assert "**Phone** (id: bf426280-c78e-41d7-a2a3-0683e0d597d6, type: phone)" in out

    def test_handles_labels_type_options(self):
        """`labels` fields carry "label" instead of "name"."""
        fields = [
            {
                "id": "f1",
                "name": "Tags",
                "type": "labels",
                "type_config": {"options": [{"id": "o1", "label": "Urgent"}]},
            }
        ]

        assert "Urgent → o1" in _format_custom_fields(fields)

    def test_tolerates_missing_type_config(self):
        fields = [{"id": "f1", "name": "Notes", "type": "text"}]

        assert "**Notes** (id: f1, type: text)" in _format_custom_fields(fields)


class TestGetMaintenanceFieldOptions:
    @pytest.mark.asyncio
    async def test_fetches_options_from_clickup(self):
        fake = AsyncMock(return_value=_ok_resp(FIELDS_PAYLOAD))
        with patch.object(clickup_tools, "_clickup_request", fake):
            out = await get_maintenance_field_options(MagicMock())

        args, _ = fake.await_args
        assert args[0] == "GET"
        assert args[1].endswith("/field")
        assert "320 S Mathilda St → b45526fd-f602-458d-9f51-620e837dec02" in out

    @pytest.mark.asyncio
    async def test_no_placeholder_option_ids_remain(self):
        """Regression guard for the FIELD_011 bug — the fabricated IDs are gone."""
        fake = AsyncMock(return_value=_ok_resp(FIELDS_PAYLOAD))
        with patch.object(clickup_tools, "_clickup_request", fake):
            out = await get_maintenance_field_options(MagicMock())

        assert "opt-req-" not in out
        assert "opt-pte-" not in out
        assert "a1b2c3d4-0001-4000-8000-" not in out

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        fake = AsyncMock(return_value=_ok_resp(FIELDS_PAYLOAD))
        with patch.object(clickup_tools, "_clickup_request", fake):
            first = await get_maintenance_field_options(MagicMock())
            second = await get_maintenance_field_options(MagicMock())

        assert first == second
        assert fake.await_count == 1

    @pytest.mark.asyncio
    async def test_failure_returns_error_string_and_is_not_cached(self):
        bad = AsyncMock(return_value=MagicMock(status_code=401, text="token invalid"))
        with patch.object(clickup_tools, "_clickup_request", bad):
            out = await get_maintenance_field_options(MagicMock())

        assert "ClickUp API error (HTTP 401)" in out
        assert clickup_tools._maintenance_fields_cache is None
