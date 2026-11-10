"""A Zillow lead is acknowledged to Pub/Sub only once it is safely queued."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from api.src.google.pubsub import routes
from api.src.sernia_ai.triggers import zillow_email_event_trigger as queue

NOTIFICATION = {"emailAddress": "emilio@serniacapital.com", "historyId": "12345"}


def _email(message_id: str, from_address: str) -> dict:
    return {
        "message_id": message_id,
        "thread_id": f"thread-{message_id}",
        "subject": "New lead",
        "from_address": from_address,
        "date": "2026-11-09T12:00:00+00:00",
        "body_text": "Is it available?",
    }


@pytest.fixture
def gmail(monkeypatch):
    """Gmail and the email table stubbed out; ``emails`` is what arrives."""
    emails: list[dict] = []

    @asynccontextmanager
    async def fake_session():
        yield AsyncMock()

    async def changes(_service, _history_id):
        return {
            "status": "success",
            "reason": "",
            "email_message_ids": [e["message_id"] for e in emails],
            "added_message_ids": [],
        }

    by_id = lambda message_id: next(e for e in emails if e["message_id"] == message_id)  # noqa: E731
    monkeypatch.setattr(routes, "get_delegated_credentials", Mock())
    monkeypatch.setattr(routes, "get_gmail_service", Mock())
    monkeypatch.setattr(routes, "get_email_changes", changes)
    monkeypatch.setattr(
        routes, "get_email_content", AsyncMock(side_effect=lambda _s, mid: by_id(mid))
    )
    monkeypatch.setattr(routes, "process_single_message", AsyncMock(side_effect=lambda m: m))
    monkeypatch.setattr(routes, "session_context", fake_session)
    monkeypatch.setattr(routes, "save_email_message", AsyncMock(return_value=(Mock(), True)))
    return emails


@pytest.mark.asyncio
async def test_zillow_email_is_queued_and_acknowledged(gmail, monkeypatch):
    enqueue = AsyncMock(return_value=True)
    monkeypatch.setattr(queue, "enqueue_zillow_email", enqueue)
    gmail.append(_email("m1", '"Zillow" <abc@convo.zillow.com>'))

    result = await routes.process_gmail_notification(NOTIFICATION)

    assert result["status"] == "success"
    kwargs = enqueue.await_args.kwargs
    assert (kwargs["message_id"], kwargs["thread_id"]) == ("m1", "thread-m1")
    assert kwargs["received_at"].isoformat() == "2026-11-09T12:00:00+00:00"


@pytest.mark.asyncio
async def test_other_email_is_not_queued(gmail, monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr(queue, "enqueue_zillow_email", enqueue)
    gmail.append(_email("m1", "tenant@gmail.com"))

    result = await routes.process_gmail_notification(NOTIFICATION)

    assert result["status"] == "success"
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_failed_enqueue_is_not_acknowledged(gmail, monkeypatch):
    """The email row saved but the queue insert failed. Reporting success
    here would make Pub/Sub drop a lead that never entered the queue."""
    monkeypatch.setattr(
        queue, "enqueue_zillow_email", AsyncMock(side_effect=RuntimeError("queue table down"))
    )
    gmail.append(_email("m1", "abc@convo.zillow.com"))

    result = await routes.process_gmail_notification(NOTIFICATION)

    assert result["status"] == "retry_needed"
    assert result["messages"] == []


@pytest.mark.asyncio
async def test_one_failed_enqueue_fails_the_notification_but_keeps_the_rest(gmail, monkeypatch):
    async def enqueue(**kwargs):
        if kwargs["message_id"] == "bad":
            raise RuntimeError("queue table down")
        return True

    monkeypatch.setattr(queue, "enqueue_zillow_email", enqueue)
    gmail.extend([_email("good", "a@convo.zillow.com"), _email("bad", "b@convo.zillow.com")])

    result = await routes.process_gmail_notification(NOTIFICATION)

    # Not "success": the webhook answers non-2xx and Pub/Sub redelivers.
    assert result["status"] == "partial_success_failure"
    assert [m["message_id"] for m in result["messages"]] == ["good"]
