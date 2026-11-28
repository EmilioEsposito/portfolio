"""Emergency escalation retries run on a wake-up job, not a 15-second poll.

The emergency path: each test names what a wrong wake-up would cause, a call
retried late or never, or a worker that wakes every few seconds for nothing.
Synthetic Twilio transport and the isolated test database; never real numbers.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from fastapi import BackgroundTasks
from sqlalchemy import delete, select, update

from api.src.apscheduler_service import service, wakeups
from api.src.database.database import AsyncSessionFactory
from api.src.open_phone import escalation_delivery as d
from api.src.open_phone import routes
from api.src.open_phone.models import (
    EscalationAttempt,
    EscalationDelivery,
    EscalationRequest,
    OpenPhoneEvent,
)
from api.src.schedulers import job_info

FLOW = "FW" + "0" * 32
SID = "FN" + "1" * 32
NUMBERS = ["+15555550101", "+15555550102"]
FROM = "+15555550100"


def now() -> datetime:
    return datetime.now(UTC)


def close(a: datetime | None, b: datetime, within: timedelta = timedelta(seconds=3)) -> bool:
    return a is not None and abs(a - b) <= within


async def wipe() -> None:
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(delete(EscalationAttempt))
        await s.execute(delete(EscalationDelivery))
        await s.execute(delete(EscalationRequest))


@pytest_asyncio.fixture(autouse=True)
async def empty_tables():
    """next_due_at reads every row, so each test starts from empty tables."""
    await wipe()
    yield
    await wipe()


async def queued(numbers=NUMBERS) -> tuple[str, list[EscalationDelivery]]:
    """A request and its calls, saved as ``dispatch`` saves them, not yet attempted."""
    source = "test:" + str(uuid4())
    request_id, created = d.identity(source), d.utcnow()
    async with AsyncSessionFactory() as s, s.begin():
        s.add(
            EscalationRequest(
                id=request_id,
                source_key=source,
                message_text="Synthetic emergency",
                state="ready",
                attempts=0,
                created_at=created,
                expires_at=created + timedelta(minutes=d.EXPIRY_MINUTES),
                next_attempt_at=created,
            )
        )
        await s.flush()
        await d.add_recipients(s, request_id, numbers)
    async with AsyncSessionFactory() as s:
        rows = (
            await s.scalars(
                select(EscalationDelivery)
                .where(EscalationDelivery.request_id == request_id)
                .order_by(EscalationDelivery.recipient_key)
            )
        ).all()
    return request_id, list(rows)


async def set_delivery(delivery_id: str, **values) -> None:
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            update(EscalationDelivery).where(EscalationDelivery.id == delivery_id).values(**values)
        )


async def set_request(request_id: str, **values) -> None:
    async with AsyncSessionFactory() as s, s.begin():
        await s.execute(
            update(EscalationRequest).where(EscalationRequest.id == request_id).values(**values)
        )


async def read(delivery_id: str) -> EscalationDelivery:
    async with AsyncSessionFactory() as s:
        return await s.get(EscalationDelivery, delivery_id)


class TestDueTimes:
    @pytest.mark.asyncio
    async def test_nothing_waiting_means_no_wake_up(self):
        assert await d.next_due_at() is None

    @pytest.mark.asyncio
    async def test_a_call_not_yet_attempted_is_due_now(self):
        await queued()
        assert await d.next_due_at() <= now()

    @pytest.mark.asyncio
    async def test_a_call_to_retry_is_due_at_its_retry_time(self):
        _, rows = await queued(NUMBERS[:1])
        await set_delivery(rows[0].id, state="retry", next_attempt_at=now() + timedelta(seconds=40))
        assert close(await d.next_due_at(), now() + timedelta(seconds=40))

    @pytest.mark.asyncio
    async def test_the_earliest_call_wins(self):
        _, rows = await queued()
        await set_delivery(rows[0].id, state="retry", next_attempt_at=now() + timedelta(minutes=2))
        await set_delivery(
            rows[1].id, state="blocked", next_attempt_at=now() + timedelta(seconds=20)
        )
        assert close(await d.next_due_at(), now() + timedelta(seconds=20))

    @pytest.mark.asyncio
    async def test_a_call_held_by_another_server_is_due_when_its_lease_ends(self):
        """A deploy: the old server took the call and stopped. Its row is skipped by
        every worker until the lease ends, so "due now" would wake for nothing."""
        _, rows = await queued(NUMBERS[:1])
        lease_end = now() + timedelta(seconds=d.LEASE_SECONDS)
        await set_delivery(
            rows[0].id,
            state="sending",
            lease_token="old-server",
            lease_until=lease_end,
            next_attempt_at=now() - timedelta(seconds=5),
        )
        assert close(await d.next_due_at(), lease_end)

    @pytest.mark.asyncio
    async def test_a_lease_that_ran_out_is_due_now(self):
        _, rows = await queued(NUMBERS[:1])
        await set_delivery(
            rows[0].id,
            state="sending",
            lease_token="dead-server",
            lease_until=now() - timedelta(seconds=1),
            next_attempt_at=now() - timedelta(minutes=1),
        )
        assert await d.next_due_at() <= now()

    @pytest.mark.asyncio
    async def test_an_assessment_waiting_or_held_is_due_by_the_same_rules(self):
        request_id, rows = await queued(NUMBERS[:1])
        await set_delivery(rows[0].id, state="accepted")
        await set_request(
            request_id, state="pending", next_attempt_at=now() + timedelta(seconds=30)
        )
        assert close(await d.next_due_at(), now() + timedelta(seconds=30))
        lease_end = now() + timedelta(seconds=d.LEASE_SECONDS)
        await set_request(
            request_id,
            state="assessing",
            lease_token="other",
            lease_until=lease_end,
            next_attempt_at=now() - timedelta(seconds=1),
        )
        assert close(await d.next_due_at(), lease_end)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("final", sorted(d.TERMINAL))
    async def test_settled_calls_and_requests_are_never_due(self, final):
        request_id, rows = await queued(NUMBERS[:1])
        await set_delivery(rows[0].id, state=final, next_attempt_at=now() - timedelta(hours=1))
        for state in ("ready", "skipped", "manual"):
            await set_request(request_id, state=state, next_attempt_at=now() - timedelta(hours=1))
            assert await d.next_due_at() is None

    @pytest.mark.asyncio
    async def test_a_long_retry_after_is_woken_at_the_deadline(self):
        """Twilio asks to wait an hour; the call is woken at its ten-minute
        deadline instead, to be marked for a person and logged as an error."""
        request_id, rows = await queued(NUMBERS[:1])
        client = d.StudioClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        429, headers={"Retry-After": "3600"}, json={"code": 20429}
                    )
                )
            ),
            FLOW,
            FROM,
        )
        await d.process_delivery(rows[0].id, client)
        async with AsyncSessionFactory() as s:
            request = await s.get(EscalationRequest, request_id)
        assert (await read(rows[0].id)).state == "retry"
        assert await d.next_due_at() == request.expires_at


class TestWiring:
    """Every path that leaves work for later moves the wake-up."""

    @pytest.fixture
    def wakes(self, monkeypatch) -> list[dict]:
        calls: list[dict] = []

        async def wake(job_id, *, not_before=None, min_delay=None):
            calls.append({"job_id": job_id, "not_before": not_before, "min_delay": min_delay})

        monkeypatch.setattr(d.wakeups, "wake", wake)
        return calls

    @pytest.mark.asyncio
    async def test_dispatch_sets_a_backstop_before_calling_and_the_next_wake_up_after(
        self, wakes, monkeypatch
    ):
        attempted_after: list[int] = []

        async def deliver(request_id=None):
            attempted_after.append(len(wakes))

        monkeypatch.setattr(d, "process_deliveries", deliver)
        await d.dispatch("Synthetic emergency", source_key="test:" + str(uuid4()), numbers=NUMBERS)
        assert attempted_after == [1]  # the backstop was set before the first attempt
        backstop, after = wakes
        assert backstop["job_id"] == after["job_id"] == d.JOB_ID
        assert backstop["min_delay"] == d.BACKSTOP
        assert after["min_delay"] is None and after["not_before"] is None

    @pytest.mark.asyncio
    async def test_dispatch_moves_the_wake_up_even_when_the_attempt_raises(
        self, wakes, monkeypatch
    ):
        monkeypatch.setattr(d, "process_deliveries", AsyncMock(side_effect=RuntimeError("db")))
        with pytest.raises(RuntimeError):
            await d.dispatch(
                "Synthetic emergency", source_key="test:" + str(uuid4()), numbers=NUMBERS
            )
        assert len(wakes) == 2 and wakes[1]["min_delay"] is None

    @pytest.mark.asyncio
    async def test_an_assessment_moves_the_wake_up_even_when_it_fails(self, wakes, monkeypatch):
        """A failed assessment is retried in 30 seconds: the wake-up must say so."""
        request_id, rows = await queued(NUMBERS[:1])
        await set_delivery(rows[0].id, state="accepted")
        await set_request(
            request_id,
            state="pending",
            event_payload={"from_number": NUMBERS[0]},
            next_attempt_at=now() - timedelta(seconds=1),
        )
        from api.src.open_phone import escalate

        monkeypatch.setattr(
            escalate, "ai_assess_for_escalation", AsyncMock(side_effect=RuntimeError("outage"))
        )
        await d.process_request(request_id)
        assert wakes == [{"job_id": d.JOB_ID, "not_before": None, "min_delay": None}]
        assert close(await d.next_due_at(), now() + timedelta(seconds=30))

    @pytest.mark.asyncio
    async def test_the_webhook_sets_the_backstop_before_the_background_attempt(
        self, wakes, monkeypatch
    ):
        from api.src.tests.test_escalation_delivery import webhook_payload

        event_id, message_id = "test:" + str(uuid4()), "test:" + str(uuid4())
        monkeypatch.setattr(
            routes,
            "get_contact_by_slug",
            AsyncMock(return_value=SimpleNamespace(phone_number=FROM)),
        )
        monkeypatch.setattr(routes, "_get_ai_phone_number", AsyncMock(return_value=None))
        try:
            tasks = BackgroundTasks()
            async with AsyncSessionFactory() as s:
                await routes.webhook(webhook_payload(event_id, message_id), tasks, s)
            assert len(tasks.tasks) == 1  # the first attempt still runs at once
            assert len(wakes) == 1 and wakes[0]["min_delay"] == d.BACKSTOP
            # A duplicate delivery of the same webhook sets nothing.
            async with AsyncSessionFactory() as s:
                await routes.webhook(webhook_payload(event_id, message_id), BackgroundTasks(), s)
            assert len(wakes) == 1
        finally:
            async with AsyncSessionFactory() as s, s.begin():
                await s.execute(delete(OpenPhoneEvent).where(OpenPhoneEvent.event_id == event_id))

    @pytest.mark.asyncio
    async def test_each_recovery_run_sets_the_next_wake_up(self, wakes, monkeypatch):
        monkeypatch.setattr(d, "process_deliveries", AsyncMock())
        await d.recover_escalations()
        assert len(wakes) == 1
        assert wakes[0]["min_delay"] == wakeups.RERUN_GAP


@pytest.fixture
def scheduler(monkeypatch) -> Iterator[AsyncIOScheduler]:
    """A real scheduler with an in-memory job store, in place of the app's."""
    fresh = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    monkeypatch.setattr(service, "_scheduler", fresh)
    monkeypatch.setattr(job_info, "_BY_JOB_ID", {})
    monkeypatch.setattr(wakeups, "_QUEUES", {})
    monkeypatch.setattr(wakeups, "_LOCKS", {})
    monkeypatch.setattr(wakeups, "_RUNNING", set())
    monkeypatch.setattr(wakeups, "_RETRIES", {})
    monkeypatch.setattr(wakeups, "_listening_to", None)
    monkeypatch.setattr(wakeups, "_catch_up_registered", False)
    yield fresh
    wakeups.cancel_retries()
    if fresh.running:
        fresh.shutdown(wait=False)


@pytest.mark.asyncio
async def test_a_failed_call_is_retried_by_its_wake_up_and_the_queue_goes_idle(
    scheduler, monkeypatch
):
    """End to end on a live scheduler: Twilio refuses the first call (a
    connection error), the call's retry sets the wake-up, the wake-up runs the
    real recovery, the call is accepted, and nothing is left waiting."""
    posts: list[httpx.Request] = []

    def twilio(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            if len(posts) == 1:
                raise httpx.ConnectError("synthetic outage", request=request)
            return httpx.Response(201, json={"sid": SID})
        return httpx.Response(200, json={"executions": []})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        d.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(twilio)),
    )
    from api.src.open_phone import escalate

    monkeypatch.setattr(escalate, "ESCALATE_FROM_NUMBER", FROM)
    monkeypatch.setattr(escalate, "TWILIO_FLOW_ID", FLOW)

    wakeups.register_queue(d.QUEUE)
    scheduler.start()
    scheduler.remove_job(d.JOB_ID)  # leave out the startup run: the test drives it

    report = await d.dispatch(
        "Synthetic emergency", source_key="test:" + str(uuid4()), numbers=NUMBERS[:1]
    )
    assert list(report.recipients.values()) == ["retry"]
    delivery_id = (await queued_ids(report.request_id))[0]
    retry_at = (await read(delivery_id)).next_attempt_at
    job = scheduler.get_job(d.JOB_ID)
    assert isinstance(job.trigger, DateTrigger)
    assert close(job.next_run_time, retry_at)  # woken for the retry, not polled

    # Bring the retry forward so the test does not wait 15 seconds.
    await set_delivery(delivery_id, next_attempt_at=now() + timedelta(milliseconds=300))
    await wakeups.wake(d.JOB_ID)
    for _ in range(80):
        if (await read(delivery_id)).state == "accepted" and scheduler.get_job(d.JOB_ID) is None:
            break
        await asyncio.sleep(0.1)
    assert (await read(delivery_id)).state == "accepted"
    assert len(posts) == 2
    assert scheduler.get_job(d.JOB_ID) is None  # idle: nothing waits


async def queued_ids(request_id: str) -> list[str]:
    async with AsyncSessionFactory() as s:
        return list(
            await s.scalars(
                select(EscalationDelivery.id).where(EscalationDelivery.request_id == request_id)
            )
        )
