"""Synthetic provider transport + isolated PostgreSQL. Never uses real credentials."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy import delete, select, update

from api.src.database.database import AsyncSessionFactory
from api.src.open_phone import escalation_delivery as d
from api.src.open_phone import routes
from api.src.open_phone.models import (
    EscalationAttempt,
    EscalationDelivery,
    EscalationRequest,
    OpenPhoneEvent,
)
from api.src.open_phone.schema import OpenPhoneWebhookPayload

FLOW = "FW" + "0" * 32
SID = "FN" + "1" * 32
OTHER_SID = "FN" + "2" * 32
NUMBERS = ["+15555550101", "+15555550102"]
FROM = "+15555550100"


@pytest_asyncio.fixture
async def requests_db(monkeypatch):
    ids = []
    monkeypatch.setattr(d, "process_deliveries", AsyncMock())

    async def create(numbers=NUMBERS):
        source = "test:" + str(uuid4())
        result = await d.dispatch("Synthetic emergency", source_key=source, numbers=numbers)
        ids.append(result.request_id)
        async with AsyncSessionFactory() as s:
            rows = (
                await s.scalars(
                    select(EscalationDelivery)
                    .where(EscalationDelivery.request_id == result.request_id)
                    .order_by(EscalationDelivery.recipient_key)
                )
            ).all()
        return source, result.request_id, rows

    yield create
    async with AsyncSessionFactory() as s, s.begin():
        deliveries = select(EscalationDelivery.id).where(EscalationDelivery.request_id.in_(ids))
        await s.execute(
            delete(EscalationAttempt).where(EscalationAttempt.delivery_id.in_(deliveries))
        )
        await s.execute(delete(EscalationDelivery).where(EscalationDelivery.request_id.in_(ids)))
        await s.execute(delete(EscalationRequest).where(EscalationRequest.id.in_(ids)))


def studio(handler):
    return d.StudioClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), FLOW, FROM)


async def due(delivery_id, **values):
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            update(EscalationDelivery)
            .where(EscalationDelivery.id == delivery_id)
            .values(next_attempt_at=d.utcnow() - timedelta(seconds=1), **values)
        )


async def read(delivery_id):
    async with AsyncSessionFactory() as s:
        return await s.get(EscalationDelivery, delivery_id)


def context(row, *, token=None, number=None):
    return {
        "context": {
            "contact": {"channel": {"address": number or row.recipient}},
            "flow": {
                "flow_sid": FLOW,
                "channel": {"address": FROM},
                "data": {"escalation_delivery_id": token or row.id},
            },
        }
    }


@pytest.mark.asyncio
async def test_partial_success_only_retries_failed_recipient(requests_db):
    source, request_id, rows = await requests_db()
    posts = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(
                200,
                json={"contact_channel_address": NUMBERS[1], "flow_sid": FLOW, "status": "ended"},
            )
        number = parse_qs(req.content.decode())["To"][0]
        posts.append(number)
        if number == NUMBERS[1] and posts.count(number) == 1:
            return httpx.Response(429, json={"code": 20429}, headers={"Retry-After": "40"})
        return httpx.Response(201, json={"sid": SID})

    client = studio(handler)
    await asyncio.gather(*(d.process_delivery(r.id, client) for r in rows))
    result = await d.report(request_id)
    assert sorted(result.recipients.values()) == ["accepted", "retry"]
    retry = await read(rows[1].id)
    assert retry.next_attempt_at >= d.utcnow() + timedelta(seconds=35)
    await due(rows[1].id)
    await asyncio.gather(*(d.process_delivery(r.id, client) for r in rows))
    assert posts == [NUMBERS[0], NUMBERS[1], NUMBERS[1]]
    assert (await d.report(request_id)).accepted == 2
    repeated = await d.dispatch("Changed retry text", source_key=source, numbers=["+15555550999"])
    assert repeated.accepted == 2 and len(repeated.recipients) == 2


@pytest.mark.asyncio
async def test_concurrent_workers_only_post_once(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    started, release = asyncio.Event(), asyncio.Event()
    posts = 0

    async def handler(req):
        nonlocal posts
        posts += 1
        started.set()
        await release.wait()
        return httpx.Response(201, json={"sid": SID})

    client = studio(handler)
    first = asyncio.create_task(d.process_delivery(rows[0].id, client))
    await started.wait()
    await d.process_delivery(rows[0].id, client)
    release.set()
    await first
    assert posts == 1 and (await read(rows[0].id)).state == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server_error", "bad_json", "missing_sid"])
async def test_uncertain_sends_are_reconciled_without_repost(requests_db, failure):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    row, posts = rows[0], []

    def handler(req):
        if req.method == "POST":
            posts.append(req)
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic", request=req)
            if failure == "server_error":
                return httpx.Response(503, json={})
            if failure == "bad_json":
                return httpx.Response(201, text="not json")
            return httpx.Response(201, json={})
        if req.url.path.endswith("/Context"):
            return httpx.Response(200, json=context(row))
        return httpx.Response(
            200, json={"executions": [{"sid": SID, "contact_channel_address": row.recipient}]}
        )

    client = studio(handler)
    await d.process_delivery(row.id, client)
    assert (await read(row.id)).state == "unknown"
    await due(row.id)
    await d.process_delivery(row.id, client)
    saved = await read(row.id)
    assert saved.state == "accepted" and saved.execution_sid == SID and len(posts) == 1


@pytest.mark.asyncio
async def test_unknown_absence_never_resends_even_after_lease_or_deadline(requests_db):
    _, request_id, rows = await requests_db(numbers=NUMBERS[:1])
    methods = []

    def handler(req):
        methods.append(req.method)
        if req.method == "POST":
            raise httpx.WriteTimeout("uncertain", request=req)
        return httpx.Response(
            200, json={"executions": [], "meta": {"next_page_url": "unread-page"}}
        )

    client = studio(handler)
    await d.process_delivery(rows[0].id, client)
    await due(rows[0].id)
    await d.process_delivery(rows[0].id, client)
    assert (await read(rows[0].id)).state == "unknown"
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            update(EscalationRequest)
            .where(EscalationRequest.id == request_id)
            .values(expires_at=d.utcnow())
        )
    await due(rows[0].id)
    await d.process_delivery(rows[0].id, client)
    assert (await read(rows[0].id)).state == "manual"
    assert methods == ["POST", "GET"]


@pytest.mark.asyncio
async def test_crash_after_provider_acceptance_recovers_get_only(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    row, methods = rows[0], []

    def handler(req):
        methods.append(req.method)
        if req.method == "POST":
            raise asyncio.CancelledError()
        if req.url.path.endswith("/Context"):
            return httpx.Response(200, json=context(row))
        return httpx.Response(
            200, json={"executions": [{"sid": SID, "contact_channel_address": row.recipient}]}
        )

    client = studio(handler)
    with pytest.raises(asyncio.CancelledError):
        await d.process_delivery(row.id, client)
    assert (await read(row.id)).state == "sending"
    await due(row.id, lease_until=d.utcnow() - timedelta(seconds=1))
    await d.process_delivery(row.id, client)
    assert (await read(row.id)).state == "accepted" and methods.count("POST") == 1


@pytest.mark.asyncio
async def test_late_worker_cannot_overwrite_new_owner(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(req):
        started.set()
        await release.wait()
        return httpx.Response(201, json={"sid": SID})

    task = asyncio.create_task(d.process_delivery(rows[0].id, studio(handler)))
    await started.wait()
    await due(rows[0].id, lease_token="new-owner", state="accepted", execution_sid=OTHER_SID)
    release.set()
    await task
    assert (await read(rows[0].id)).execution_sid == OTHER_SID


@pytest.mark.asyncio
async def test_busy_recipient_waits_then_sends_new_incident(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    row, posts, active = rows[0], [], True

    def handler(req):
        if req.method == "POST":
            posts.append(req)
            if len(posts) == 1:
                return httpx.Response(
                    409, json={"code": 20409, "details": {"conflicting_execution_sid": OTHER_SID}}
                )
            return httpx.Response(201, json={"sid": SID})
        if req.url.path.endswith("/Context"):
            return httpx.Response(200, json=context(row, token="different-incident"))
        return httpx.Response(
            200,
            json={
                "contact_channel_address": row.recipient,
                "flow_sid": FLOW,
                "status": "active" if active else "ended",
            },
        )

    client = studio(handler)
    for _ in range(2):
        await due(row.id)
        await d.process_delivery(row.id, client)
        assert (await read(row.id)).state == "blocked"
    assert len(posts) == 1
    active = False
    await due(row.id)
    await d.process_delivery(row.id, client)
    assert (await read(row.id)).state == "retry"
    await due(row.id)
    await d.process_delivery(row.id, client)
    assert (await read(row.id)).state == "accepted" and len(posts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("same_recipient", [True, False])
async def test_conflict_only_adopts_exact_identity(requests_db, same_recipient):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    row = rows[0]
    await due(row.id, state="blocked", conflict_sid=SID)

    def handler(req):
        assert req.method == "GET"
        if req.url.path.endswith("/Context"):
            return httpx.Response(
                200, json=context(row, number=row.recipient if same_recipient else NUMBERS[1])
            )
        return httpx.Response(
            200,
            json={"contact_channel_address": row.recipient, "flow_sid": FLOW, "status": "active"},
        )

    await d.process_delivery(row.id, studio(handler))
    assert (await read(row.id)).state == ("accepted" if same_recipient else "blocked")


@pytest.mark.asyncio
async def test_connection_failure_retries_but_permanent_rejection_does_not(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    posts = []

    def handler(req):
        posts.append(req)
        if len(posts) == 1:
            raise httpx.ConnectError("synthetic", request=req)
        return httpx.Response(400, json={"code": 21211})

    client = studio(handler)
    await d.process_delivery(rows[0].id, client)
    assert (await read(rows[0].id)).state == "retry"
    await due(rows[0].id)
    await d.process_delivery(rows[0].id, client)
    assert (await read(rows[0].id)).state == "failed"
    await due(rows[0].id)
    await d.process_delivery(rows[0].id, client)
    assert len(posts) == 2
    async with AsyncSessionFactory() as s:
        attempts = (
            await s.scalars(
                select(EscalationAttempt).where(EscalationAttempt.delivery_id == rows[0].id)
            )
        ).all()
    assert {a.outcome for a in attempts} == {"retry", "failed"}


@pytest.mark.asyncio
async def test_missing_contact_remains_durable_work(requests_db, monkeypatch):
    _, _, rows = await requests_db(numbers=None)
    monkeypatch.setattr(d, "get_contact_by_slug", AsyncMock(return_value=None))
    client = studio(lambda req: pytest.fail("Missing contact must not send"))
    await d.process_delivery(rows[0].id, client)
    saved = await read(rows[0].id)
    assert (
        saved.state == "retry"
        and saved.last_error == "recipient_unavailable"
        and saved.attempts == 0
    )


def webhook_payload(event_id, message_id):
    now = d.utcnow()
    return OpenPhoneWebhookPayload.model_validate(
        {
            "id": event_id,
            "object": "event",
            "createdAt": now,
            "apiVersion": "v3",
            "type": "message.received",
            "data": {
                "object": {
                    "id": message_id,
                    "object": "message",
                    "createdAt": now,
                    "userId": "test",
                    "phoneNumberId": "test",
                    "conversationId": "test",
                    "from": NUMBERS[0],
                    "to": FROM,
                    "body": "Synthetic emergency",
                    "status": "received",
                    "direction": "incoming",
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_webhook_commit_dedup_and_lost_background_recovery(monkeypatch):
    event_id, message_id = "test:" + str(uuid4()), "test:" + str(uuid4())
    payload = webhook_payload(event_id, message_id)
    request_id = d.identity(f"quo-message:{message_id}")
    monkeypatch.setattr(
        routes, "get_contact_by_slug", AsyncMock(return_value=SimpleNamespace(phone_number=FROM))
    )
    monkeypatch.setattr(routes, "_get_ai_phone_number", AsyncMock(return_value=None))
    monkeypatch.setattr(d, "process_deliveries", AsyncMock())
    from api.src.open_phone import escalate

    assessment = AsyncMock(return_value=(True, "synthetic"))
    monkeypatch.setattr(escalate, "ai_assess_for_escalation", assessment)
    try:

        async def accept():
            tasks = BackgroundTasks()
            async with AsyncSessionFactory() as s:
                return await routes.webhook(payload, tasks, s), tasks

        results = await asyncio.gather(accept(), accept())
        assert sum(len(tasks.tasks) for _, tasks in results) == 1
        # Simulate the HTTP process exiting without running BackgroundTasks.
        async with AsyncSessionFactory() as s:
            saved = await s.get(EscalationRequest, request_id)
            assert saved.state == "pending"
        await asyncio.gather(d.process_request(request_id), d.process_request(request_id))
        assessment.assert_awaited_once()
        assert len((await d.report(request_id)).recipients) == 2
        # Same provider message in another event envelope does not re-assess.
        async with AsyncSessionFactory() as s, s.begin():
            key = await d.enqueue_assessment(
                s, routes.extract_event_data(webhook_payload(event_id + "-retry", message_id))
            )
        assert key == request_id
        await d.process_request(request_id)
        assessment.assert_awaited_once()
    finally:
        async with AsyncSessionFactory() as s, s.begin():
            await s.execute(
                delete(EscalationDelivery).where(EscalationDelivery.request_id == request_id)
            )
            await s.execute(delete(EscalationRequest).where(EscalationRequest.id == request_id))
            await s.execute(delete(OpenPhoneEvent).where(OpenPhoneEvent.event_id == event_id))


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_webhook(monkeypatch):
    event_id = "test:" + str(uuid4())
    monkeypatch.setattr(
        routes, "get_contact_by_slug", AsyncMock(return_value=SimpleNamespace(phone_number=FROM))
    )
    monkeypatch.setattr(routes, "_get_ai_phone_number", AsyncMock(return_value=None))
    monkeypatch.setattr(
        routes, "enqueue_assessment", AsyncMock(side_effect=RuntimeError("synthetic rollback"))
    )
    tasks = BackgroundTasks()
    async with AsyncSessionFactory() as s:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await routes.webhook(webhook_payload(event_id, "test"), tasks, s)
    async with AsyncSessionFactory() as s:
        assert (
            await s.scalar(select(OpenPhoneEvent).where(OpenPhoneEvent.event_id == event_id))
            is None
        )
    assert not tasks.tasks


@pytest.mark.asyncio
async def test_two_slugs_resolving_same_number_send_once(requests_db, monkeypatch):
    _, request_id, rows = await requests_db(numbers=None)
    monkeypatch.setattr(
        d, "get_contact_by_slug", AsyncMock(return_value=SimpleNamespace(phone_number=NUMBERS[0]))
    )
    posts = []

    def handler(req):
        posts.append(req)
        return httpx.Response(201, json={"sid": SID})

    client = studio(handler)
    await asyncio.gather(*(d.process_delivery(row.id, client) for row in rows))
    assert len(posts) == 1
    assert sorted((await d.report(request_id)).recipients.values()) == ["accepted", "duplicate"]


@pytest.mark.asyncio
async def test_expiry_during_contact_lookup_does_not_send(requests_db, monkeypatch):
    _, request_id, rows = await requests_db(numbers=None)

    async def lookup(slug):
        async with AsyncSessionFactory() as s, s.begin():
            await s.execute(
                update(EscalationRequest)
                .where(EscalationRequest.id == request_id)
                .values(expires_at=d.utcnow())
            )
        return SimpleNamespace(phone_number=NUMBERS[0])

    monkeypatch.setattr(d, "get_contact_by_slug", lookup)
    await d.process_delivery(
        rows[0].id, studio(lambda req: pytest.fail("Expired work must not send"))
    )
    saved = await read(rows[0].id)
    assert saved.state == "manual" and saved.lease_token is None and saved.attempts == 0


@pytest.mark.asyncio
async def test_expired_send_fences_old_completion(requests_db):
    _, request_id, rows = await requests_db(numbers=NUMBERS[:1])
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(req):
        started.set()
        await release.wait()
        return httpx.Response(201, json={"sid": SID})

    client = studio(handler)
    task = asyncio.create_task(d.process_delivery(rows[0].id, client))
    await started.wait()
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            update(EscalationRequest)
            .where(EscalationRequest.id == request_id)
            .values(expires_at=d.utcnow())
        )
    await due(rows[0].id, lease_until=d.utcnow() - timedelta(seconds=1))
    await d.process_delivery(rows[0].id, client)
    release.set()
    await task
    saved = await read(rows[0].id)
    assert saved.state == "manual" and saved.lease_token is None and saved.execution_sid is None


@pytest.mark.asyncio
async def test_busy_get_failure_keeps_conflict_without_reposting(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    await due(rows[0].id, state="blocked", conflict_sid=OTHER_SID)

    def handler(req):
        assert req.method == "GET"
        raise httpx.ReadTimeout("synthetic provider read timeout", request=req)

    await d.process_delivery(rows[0].id, studio(handler))
    saved = await read(rows[0].id)
    assert saved.state == "blocked" and saved.conflict_sid == OTHER_SID and saved.attempts == 0


@pytest.mark.asyncio
async def test_assessment_failure_is_recoverable_and_deadline_fenced(requests_db, monkeypatch):
    _, request_id, _ = await requests_db(numbers=[])
    from api.src.open_phone import escalate

    assessment = AsyncMock(side_effect=[RuntimeError("synthetic outage"), (False, "routine")])
    monkeypatch.setattr(escalate, "ai_assess_for_escalation", assessment)
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            delete(EscalationDelivery).where(EscalationDelivery.request_id == request_id)
        )
        await s.execute(
            update(EscalationRequest)
            .where(EscalationRequest.id == request_id)
            .values(
                state="pending",
                event_payload={"from_number": NUMBERS[0]},
            )
        )
    await d.process_request(request_id)
    async with AsyncSessionFactory() as s, s.begin():
        saved = await s.get(EscalationRequest, request_id)
        assert saved.state == "pending" and saved.last_error == "RuntimeError"
        saved.next_attempt_at = d.utcnow()
    await d.process_request(request_id)
    async with AsyncSessionFactory() as s, s.begin():
        saved = await s.get(EscalationRequest, request_id)
        assert saved.state == "skipped"
        saved.state = "assessing"
        saved.lease_token = "old-worker"
        saved.lease_until = saved.expires_at = d.utcnow() - timedelta(seconds=1)
    await d.process_request(request_id)
    async with AsyncSessionFactory() as s:
        saved = await s.get(EscalationRequest, request_id)
        assert saved.state == "manual" and saved.lease_token is None
    assert assessment.await_count == 2


@pytest.mark.asyncio
async def test_retry_limit_remains_terminal(requests_db):
    _, _, rows = await requests_db(numbers=NUMBERS[:1])
    await due(rows[0].id, attempts=d.MAX_SEND_ATTEMPTS)
    await d.process_delivery(
        rows[0].id, studio(lambda req: pytest.fail("Exhausted send must stop"))
    )
    assert (await read(rows[0].id)).state == "manual"


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["3600", "Sun, 11 Oct 2099 18:00:00 GMT"])
async def test_long_retry_after_wakes_at_deadline_without_resending(
    requests_db, monkeypatch, retry_after
):
    _, request_id, rows = await requests_db(numbers=NUMBERS[:1])
    posts = []

    def handler(req):
        posts.append(req)
        return httpx.Response(429, headers={"Retry-After": retry_after}, json={"code": 20429})

    client = studio(handler)
    await d.process_delivery(rows[0].id, client)
    saved = await read(rows[0].id)
    async with AsyncSessionFactory() as s:
        request = await s.get(EscalationRequest, request_id)
    assert saved.state == "retry" and saved.next_attempt_at == request.expires_at
    monkeypatch.setattr(d, "utcnow", lambda: request.expires_at)
    await d.process_delivery(rows[0].id, client)
    saved = await read(rows[0].id)
    assert saved.state == "manual" and saved.last_error == "delivery_deadline_exceeded"
    assert len(posts) == 1
