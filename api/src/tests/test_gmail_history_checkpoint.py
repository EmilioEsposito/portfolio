"""Checkpoint progression, empty history, pagination and failure safety."""

from unittest.mock import AsyncMock, Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from sqlalchemy import delete, select

from api.src.database.app_settings import AppSetting
from api.src.database.database import session_context
from api.src.google.gmail import checkpoint, service
from api.src.google.pubsub import routes


def fake_gmail(*responses):
    gmail = Mock()
    gmail.users.return_value.history.return_value.list.return_value.execute.side_effect = responses
    return gmail


@pytest.mark.asyncio
async def test_empty_success_is_immediate_and_has_checkpoint():
    gmail = fake_gmail({"historyId": "120"})
    result = await service.get_email_changes(gmail, "100")
    assert result["status"] == "no_messages"
    assert result["checkpoint"] == "120"
    gmail.users.return_value.history.return_value.list.return_value.execute.assert_called_once()


@pytest.mark.asyncio
async def test_page_checkpoint_never_jumps_to_mailbox_head():
    gmail = fake_gmail(
        {
            "historyId": "900",
            "nextPageToken": "next",
            "history": [
                {"id": "110", "messagesAdded": [{"message": {"id": "new"}}]},
                {"id": "120", "labelsRemoved": [{"message": {"id": "changed"}}]},
            ],
        }
    )
    result = await service.get_email_changes(gmail, "100")
    assert result["email_message_ids"] == ["changed", "new"]
    assert result["added_message_ids"] == ["new"]
    assert result["checkpoint"] == "120"
    assert result["has_more"] is True
    assert result["message_history"]["changed"][0]["history_id"] == "120"
    assert result["message_history"]["changed"][0]["event_types"] == ["labelsRemoved"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 500])
async def test_provider_failure_never_supplies_checkpoint(status):
    error = HttpError(httplib2.Response({"status": str(status)}), b'{"error":{"message":"failed"}}')
    result = await service.get_email_changes(fake_gmail(error), "100")
    assert result["status"] == "retry_needed"
    assert "checkpoint" not in result
    if status == 404:
        assert result["retry_reason"] == "gmail_history_expired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"historyId": "99"},
        {"historyId": "abc"},
        {"historyId": "200", "nextPageToken": "next"},
        {"historyId": "200", "nextPageToken": "next", "history": [{"id": "100"}]},
    ],
)
async def test_invalid_history_cannot_advance(response):
    result = await service.get_email_changes(fake_gmail(response), "100")
    assert result["status"] == "retry_needed"
    assert "checkpoint" not in result


@pytest.fixture
def notification():
    return {"emailAddress": routes.GMAIL_WATCH_MAILBOX, "historyId": "110"}


@pytest.mark.asyncio
async def test_uninitialized_cursor_does_not_infer_progress(monkeypatch, notification):
    monkeypatch.setattr(routes, "get_history_checkpoint", AsyncMock(return_value=None))
    process = AsyncMock()
    advance = AsyncMock()
    monkeypatch.setattr(routes, "_process_gmail_history", process)
    monkeypatch.setattr(routes, "advance_history_checkpoint", advance)
    result = await routes.process_gmail_notification(notification)
    assert result["retry_reason"] == "gmail_checkpoint_uninitialized"
    process.assert_not_awaited()
    advance.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["retry_needed", "partial_success_failure"])
async def test_failed_page_never_advances(monkeypatch, notification, status):
    monkeypatch.setattr(routes, "get_history_checkpoint", AsyncMock(return_value="100"))
    process = AsyncMock(return_value={"status": status, "messages": [], "checkpoint": "900"})
    advance = AsyncMock()
    monkeypatch.setattr(routes, "_process_gmail_history", process)
    monkeypatch.setattr(routes, "advance_history_checkpoint", advance)
    await routes.process_gmail_notification(notification)
    process.assert_awaited_once_with(notification, "100")
    advance.assert_not_awaited()


@pytest.mark.asyncio
async def test_redelivery_drains_next_page_even_after_notified_id(monkeypatch, notification):
    # Notification 110 remains unacked while we process pages through 200 and 300.
    cursor = "100"

    async def read():
        return cursor

    async def advance(expected, completed):
        nonlocal cursor
        assert cursor == expected
        cursor = completed

    monkeypatch.setattr(routes, "get_history_checkpoint", read)
    monkeypatch.setattr(routes, "advance_history_checkpoint", advance)
    monkeypatch.setattr(routes, "get_delegated_credentials", Mock())
    gmail = fake_gmail(
        {"historyId": "300", "nextPageToken": "next", "history": [{"id": "200"}]},
        {"historyId": "300"},
    )
    monkeypatch.setattr(routes, "get_gmail_service", Mock(return_value=gmail))
    result = await routes.process_gmail_notification(notification)
    assert result["retry_reason"] == "gmail_history_remaining"
    assert cursor == "200"
    result = await routes.process_gmail_notification(notification)
    assert result["status"] == "no_messages"
    assert cursor == "300"
    calls = gmail.users.return_value.history.return_value.list.call_args_list
    assert [c.kwargs["startHistoryId"] for c in calls] == ["100", "200"]


@pytest.mark.asyncio
async def test_checkpoint_commit_failure_is_not_acknowledged(monkeypatch, notification):
    monkeypatch.setattr(routes, "get_history_checkpoint", AsyncMock(return_value="100"))
    monkeypatch.setattr(
        routes,
        "_process_gmail_history",
        AsyncMock(
            return_value={
                "status": "no_messages",
                "checkpoint": "120",
                "has_more": False,
            }
        ),
    )
    monkeypatch.setattr(
        routes, "advance_history_checkpoint", AsyncMock(side_effect=RuntimeError("db down"))
    )
    with pytest.raises(RuntimeError, match="db down"):
        await routes.process_gmail_notification(notification)


@pytest.mark.asyncio
async def test_database_compare_and_swap_cannot_rewind_or_initialize(monkeypatch):
    # The worktree test wrapper supplies a disposable database; unique key avoids
    # touching operational settings or other tests' cursor state.
    key = "gmail_history_checkpoint:offline-checkpoint-test"
    monkeypatch.setattr(checkpoint, "CHECKPOINT_KEY", key)
    async with session_context() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == key))
        await session.commit()
    try:
        assert await checkpoint.get_history_checkpoint() is None
        await checkpoint.advance_history_checkpoint("100", "200")
        assert await checkpoint.get_history_checkpoint() is None
        async with session_context() as session:
            session.add(AppSetting(key=key, value={"history_id": "100"}))
            await session.commit()
        await checkpoint.advance_history_checkpoint("100", "200")
        await checkpoint.advance_history_checkpoint("100", "150")  # stale worker
        assert await checkpoint.get_history_checkpoint() == "200"
        with pytest.raises(ValueError):
            await checkpoint.advance_history_checkpoint("200", "199")
        async with session_context() as session:
            value = await session.scalar(select(AppSetting.value).where(AppSetting.key == key))
            assert value == {"history_id": "200"}
    finally:
        async with session_context() as session:
            await session.execute(delete(AppSetting).where(AppSetting.key == key))
            await session.commit()


@pytest.mark.asyncio
async def test_exception_ledger_preserves_evidence_across_retry_and_concurrency(monkeypatch):
    import asyncio

    from api.src.google.gmail import exceptions

    prefix = "gmail_unavailable:offline-ledger-test:"
    monkeypatch.setattr(exceptions, "PREFIX", prefix)
    key = prefix + "missing"
    async with session_context() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == key))
        await session.commit()
    try:
        await exceptions.record_unavailable(
            "missing", "original", {"http_status": 404, "logged_at": "2026-11-17T16:45:15Z"}
        )
        await asyncio.gather(
            *[
                exceptions.record_unavailable("missing", "page-" + str(n), {"history_id": str(n)})
                for n in range(3)
            ]
        )
        await exceptions.record_unavailable("missing", "page-1", {"history_id": "1"})
        assert await exceptions.has_unresolved_exception("missing")
        async with session_context() as session:
            value = await session.scalar(select(AppSetting.value).where(AppSetting.key == key))
        assert value["status"] == "unresolved"
        assert value["classification"] == "unknown"
        assert len(value["observations"]) == 4
        assert value["observations"]["original"]["logged_at"] == "2026-11-17T16:45:15Z"
    finally:
        async with session_context() as session:
            await session.execute(delete(AppSetting).where(AppSetting.key == key))
            await session.commit()
