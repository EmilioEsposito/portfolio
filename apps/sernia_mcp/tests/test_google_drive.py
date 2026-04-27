"""Unit tests for ``core.google.drive``.

Mocks the ``googleapiclient.discovery.build``-returned service, so these
run without API keys. Pins the contract sernia_ai had: invalid sheet
name falls back to listing available sheet names; large sheets are
capped; empty sheets return a clear message.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# search_drive_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_drive_returns_formatted_listing():
    fake_service = MagicMock()
    fake_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "abc123",
                "name": "Tenants 2026.gsheet",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-04-20T10:00:00Z",
                "webViewLink": "https://docs.google.com/spreadsheets/abc123",
            },
        ]
    }
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import search_drive_core

        out = await search_drive_core(
            "Tenants", user_email="agent@serniacapital.com"
        )

    assert "Tenants 2026.gsheet" in out
    assert "Google Sheet" in out
    assert "abc123" in out


@pytest.mark.asyncio
async def test_search_drive_handles_empty_results():
    fake_service = MagicMock()
    fake_service.files().list().execute.return_value = {"files": []}
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import search_drive_core

        out = await search_drive_core(
            "no-such-thing", user_email="agent@serniacapital.com"
        )

    assert "No Drive files found" in out


# ---------------------------------------------------------------------------
# read_google_sheet_core
# ---------------------------------------------------------------------------


def _mock_sheets_service(values_response: dict, meta_response: dict | None = None):
    """Build a fake sheets service mock with chained-method support.

    The real Google client uses ``service.spreadsheets().values().get(...).execute()``.
    Tests need each link in the chain to be the same mock so we can wire a
    single ``execute`` return value per call.
    """
    service = MagicMock()
    values_get = service.spreadsheets().values().get
    values_get().execute.return_value = values_response
    if meta_response is not None:
        service.spreadsheets().get().execute.return_value = meta_response
    return service


@pytest.mark.asyncio
async def test_read_sheet_with_explicit_sheet_and_range():
    fake_service = _mock_sheets_service(
        values_response={
            "values": [
                ["Property", "Unit", "Tenant"],
                ["319", "01", "Anna"],
                ["319", "02", "Bob"],
            ]
        }
    )
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import read_google_sheet_core

        out = await read_google_sheet_core(
            "abc123",
            user_email="agent@serniacapital.com",
            sheet_name="Roster",
            range="A1:C3",
        )

    assert "Property | Unit | Tenant" in out
    assert "319 | 01 | Anna" in out
    # The values().get(...) call should have used the quoted A1 form
    fake_service.spreadsheets().values().get.assert_called_with(
        spreadsheetId="abc123", range="'Roster'!A1:C3"
    )


@pytest.mark.asyncio
async def test_read_sheet_defaults_to_first_sheet_when_no_args():
    """No sheet_name + no range → fetch metadata, pick first sheet's title."""
    fake_service = _mock_sheets_service(
        values_response={"values": [["A", "B"], ["1", "2"]]},
        meta_response={"sheets": [{"properties": {"title": "Main"}}]},
    )
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import read_google_sheet_core

        out = await read_google_sheet_core(
            "abc123", user_email="agent@serniacapital.com"
        )

    assert "A | B" in out
    fake_service.spreadsheets().values().get.assert_called_with(
        spreadsheetId="abc123", range="Main"
    )


@pytest.mark.asyncio
async def test_read_sheet_invalid_range_returns_available_sheet_names():
    """The fallback is load-bearing: the agent guesses sheet names, and a 400
    response with a list of valid names lets it retry without our help.
    """
    from googleapiclient.errors import HttpError

    fake_resp = MagicMock()
    fake_resp.status = 400
    err = HttpError(resp=fake_resp, content=b'{"error":{"message":"Bad range"}}')
    err._get_reason = lambda: "Unable to parse range"

    fake_service = MagicMock()
    fake_service.spreadsheets().values().get().execute.side_effect = err
    fake_service.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"title": "Tenants"}},
            {"properties": {"title": "Vendors"}},
        ]
    }
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import read_google_sheet_core

        out = await read_google_sheet_core(
            "abc123",
            user_email="agent@serniacapital.com",
            sheet_name="Wrong Name",
        )

    assert "Available sheets" in out
    assert "Tenants" in out
    assert "Vendors" in out


@pytest.mark.asyncio
async def test_read_sheet_caps_at_100_data_rows():
    headers = ["Col"]
    data = [[str(i)] for i in range(250)]
    fake_service = _mock_sheets_service(values_response={"values": [headers] + data})
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import read_google_sheet_core

        out = await read_google_sheet_core(
            "abc123",
            user_email="agent@serniacapital.com",
            sheet_name="x",
            range="A1:A300",
        )

    assert "showing 100 of 250 data rows" in out


@pytest.mark.asyncio
async def test_read_sheet_empty_returns_message():
    fake_service = _mock_sheets_service(values_response={"values": []})
    with patch(
        "sernia_mcp.core.google.drive.build", return_value=fake_service
    ), patch(
        "sernia_mcp.core.google.drive.get_delegated_credentials",
        return_value=MagicMock(),
    ):
        from sernia_mcp.core.google.drive import read_google_sheet_core

        out = await read_google_sheet_core(
            "abc123",
            user_email="agent@serniacapital.com",
            sheet_name="Empty",
        )

    assert out == "Sheet is empty."
