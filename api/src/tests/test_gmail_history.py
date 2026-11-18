"""
Unit tests for Gmail history fetching (``get_email_changes``) and Gmail service
construction (``get_gmail_service``).

These mock the Gmail API entirely — no credentials or network required, so they
run in the default (non-live) suite.

Context: a daily Logfire triage once misread the benign, high-volume "no history
found after N retries" outcome as an outage. That path is benign (a watch fires
but ``history.list`` has no in-scope changes) and must not be logged as a
failure. Genuine transport errors (timeouts) retain the checkpoint for retry. These tests
pin both behaviors, plus the explicit httplib2 timeout that prevents a stalled
connection from hanging indefinitely.
"""

from unittest.mock import MagicMock, patch

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
        "historyId": "999",
        "history": [
            {"id": "124", "messages": [{"id": "m1"}, {"id": "m2"}]},
            {"id": "125", "messagesAdded": [{"message": {"id": "m3"}}]},
        ],
    }

    result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "success"
    assert set(result["email_message_ids"]) == {"m1", "m2", "m3"}
    assert result["added_message_ids"] == ["m3"]


@pytest.mark.asyncio
async def test_get_email_changes_no_history_is_benign_not_failure():
    """Successful empty history is benign and immediately acknowledged, without polling."""
    mock_service = MagicMock()
    # A successful response may omit history when there are no changes.
    mock_service.users().history().list().execute.return_value = {"historyId": "999"}

    result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "no_messages"
    assert result["email_message_ids"] == []
    assert result["checkpoint"] == "999"
    mock_service.users().history().list().execute.assert_called_once()
    # Guard against regressing to the old alarming wording that fooled triage.
    assert "Failed to retrieve history" not in result["reason"]


@pytest.mark.asyncio
async def test_get_email_changes_timeout_is_retry_needed_exception():
    """A genuine transport error surfaces as retry_needed with an exception reason."""
    mock_service = MagicMock()
    mock_service.users().history().list().execute.side_effect = TimeoutError("timed out")

    result = await get_email_changes(mock_service, history_id="123")

    assert result["status"] == "retry_needed"
    assert result["retry_reason"] == "gmail_history_failed"
    assert "checkpoint" not in result
    mock_service.users().history().list().execute.assert_called_once()


def test_get_gmail_service_sets_explicit_timeout():
    """The Gmail transport must be built with an explicit httplib2 timeout so a
    stalled connection fails fast instead of hanging indefinitely."""
    fake_http = MagicMock()
    with (
        patch("api.src.google.gmail.service.httplib2.Http", return_value=fake_http) as mock_http,
        patch("api.src.google.gmail.service.google_auth_httplib2.AuthorizedHttp") as mock_authed,
        patch("api.src.google.gmail.service.build") as mock_build,
    ):
        get_gmail_service(MagicMock())

    mock_http.assert_called_once_with(timeout=GMAIL_HTTP_TIMEOUT_SECONDS)
    # build() must use the authorized http (not the credentials= path) so the
    # timeout actually takes effect.
    assert mock_build.call_args.kwargs.get("http") is mock_authed.return_value
