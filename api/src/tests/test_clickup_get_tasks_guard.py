"""
Unit tests for the ``get_tasks`` ClickUp ID guard.

Regression coverage for the "Error-level records (non-local)" Logfire alert,
which fired on 2026-10-14 when the agent called ``clickup_get_tasks`` with the
view *name* ``"MaintenanceRequest"``. That produced
``GET /view/MaintenanceRequest/task`` → 404, which httpx instrumentation
records at error level. No API key or network access is needed here.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.src.sernia_ai.config import CLICKUP_MAINTENANCE_LIST_ID, DEFAULT_CLICKUP_VIEW_ID
from api.src.sernia_ai.tools.clickup_tools import _looks_like_clickup_id, get_tasks

# ---------------------------------------------------------------------------
# _looks_like_clickup_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        DEFAULT_CLICKUP_VIEW_ID,  # "2ky3xg85-573" — real view ID
        CLICKUP_MAINTENANCE_LIST_ID,  # "901312027371" — real list ID
        "901312027371",
        "2ky3xg85-573",
        "6-901312027371-1",
        "2kz7ht1",
    ],
)
def test_real_ids_are_accepted(value):
    """The guard must never refuse a shape a real ClickUp ID can take."""
    assert _looks_like_clickup_id(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "MaintenanceRequest",  # the name that fired the alert
        "Maintenance Request 2",  # whitespace is not valid in an ID
        "maintenance-requests",  # hyphenated name, no digit
        "Peppino View",
        "",
    ],
)
def test_names_are_rejected(value):
    assert _looks_like_clickup_id(value) is False


# ---------------------------------------------------------------------------
# get_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tasks_rejects_a_name_without_calling_clickup():
    """A guessed name returns guidance and makes no HTTP request.

    The no-request half is the point: the 404 is what paged the alert.
    """
    with patch(
        "api.src.sernia_ai.tools.clickup_tools._clickup_request",
        new=AsyncMock(),
    ) as mock_request:
        result = await get_tasks(None, "MaintenanceRequest")

    mock_request.assert_not_called()
    assert "not a ClickUp ID" in result
    assert "list_clickup_lists" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_id", "expected_path"),
    [
        (CLICKUP_MAINTENANCE_LIST_ID, f"/list/{CLICKUP_MAINTENANCE_LIST_ID}/task"),
        (DEFAULT_CLICKUP_VIEW_ID, f"/view/{DEFAULT_CLICKUP_VIEW_ID}/task"),
        (None, f"/view/{DEFAULT_CLICKUP_VIEW_ID}/task"),
    ],
)
async def test_get_tasks_still_routes_real_ids(target_id, expected_path):
    """The guard must not disturb list vs view routing, or the default."""
    response = AsyncMock()
    response.status_code = 200
    response.json = lambda: {"tasks": []}

    with patch(
        "api.src.sernia_ai.tools.clickup_tools._clickup_request",
        new=AsyncMock(return_value=response),
    ) as mock_request:
        result = await get_tasks(None, target_id)

    mock_request.assert_awaited_once_with("GET", expected_path)
    assert result == "No tasks found."
