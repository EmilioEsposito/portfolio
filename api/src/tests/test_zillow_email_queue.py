"""The durable Zillow email queue, exercised against a real database.

Each test names the failure it guards against: a lost lead, a duplicated
reply, two workers on one thread, or a job that retries forever.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.models import ZillowEmailJob
from api.src.sernia_ai.runs import RunOutcome
from api.src.sernia_ai.triggers import zillow_email_event_trigger as queue

LEAD = "abc123@convo.zillow.com"
OK = RunOutcome(persisted=True, reply_text="Drafted a reply.")


@pytest_asyncio.fixture(autouse=True)
async def clean_queue(monkeypatch):
    """Empty table, kill switch on, zero debounce, no real agent."""

    async def wipe():
        async with AsyncSessionFactory() as s:
            await s.execute(delete(ZillowEmailJob))
            await s.commit()

    await wipe()
    monkeypatch.setattr(queue, "is_sernia_ai_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        queue,
        "get_zillow_email_config",
        AsyncMock(return_value={"debounce_seconds": 0, "require_approval": True}),
    )
    monkeypatch.setattr(queue, "_already_completed", AsyncMock(return_value=False))
    monkeypatch.setattr(queue, "execute_trigger_run", AsyncMock(return_value=OK))
    yield
    await wipe()


async def enqueue(message_id: str | None = None, thread_id: str = "T1", **kwargs) -> str:
    message_id = message_id or uuid.uuid4().hex
    await queue.enqueue_zillow_email(
        message_id=message_id,
        thread_id=thread_id,
        subject=kwargs.pop("subject", "New lead"),
        from_address=LEAD,
        **kwargs,
    )
    return message_id


async def rows() -> dict[str, ZillowEmailJob]:
    async with AsyncSessionFactory() as s:
        return {r.message_id: r for r in (await s.scalars(select(ZillowEmailJob))).all()}


def later(**delta) -> datetime:
    return datetime.now(UTC) + timedelta(**delta)


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_redelivery_of_the_same_email_queues_it_once(self):
        """Pub/Sub is at-least-once and label changes re-notify the same email."""
        assert await queue.enqueue_zillow_email(
            message_id="m1", thread_id="T1", subject="s", from_address=LEAD
        )
        for _ in range(3):
            assert not await queue.enqueue_zillow_email(
                message_id="m1", thread_id="T1", subject="s", from_address=LEAD
            )
        assert list(await rows()) == ["m1"]

    @pytest.mark.asyncio
    async def test_a_finished_email_is_never_queued_again(self):
        """A label change days later must not reopen a handled lead."""
        await enqueue("m1")
        await queue.process_zillow_email_queue()
        assert not await queue.enqueue_zillow_email(
            message_id="m1", thread_id="T1", subject="s", from_address=LEAD
        )
        assert (await rows())["m1"].state == "done"

    @pytest.mark.asyncio
    async def test_old_email_is_not_queued(self):
        """A label change on last season's email arrives as a fresh notification."""
        queued = await queue.enqueue_zillow_email(
            message_id="old",
            thread_id="T1",
            subject="s",
            from_address=LEAD,
            received_at=later(days=-30),
        )
        assert not queued
        assert await rows() == {}

    @pytest.mark.asyncio
    async def test_nothing_is_queued_while_automation_is_off(self, monkeypatch):
        monkeypatch.setattr(queue, "is_sernia_ai_enabled", AsyncMock(return_value=False))
        await enqueue("m1")
        assert await rows() == {}

    @pytest.mark.asyncio
    async def test_debounce_delays_the_first_attempt(self, monkeypatch):
        monkeypatch.setattr(
            queue,
            "get_zillow_email_config",
            AsyncMock(return_value={"debounce_seconds": 300, "require_approval": True}),
        )
        await enqueue("m1")
        assert await queue.claim_next_thread() is None
        assert await queue.claim_next_thread(now=later(seconds=301)) is not None


class TestClaim:
    @pytest.mark.asyncio
    async def test_emails_in_one_thread_are_claimed_together_oldest_first(self):
        first, second = await enqueue(thread_id="T1"), await enqueue(thread_id="T1")
        claim = await queue.claim_next_thread()
        assert [e.message_id for e in claim.emails] == [first, second]
        assert claim.attempt == 1

    @pytest.mark.asyncio
    async def test_first_email_starts_the_window_and_later_ones_ride_along(self, monkeypatch):
        config = AsyncMock(return_value={"debounce_seconds": 300, "require_approval": True})
        monkeypatch.setattr(queue, "get_zillow_email_config", config)
        first = await enqueue(thread_id="T1")
        # Backdate the first email so its window has closed; the second is new.
        async with AsyncSessionFactory() as s:
            row = await s.scalar(select(ZillowEmailJob).where(ZillowEmailJob.message_id == first))
            row.next_attempt_at = later(seconds=-1)
            await s.commit()
        second = await enqueue(thread_id="T1")

        claim = await queue.claim_next_thread()
        assert {e.message_id for e in claim.emails} == {first, second}

    @pytest.mark.asyncio
    async def test_each_thread_gets_its_own_run(self):
        await enqueue(thread_id="T1")
        await enqueue(thread_id="T2")
        a, b = await queue.claim_next_thread(), await queue.claim_next_thread()
        assert {a.thread_id, b.thread_id} == {"T1", "T2"}
        assert await queue.claim_next_thread() is None

    @pytest.mark.asyncio
    async def test_claimed_jobs_are_not_handed_out_twice(self):
        """Two workers must never run the agent on the same thread."""
        await enqueue(thread_id="T1")
        assert await queue.claim_next_thread() is not None
        assert await queue.claim_next_thread() is None

    @pytest.mark.asyncio
    async def test_new_email_waits_while_its_thread_is_being_worked_on(self):
        """A second worker (e.g. the old process during a deploy) must not
        start a run on a lead another run is still handling — with approval
        off, the two could send conflicting replies."""
        await enqueue(thread_id="T1")
        running = await queue.claim_next_thread()
        late = await enqueue(thread_id="T1")
        other = await enqueue(thread_id="T2")

        next_claim = await queue.claim_next_thread()
        assert [e.message_id for e in next_claim.emails] == [other]  # other leads still flow
        assert await queue.claim_next_thread() is None  # T1's new email is held back

        await queue.complete_thread(running)
        follow_up = await queue.claim_next_thread()
        assert [e.message_id for e in follow_up.emails] == [late]

    @pytest.mark.asyncio
    async def test_a_dead_workers_jobs_come_back_when_the_lease_runs_out(self):
        message_id = await enqueue(thread_id="T1")
        crashed = await queue.claim_next_thread()

        assert await queue.claim_next_thread(now=later(minutes=29)) is None
        reclaimed = await queue.claim_next_thread(now=later(minutes=31))
        assert [e.message_id for e in reclaimed.emails] == [message_id]
        assert reclaimed.attempt == 2
        assert reclaimed.lease_token != crashed.lease_token

    @pytest.mark.asyncio
    async def test_reclaimed_batch_keeps_its_conversation_when_a_new_email_arrived(self):
        """The crash window: the run was saved, the worker died before
        marking it done, and another email landed meanwhile. The retry must
        look up the SAME conversation (to see it is already complete) — so
        the new email cannot join the batch and change its identity."""
        original = await enqueue(thread_id="T1")
        crashed = await queue.claim_next_thread()
        late = await enqueue(thread_id="T1")

        reclaimed = await queue.claim_next_thread(now=later(minutes=31))
        assert [e.message_id for e in reclaimed.emails] == [original]
        assert reclaimed.conversation_id == crashed.conversation_id
        assert reclaimed.attempt == 2

        await queue.complete_thread(reclaimed)
        next_batch = await queue.claim_next_thread(now=later(minutes=31))
        assert [e.message_id for e in next_batch.emails] == [late]
        assert next_batch.conversation_id != crashed.conversation_id
        assert next_batch.attempt == 1

    @pytest.mark.asyncio
    async def test_conversation_id_is_recorded_when_the_batch_is_first_claimed(self):
        message_id = await enqueue(thread_id="T1")
        claim = await queue.claim_next_thread()
        row = (await rows())[message_id]
        assert row.conversation_id == claim.conversation_id == f"zillow-T1-{row.id}"

    @pytest.mark.asyncio
    async def test_a_worker_that_lost_its_lease_cannot_overwrite_the_new_owner(self):
        message_id = await enqueue(thread_id="T1")
        stale = await queue.claim_next_thread()
        current = await queue.claim_next_thread(now=later(minutes=31))

        await queue.fail_thread(stale, "slow worker finally gave up")
        assert (await rows())[message_id].state == "running"
        assert (await rows())[message_id].lease_token == current.lease_token

        await queue.complete_thread(current)
        assert (await rows())[message_id].state == "done"

    @pytest.mark.asyncio
    async def test_unprocessed_jobs_expire_instead_of_replying_late(self):
        message_id = await enqueue(thread_id="T1")
        assert await queue.claim_next_thread(now=later(hours=25)) is None
        assert (await rows())[message_id].state == "expired"

    @pytest.mark.asyncio
    async def test_a_job_that_keeps_killing_its_worker_is_parked(self):
        """Three lease expiries in a row: stop handing it out."""
        message_id = await enqueue(thread_id="T1")
        for attempt in range(queue.MAX_ATTEMPTS):
            assert await queue.claim_next_thread(now=later(minutes=31 * attempt)) is not None
        assert await queue.claim_next_thread(now=later(minutes=31 * queue.MAX_ATTEMPTS)) is None
        assert (await rows())[message_id].state == "failed"


class TestProcess:
    @pytest.mark.asyncio
    async def test_successful_run_marks_the_jobs_done(self):
        message_id = await enqueue(thread_id="T1")
        await queue.process_zillow_email_queue()

        row = (await rows())[message_id]
        assert row.state == "done"
        assert row.lease_token is None
        assert row.conversation_id == f"zillow-T1-{row.id}"

    @pytest.mark.asyncio
    async def test_run_is_told_which_emails_arrived(self):
        first = await enqueue(thread_id="T1", subject="Is it available?")
        second = await enqueue(thread_id="T1", subject="Also, pets?")
        await queue.process_zillow_email_queue()

        kwargs = queue.execute_trigger_run.await_args.kwargs
        assert kwargs["trigger_source"] == "zillow_email_event"
        assert first in kwargs["trigger_prompt"] and second in kwargs["trigger_prompt"]
        assert "zillow-auto-reply skill" in kwargs["trigger_prompt"]
        assert kwargs["conversation_id"] in kwargs["trigger_prompt"]  # deeplink
        assert kwargs["trigger_metadata"]["subjects"] == ["Is it available?", "Also, pets?"]
        assert kwargs["trigger_metadata"]["email_count"] == 2

    @pytest.mark.asyncio
    async def test_single_email_prompt_names_the_message_to_reply_to(self):
        message_id = await enqueue(thread_id="T1")
        await queue.process_zillow_email_queue()
        prompt = queue.execute_trigger_run.await_args.kwargs["trigger_prompt"]
        assert f'reply_to_message_id="{message_id}"' in prompt

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("require_approval", "expected_scope"),
        [(True, frozenset()), (False, frozenset({"zillow.com"}))],
    )
    async def test_approval_setting_controls_the_auto_send_scope(
        self, monkeypatch, require_approval, expected_scope
    ):
        monkeypatch.setattr(
            queue,
            "get_zillow_email_config",
            AsyncMock(return_value={"debounce_seconds": 0, "require_approval": require_approval}),
        )
        await enqueue(thread_id="T1")
        await queue.process_zillow_email_queue()
        scope = queue.execute_trigger_run.await_args.kwargs["auto_approved_reply_domains"]
        assert scope == expected_scope

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_outcome",
        [None, RunOutcome(persisted=False), RuntimeError("gmail down")],
        ids=["run crashed", "not saved", "unexpected error"],
    )
    async def test_failed_run_is_retried_later_not_dropped(self, bad_outcome):
        """The old in-memory trigger lost the lead on any failure."""
        if isinstance(bad_outcome, Exception):
            queue.execute_trigger_run.side_effect = bad_outcome
        else:
            queue.execute_trigger_run.return_value = bad_outcome
        message_id = await enqueue(thread_id="T1")
        await queue.process_zillow_email_queue()

        row = (await rows())[message_id]
        assert row.state == "pending"
        assert row.attempts == 1
        assert row.last_error
        assert row.next_attempt_at > datetime.now(UTC) + timedelta(minutes=1)  # backoff

        # Recovers on the retry.
        queue.execute_trigger_run.side_effect = None
        queue.execute_trigger_run.return_value = OK
        claim = await queue.claim_next_thread(now=later(minutes=3))
        await queue.process_thread(claim)
        row = (await rows())[message_id]
        assert (row.state, row.attempts, row.last_error) == ("done", 2, None)

    @pytest.mark.asyncio
    async def test_run_that_always_fails_is_parked_after_max_attempts(self):
        queue.execute_trigger_run.return_value = None
        message_id = await enqueue(thread_id="T1")
        for attempt in range(queue.MAX_ATTEMPTS):
            claim = await queue.claim_next_thread(now=later(hours=attempt))
            await queue.process_thread(claim)

        assert (await rows())[message_id].state == "failed"
        assert await queue.claim_next_thread(now=later(hours=10)) is None
        assert queue.execute_trigger_run.await_count == queue.MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_a_new_email_does_not_inherit_an_old_batchs_failures(self):
        """A lead's fresh message must get its own three attempts, not be
        parked alongside an older batch that has just used its last one."""
        queue.execute_trigger_run.return_value = None  # every run crashes
        old = await enqueue(thread_id="T1")
        for attempt in range(queue.MAX_ATTEMPTS - 1):
            await queue.process_thread(await queue.claim_next_thread(now=later(hours=attempt)))
        fresh = await enqueue(thread_id="T1")

        last_try = await queue.claim_next_thread(now=later(hours=5))
        assert [e.message_id for e in last_try.emails] == [old]
        await queue.process_thread(last_try)

        state = await rows()
        assert (state[old].state, state[old].attempts) == ("failed", queue.MAX_ATTEMPTS)
        assert (state[fresh].state, state[fresh].attempts) == ("pending", 0)

        first_try = await queue.claim_next_thread(now=later(hours=6))
        assert [e.message_id for e in first_try.emails] == [fresh]
        assert first_try.attempt == 1

    @pytest.mark.asyncio
    async def test_retry_after_a_saved_run_does_not_run_the_agent_again(self, monkeypatch):
        """Worker died after the conversation was saved but before the jobs
        were marked done. Running again would draft or send a second reply."""
        monkeypatch.setattr(queue, "_already_completed", AsyncMock(return_value=True))
        message_id = await enqueue(thread_id="T1")
        await queue.process_zillow_email_queue()

        queue.execute_trigger_run.assert_not_called()
        assert (await rows())[message_id].state == "done"

    @pytest.mark.asyncio
    async def test_email_arriving_mid_run_gets_its_own_later_run(self):
        first = await enqueue(thread_id="T1")
        claim = await queue.claim_next_thread()
        late = await enqueue(thread_id="T1")  # lands while the agent is working
        await queue.process_thread(claim)

        state = await rows()
        assert (state[first].state, state[late].state) == ("done", "pending")
        follow_up = await queue.claim_next_thread()
        assert [e.message_id for e in follow_up.emails] == [late]
        assert follow_up.conversation_id != claim.conversation_id

    @pytest.mark.asyncio
    async def test_worker_does_nothing_while_automation_is_off(self, monkeypatch):
        message_id = await enqueue(thread_id="T1")
        monkeypatch.setattr(queue, "is_sernia_ai_enabled", AsyncMock(return_value=False))
        await queue.process_zillow_email_queue()
        queue.execute_trigger_run.assert_not_called()
        assert (await rows())[message_id].state == "pending"


class TestAlreadyCompleted:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("conversation", "expected"),
        [
            (None, False),
            (SimpleNamespace(metadata_={"trigger_source": "zillow_email_event"}), True),
            (SimpleNamespace(metadata_=None), True),
            (SimpleNamespace(metadata_={"partial": True, "error": True}), False),
        ],
        ids=["never saved", "saved in full", "saved, no metadata", "partial from a crash"],
    )
    async def test_only_a_fully_saved_conversation_counts(
        self, monkeypatch, conversation, expected
    ):
        monkeypatch.undo()  # use the real _already_completed
        monkeypatch.setattr(queue, "get_agent_conversation", AsyncMock(return_value=conversation))
        assert await queue._already_completed("zillow-T1-1") is expected
