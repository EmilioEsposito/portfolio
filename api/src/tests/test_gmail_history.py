"""
Unit tests for Gmail history fetching (``get_email_changes``) and Gmail service
construction (``get_gmail_service``).

These mock the Gmail API entirely — no credentials or network required, so they
run in the default (non-live) suite.

Context: a daily Logfire triage once misread the benign, high-volume "no history
found after N retries" outcome as an outage. That path is benign (a watch fires
but ``history.list`` has no in-scope changes) and must not be logged as a
failure. Genuine transport errors (timeouts) are the alertable case. These tests
pin both behaviors, plus the explicit httplib2 timeout that prevents a stalled
connection from hanging indefinitely.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.src.google.gmail.service import (
    GMAIL_HTTP_TIMEOUT_SECONDS,
    get_email_changes,
    get_gmail_service,
)


@pytest.mark.asyncio
async def test_get_email_changes_success_returns_message_ids():
    """When history.list returns history with messages, status is success."""
    mock_service = MagicMock()
    mock_service.users().history().list().execute.return_value = {
        "history": [
            {"messages": [{"id": "m1"}, {"id": "m2"}]},
            {"messagesAdded": [{"message": {"id": "m3"}}]},
        ]
    }

    result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "success"
    assert set(result["email_message_ids"]) == {"m1", "m2", "m3"}
    assert result["added_message_ids"] == ["m3"]


@pytest.mark.asyncio
async def test_get_email_changes_no_history_is_benign_not_failure():
    """
    A watch notification that yields no in-scope history must exhaust the retry
    loop and return retry_needed with a *benign* reason — never a "failure"
    string. Sleeps are patched so the exponential backoff doesn't actually wait.
    """
    mock_service = MagicMock()
    # No "history" key on every attempt → the benign "no history found" path.
    mock_service.users().history().list().execute.return_value = {"historyId": "999"}

    with patch("api.src.google.gmail.service.asyncio.sleep", new=AsyncMock()):
        result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "retry_needed"
    assert result["email_message_ids"] == []
    assert "No retrievable history" in result["reason"]
    # Guard against regressing to the old alarming wording that fooled triage.
    assert "Failed to retrieve history" not in result["reason"]


@pytest.mark.asyncio
async def test_get_email_changes_timeout_is_retry_needed_exception():
    """A genuine transport error surfaces as retry_needed with an exception reason."""
    mock_service = MagicMock()
    mock_service.users().history().list().execute.side_effect = TimeoutError("timed out")

    with patch("api.src.google.gmail.service.asyncio.sleep", new=AsyncMock()):
        result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "retry_needed"
    assert "Exception fetching history" in result["reason"]


def test_get_gmail_service_sets_explicit_timeout():
    """The Gmail transport must be built with an explicit httplib2 timeout so a
    stalled connection fails fast instead of hanging indefinitely."""
    fake_http = MagicMock()
    with patch("api.src.google.gmail.service.httplib2.Http", return_value=fake_http) as mock_http, \
         patch("api.src.google.gmail.service.google_auth_httplib2.AuthorizedHttp") as mock_authed, \
         patch("api.src.google.gmail.service.build") as mock_build:
        get_gmail_service(MagicMock())

    mock_http.assert_called_once_with(timeout=GMAIL_HTTP_TIMEOUT_SECONDS)
    # build() must use the authorized http (not the credentials= path) so the
    # timeout actually takes effect.
    assert mock_build.call_args.kwargs.get("http") is mock_authed.return_value
