"""Duplicate deliveries must never enqueue duplicate effects, including races."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import delete

from api.src.database.database import AsyncSessionFactory
from api.src.open_phone import routes
from api.src.open_phone.models import OpenPhoneEvent
from api.src.open_phone.schema import OpenPhoneWebhookPayload
from api.src.sernia_ai.config import QUO_SERNIA_AI_PHONE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["ai", "team"])
async def test_duplicate_event_queues_one_task_even_when_concurrent(monkeypatch, target):
    monkeypatch.setattr(
        routes,
        "get_contact_by_slug",
        AsyncMock(return_value=SimpleNamespace(phone_number="+14125559998")),
    )
    monkeypatch.setattr(routes, "_get_ai_phone_number", AsyncMock(return_value="+14125559999"))
    event_id = "EVtest-" + str(uuid4())
    payload = OpenPhoneWebhookPayload.model_validate(
        {
            "id": event_id,
            "object": "event",
            "createdAt": "2026-09-10T15:00:00Z",
            "apiVersion": "v3",
            "type": "message.received",
            "data": {
                "object": {
                    "id": "ACtest",
                    "object": "message",
                    "createdAt": "2026-09-10T15:00:00Z",
                    "userId": "UStest",
                    "phoneNumberId": QUO_SERNIA_AI_PHONE_ID if target == "ai" else "PNteam",
                    "conversationId": "CNtest",
                    "from": "+14125550101",
                    "to": "+14125559999" if target == "ai" else "+14125559998",
                    "body": "Maintenance update",
                    "status": "received",
                    "direction": "incoming",
                }
            },
        }
    )

    async def deliver():
        tasks = BackgroundTasks()
        async with AsyncSessionFactory() as session:
            response = await routes.webhook(payload, tasks, session)
        return response, tasks

    try:
        results = await asyncio.gather(deliver(), deliver())
        assert sum(len(tasks.tasks) for _, tasks in results) == 1
        replay, tasks = await deliver()
        assert replay["message"] == "Event already processed"
        assert not tasks.tasks
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(OpenPhoneEvent).where(OpenPhoneEvent.event_id == event_id))
            await session.commit()
