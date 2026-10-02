"""Gmail lookup failures must abort the save and leave Pub/Sub free to retry."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from asyncpg.exceptions import ConnectionDoesNotExistError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.src.google.gmail.db_ops import get_email_by_message_id, save_email_message
from api.src.google.pubsub import routes


@pytest.fixture
def disconnected():
    return DBAPIError(
        "SELECT email_messages",
        {},
        ConnectionDoesNotExistError("connection was closed in the middle of operation"),
        connection_invalidated=True,
    )


@pytest.fixture
def message_data():
    return {
        "message_id": "gmail-transaction-test",
        "thread_id": "thread-test",
        "subject": "Test email",
        "from_address": "sender@example.com",
        "to_address": "recipient@example.com",
        "date": "2026-10-02T18:00:00+00:00",
        "body_text": "Test body",
        "body_html": "<p>Test body</p>",
        "raw_payload": {},
    }


@pytest.mark.asyncio
async def test_lookup_returns_none_only_when_row_is_missing(disconnected):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.side_effect = [result, disconnected]

    assert await get_email_by_message_id(session, "missing") is None
    with pytest.raises(DBAPIError) as caught:
        await get_email_by_message_id(session, "unavailable")
    assert caught.value is disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_lookup_failure_aborts_save_and_rolls_back(disconnected, message_data, after_commit):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.side_effect = [result, Mock(), disconnected] if after_commit else [disconnected]

    assert await save_email_message(session, message_data, history_id=123) == (None, False)

    session.rollback.assert_awaited_once()
    # A failed initial SELECT must never proceed to the INSERT. A failed
    # post-commit read must report failure rather than claim a successful save.
    assert session.execute.await_count == (3 if after_commit else 1)
    assert session.commit.await_count == (1 if after_commit else 0)


@pytest.mark.asyncio
async def test_disconnect_requests_redelivery_then_recovers(
    monkeypatch, disconnected, message_data
):
    failed_session = AsyncMock(spec=AsyncSession)
    failed_session.execute.side_effect = disconnected
    recovered_session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = Mock()  # Previously stored email.
    recovered_session.execute.side_effect = [result, Mock(), result]
    sessions = iter([failed_session, recovered_session])

    @asynccontextmanager
    async def session_context():
        yield next(sessions)

    monkeypatch.setattr(routes, "session_context", session_context)
    monkeypatch.setattr(routes, "verify_pubsub_token", AsyncMock())
    monkeypatch.setattr(
        routes,
        "decode_pubsub_message",
        Mock(return_value={"emailAddress": "recipient@example.com", "historyId": 123}),
    )
    monkeypatch.setattr(routes, "get_delegated_credentials", Mock())
    monkeypatch.setattr(routes, "get_gmail_service", Mock())
    monkeypatch.setattr(
        routes,
        "get_email_changes",
        AsyncMock(
            return_value={"status": "success", "email_message_ids": [message_data["message_id"]]}
        ),
    )
    monkeypatch.setattr(routes, "get_email_content", AsyncMock(return_value={"id": "test"}))
    monkeypatch.setattr(routes, "process_single_message", AsyncMock(return_value=message_data))
    request = Mock(spec=Request)
    request.headers = {}
    request.body = AsyncMock(return_value=b'{"message":{"data":"test"}}')
    request.json = AsyncMock(return_value={"message": {"data": "test"}})

    failed_response = await routes.handle_gmail_notifications(request)

    assert failed_response.status_code == 429
    assert message_data["message_id"].encode() in failed_response.body
    failed_session.rollback.assert_awaited_once()
    failed_session.execute.assert_awaited_once()
    failed_session.commit.assert_not_awaited()

    recovered_response = await routes.handle_gmail_notifications(request)

    assert recovered_response.status_code == 204
    recovered_session.commit.assert_awaited_once()
    recovered_session.rollback.assert_not_awaited()
