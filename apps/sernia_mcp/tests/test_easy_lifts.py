"""Unit tests for the easy-lift batch.

Covers:
- ``read_google_doc_core``       — Docs API read
- ``read_drive_pdf_core``        — Drive PDF download + pypdf extract
- ``read_email_thread_core``     — Gmail thread read with truncation
- ``list_calendar_events_core``  — Calendar API list
- ``list_clickup_lists_core``    — ClickUp workspace browse
- ``get_tasks_core``             — ClickUp list/view tasks
- ``get_maintenance_field_options_core`` — ClickUp custom-field reference

All Google calls patch the discovery-built service mock; ClickUp calls
patch the shared ``clickup_request`` helper. No network.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# read_google_doc_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_google_doc_extracts_text_from_paragraph_runs():
    fake_service = MagicMock()
    fake_service.documents().get().execute.return_value = {
        "title": "Lease Renewal Plan",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Hello "}},
                            {"textRun": {"content": "world.\n"}},
                        ]
                    }
                },
            ]
        },
    }
    with (
        patch("sernia_mcp.core.google.drive.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.drive.get_delegated_credentials",
            return_value=MagicMock(),
        ),
    ):
        from sernia_mcp.core.google.drive import read_google_doc_core

        out = await read_google_doc_core("docID", user_email="x@s.com")

    assert "Title: Lease Renewal Plan" in out
    assert "Hello world." in out


@pytest.mark.asyncio
async def test_read_google_doc_empty_returns_friendly_message():
    fake_service = MagicMock()
    fake_service.documents().get().execute.return_value = {
        "title": "Blank",
        "body": {"content": []},
    }
    with (
        patch("sernia_mcp.core.google.drive.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.drive.get_delegated_credentials",
            return_value=MagicMock(),
        ),
    ):
        from sernia_mcp.core.google.drive import read_google_doc_core

        out = await read_google_doc_core("docID", user_email="x@s.com")

    assert "(empty document)" in out


# ---------------------------------------------------------------------------
# read_drive_pdf_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_pdf_delegates_to_doc_when_mime_is_google_doc():
    """Drive search may surface a Google Doc by ID even if user said "PDF" —
    the core function should transparently fall through to the doc reader.
    """
    fake_service = MagicMock()
    fake_service.files().get().execute.return_value = {
        "name": "Mistake.docx",
        "mimeType": "application/vnd.google-apps.document",
    }
    with (
        patch("sernia_mcp.core.google.drive.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.drive.get_delegated_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sernia_mcp.core.google.drive.read_google_doc_core",
            new=AsyncMock(return_value="<doc body>"),
        ) as fake_doc_read,
    ):
        from sernia_mcp.core.google.drive import read_drive_pdf_core

        out = await read_drive_pdf_core("file1", user_email="x@s.com")

    assert out == "<doc body>"
    fake_doc_read.assert_awaited_once_with("file1", user_email="x@s.com")


@pytest.mark.asyncio
async def test_read_pdf_extracts_text_via_pypdf():
    fake_service = MagicMock()
    fake_service.files().get().execute.return_value = {
        "name": "lease.pdf",
        "mimeType": "application/pdf",
    }
    fake_service.files().get_media.return_value = MagicMock()

    fake_downloader_cls = MagicMock()
    fake_downloader = MagicMock()
    fake_downloader.next_chunk.return_value = (None, True)
    fake_downloader_cls.return_value = fake_downloader

    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Page 1 text."
    fake_reader = MagicMock(pages=[fake_page])
    fake_pypdf = MagicMock(PdfReader=MagicMock(return_value=fake_reader))

    with (
        patch("sernia_mcp.core.google.drive.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.drive.get_delegated_credentials",
            return_value=MagicMock(),
        ),
        patch("sernia_mcp.core.google.drive.MediaIoBaseDownload", fake_downloader_cls),
        patch.dict("sys.modules", {"pypdf": fake_pypdf}),
    ):
        from sernia_mcp.core.google.drive import read_drive_pdf_core

        out = await read_drive_pdf_core("pdf1", user_email="x@s.com")

    assert "PDF: lease.pdf" in out
    assert "Page 1 text." in out


@pytest.mark.asyncio
async def test_read_pdf_image_only_returns_no_text_notice():
    fake_service = MagicMock()
    fake_service.files().get().execute.return_value = {
        "name": "scan.pdf",
        "mimeType": "application/pdf",
    }
    fake_downloader = MagicMock()
    fake_downloader.next_chunk.return_value = (None, True)

    # Empty pages → pypdf returns "" for every page → "no extractable text"
    fake_page = MagicMock()
    fake_page.extract_text.return_value = ""
    fake_reader = MagicMock(pages=[fake_page])
    fake_pypdf = MagicMock(PdfReader=MagicMock(return_value=fake_reader))

    with (
        patch("sernia_mcp.core.google.drive.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.drive.get_delegated_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sernia_mcp.core.google.drive.MediaIoBaseDownload",
            return_value=fake_downloader,
        ),
        patch.dict("sys.modules", {"pypdf": fake_pypdf}),
    ):
        # Provide some bytes via the buffer so the byte-count line renders
        with patch(
            "sernia_mcp.core.google.drive.io.BytesIO",
            side_effect=[io.BytesIO(b"PDF" * 100), io.BytesIO(b"PDF" * 100)],
        ):
            from sernia_mcp.core.google.drive import read_drive_pdf_core

            out = await read_drive_pdf_core("pdf1", user_email="x@s.com")

    assert "image-based" in out
    assert "scan.pdf" in out


# ---------------------------------------------------------------------------
# read_email_thread_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_email_thread_renders_messages_in_order():
    fake_service = MagicMock()
    fake_service.users().threads().get().execute.return_value = {
        "messages": [
            {
                "id": "m1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "a@x.com"},
                        {"name": "To", "value": "b@x.com"},
                        {"name": "Date", "value": "Mon, 1 Apr 2026"},
                        {"name": "Subject", "value": "Hello"},
                    ]
                },
            },
            {
                "id": "m2",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "b@x.com"},
                        {"name": "Subject", "value": "Re: Hello"},
                    ]
                },
            },
        ]
    }
    with (
        patch("sernia_mcp.core.google.gmail.get_gmail_service", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.gmail.get_delegated_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sernia_mcp.core.google.gmail.extract_body",
            side_effect=[
                {"text": "First message body"},
                {"text": "Reply body"},
            ],
        ),
    ):
        from sernia_mcp.core.google.gmail import read_email_thread_core

        out = await read_email_thread_core("THR1", user_email="b@x.com")

    assert "Message 1/2" in out and "(ID: m1)" in out
    assert "Message 2/2" in out and "(ID: m2)" in out
    assert "First message body" in out
    assert "Reply body" in out


@pytest.mark.asyncio
async def test_read_email_thread_404_returns_helpful_message():
    from googleapiclient.errors import HttpError

    fake_resp = MagicMock(status=404)
    err = HttpError(resp=fake_resp, content=b'{"error":"not found"}')
    fake_service = MagicMock()
    fake_service.users().threads().get().execute.side_effect = err

    with (
        patch("sernia_mcp.core.google.gmail.get_gmail_service", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.gmail.get_delegated_credentials",
            return_value=MagicMock(),
        ),
    ):
        from sernia_mcp.core.google.gmail import read_email_thread_core

        out = await read_email_thread_core("missing", user_email="x@s.com")

    assert "not found in x@s.com" in out
    assert "mailbox-specific" in out


# ---------------------------------------------------------------------------
# list_calendar_events_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_calendar_events_renders_event_lines():
    fake_service = MagicMock()
    fake_service.events().list().execute.return_value = {
        "items": [
            {
                "id": "ev1",
                "summary": "Sernia weekly sync",
                "start": {"dateTime": "2026-04-30T10:00:00-04:00"},
                "end": {"dateTime": "2026-04-30T11:00:00-04:00"},
                "attendees": [{"email": "emilio@serniacapital.com"}],
            }
        ]
    }
    with (
        patch("sernia_mcp.core.google.calendar.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.calendar.get_delegated_credentials",
            return_value=MagicMock(),
        ),
    ):
        from sernia_mcp.core.google.calendar import list_calendar_events_core

        out = await list_calendar_events_core(user_email="x@s.com")

    assert "Sernia weekly sync" in out
    assert "ev1" in out
    assert "emilio@serniacapital.com" in out


@pytest.mark.asyncio
async def test_list_calendar_events_empty_message_includes_window():
    fake_service = MagicMock()
    fake_service.events().list().execute.return_value = {"items": []}
    with (
        patch("sernia_mcp.core.google.calendar.build", return_value=fake_service),
        patch(
            "sernia_mcp.core.google.calendar.get_delegated_credentials",
            return_value=MagicMock(),
        ),
    ):
        from sernia_mcp.core.google.calendar import list_calendar_events_core

        out = await list_calendar_events_core(user_email="x@s.com", days_ahead=14, days_behind=3)

    assert "next 14 days" in out
    assert "3 days back" in out


# ---------------------------------------------------------------------------
# ClickUp reads
# ---------------------------------------------------------------------------


def _ok_resp(payload: dict, status: int = 200) -> MagicMock:
    r = MagicMock(status_code=status)
    r.json.return_value = payload
    return r


@pytest.mark.asyncio
async def test_list_clickup_lists_renders_hierarchy():
    """Three sequential calls: spaces → folders → folderless lists."""
    fake = AsyncMock(
        side_effect=[
            _ok_resp({"spaces": [{"id": "S1", "name": "Sernia"}]}),
            _ok_resp(
                {
                    "folders": [
                        {
                            "name": "Tenant Ops",
                            "lists": [{"id": "L1", "name": "Maintenance", "task_count": 3}],
                        }
                    ]
                }
            ),
            _ok_resp({"lists": [{"id": "L2", "name": "Inbox", "task_count": 0}]}),
        ]
    )
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import list_clickup_lists_core

        out = await list_clickup_lists_core()

    assert "## Sernia" in out
    assert "Tenant Ops" in out
    assert "Maintenance (id: L1, tasks: 3)" in out
    assert "Inbox (id: L2, tasks: 0)" in out


@pytest.mark.asyncio
async def test_get_tasks_uses_list_endpoint_for_numeric_id():
    fake = AsyncMock(
        return_value=_ok_resp(
            {
                "tasks": [
                    {
                        "id": "T1",
                        "name": "Replace faucet",
                        "status": {"status": "in progress"},
                        "priority": {"priority": "high"},
                        "due_date": None,
                        "url": "https://app.clickup.com/t/T1",
                    }
                ]
            }
        )
    )
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import get_tasks_core

        out = await get_tasks_core("12345")

    assert "Replace faucet" in out
    args, _ = fake.await_args
    assert args == ("GET", "/list/12345/task")


@pytest.mark.asyncio
async def test_get_tasks_uses_view_endpoint_for_alphanumeric_id():
    fake = AsyncMock(return_value=_ok_resp({"tasks": []}))
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import get_tasks_core

        await get_tasks_core("ab12-xyz")

    args, _ = fake.await_args
    assert args == ("GET", "/view/ab12-xyz/task")


@pytest.mark.asyncio
async def test_get_tasks_defaults_to_configured_view_when_no_id():
    fake = AsyncMock(return_value=_ok_resp({"tasks": []}))
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import get_tasks_core

        await get_tasks_core()

    args, _ = fake.await_args
    # Default view ID has hyphens → /view path.
    assert args[1].startswith("/view/")


_MAINTENANCE_FIELDS_PAYLOAD = {
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


@pytest.fixture
def clear_maintenance_field_cache():
    """The formatted reference is module-cached — reset around each test."""
    import sernia_mcp.core.clickup.reads as reads

    reads._maintenance_fields_cache = None
    yield
    reads._maintenance_fields_cache = None


@pytest.mark.asyncio
async def test_get_maintenance_field_options_renders_live_options(clear_maintenance_field_cache):
    """Option UUIDs come from the ClickUp API, not a hardcoded table."""
    fake = AsyncMock(return_value=_ok_resp(_MAINTENANCE_FIELDS_PAYLOAD))
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import get_maintenance_field_options_core

        out = await get_maintenance_field_options_core()

    args, _ = fake.await_args
    assert args[0] == "GET"
    assert args[1].endswith("/field")

    assert "Property Address" in out
    assert "drop_down" in out
    # The real option UUID must be rendered next to its label.
    assert "320 S Mathilda St → b45526fd-f602-458d-9f51-620e837dec02" in out
    # Field IDs still appear so consumers can paste them as field IDs.
    assert "56c7f3d6-9cac-4e41-8be4-4c91b057fcfa" in out
    # A non-dropdown field renders with no options beneath it.
    assert "**Phone** (id: bf426280-c78e-41d7-a2a3-0683e0d597d6, type: phone)" in out


@pytest.mark.asyncio
async def test_get_maintenance_field_options_caches_between_calls(clear_maintenance_field_cache):
    fake = AsyncMock(return_value=_ok_resp(_MAINTENANCE_FIELDS_PAYLOAD))
    with patch("sernia_mcp.core.clickup.reads.clickup_request", fake):
        from sernia_mcp.core.clickup.reads import get_maintenance_field_options_core

        first = await get_maintenance_field_options_core()
        second = await get_maintenance_field_options_core()

    assert first == second
    assert fake.await_count == 1


@pytest.mark.asyncio
async def test_get_maintenance_field_options_does_not_cache_failures(
    clear_maintenance_field_cache,
):
    """A failed fetch must raise and leave the cache empty so the next call retries."""
    import sernia_mcp.core.clickup.reads as reads
    from sernia_mcp.core.errors import ExternalServiceError

    bad_resp = MagicMock(status_code=401, text="token invalid")
    bad = AsyncMock(return_value=bad_resp)
    with patch("sernia_mcp.core.clickup.reads.clickup_request", bad):
        with pytest.raises(ExternalServiceError):
            await reads.get_maintenance_field_options_core()

    assert reads._maintenance_fields_cache is None
