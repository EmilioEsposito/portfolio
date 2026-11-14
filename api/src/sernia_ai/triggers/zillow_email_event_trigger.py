"""
Zillow email event trigger — a durable queue in front of the lead agent.

When a Zillow email arrives via Gmail Pub/Sub it is written to the
``zillow_email_jobs`` table. A scheduled worker picks up due jobs, groups them
by lead thread, and hands each thread to the dedicated lead agent
(``zillow_leads``), once per thread. The general Sernia agent is not involved.

Lifecycle of a job row::

    pending --claim--> running --ok----> done
       ^                  |
       |                  +--error--> pending (retry with backoff)
       |                  |              or failed (retries exhausted)
       +--lease ran out---+
    pending/running --too old--> expired

Why a table instead of a list in memory: the previous version kept arriving
emails in a module-level list and slept for the debounce window. A deploy or
crash inside that window lost the leads, and a run that failed was never tried
again. Now the email is recorded before anything else happens, so a restart
resumes where it left off.

The guarantees and what provides them:

- **Nothing is lost** — the row is committed when the email arrives.
- **Nothing is queued twice** — ``message_id`` is unique, so Pub/Sub
  redeliveries and Gmail label-change notifications insert nothing.
- **One run per thread at a time** — a claim marks rows ``running`` under a
  lease (``lease_until``), and a thread with a live lease is not claimable at
  all: an email that arrives mid-run waits for that run to finish. Claims are
  serialized by a Postgres advisory lock, so two workers can never split one
  thread's emails between them.
- **A dead worker's jobs come back** — a ``running`` row whose lease has run
  out is claimable again.
- **A retry never duplicates work** — delivery is *at-least-once*, so the same
  emails can be handed to the agent twice. Two things make that safe: a batch
  is given its conversation ID on first claim and keeps it (a retry overwrites,
  it doesn't add a second conversation); and a batch whose conversation was
  already saved complete is marked done without running again. Nothing is sent
  by a run: every reply waits for a person, and the send happens once, on
  approval (``zillow_leads/handle.py``).

Batching: the first email in a thread starts that thread's debounce window
(``zillow_lead_config.debounce_seconds``); further emails in the same thread
before it fires ride along in the same run. Once claimed, a batch's
membership is fixed — later emails form the next batch, which keeps retries
looking at the same conversation and counting their own attempts.

Configuration: the debounce length is overridable via the DB-backed
``zillow_lead_config`` AppSetting (JSONB), ``{"debounce_seconds": 300}``; the
default is ``DEFAULT_ZILLOW_DEBOUNCE_SECONDS`` in ``config.py``.

The dedicated agent reads `zillow_lead_config` for both batching and approval.
Before it is saved, only the old batching window is inherited; the old
`require_approval` flag can never enable automatic delivery in the new agent.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

import logfire
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.src.agent_conversations.models import get_agent_conversation
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.models import ZillowEmailJob, is_sernia_ai_enabled
from api.src.sernia_ai.zillow_leads.handle import handle_thread
from api.src.sernia_ai.zillow_leads.thread import SharedCopy, find_shared_copy

# Zillow's mail domain. Lead inquiries arrive from (and are answered at) a
# relay address under it, so this one constant decides both which inbound
# email wakes the agent and where an auto-approved reply may go.
ZILLOW_EMAIL_DOMAIN = "zillow.com"

TRIGGER_SOURCE = "zillow_email_event"
JOB_ID = "zillow-email-queue"

# How often the worker looks for due jobs.
POLL_SECONDS = 30
# A job is dropped rather than run once the email is this old. It bounds the
# backlog after an outage, and it is what makes label changes on old emails
# harmless (they re-notify through Pub/Sub long after the lead went cold).
JOB_MAX_AGE = timedelta(hours=24)
# How long a worker owns the rows it claimed. Longer than any real agent run;
# after this a crashed worker's jobs are handed out again.
LEASE = timedelta(minutes=30)
# Runs per job before it is parked as ``failed`` for a human to look at.
MAX_ATTEMPTS = 3
# Wait before retry N: 2 min, then 4 min.
RETRY_BASE = timedelta(minutes=2)
# Threads handled per worker tick, so one tick can't run unbounded.
MAX_THREADS_PER_TICK = 10
# Arbitrary constant naming this queue's advisory lock.
_CLAIM_LOCK_KEY = 0x5A11_10AB

_STATE_PENDING = "pending"
_STATE_RUNNING = "running"
_STATE_DONE = "done"
_STATE_FAILED = "failed"
_STATE_EXPIRED = "expired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def get_zillow_email_config() -> dict:
    """Compatibility name for the dedicated lead agent's effective settings."""
    from api.src.sernia_ai.zillow_leads.settings import effective_settings

    return (await effective_settings()).model_dump()


def is_zillow_email(from_address: str) -> bool:
    """Return True if the sender's address is at zillow.com or a subdomain.

    Accepts a raw ``From`` header (``"Zillow" <x@convo.zillow.com>``) as well
    as a bare address. Only the parsed address's domain counts: a substring
    test would also accept ``"noreply@zillow.com" <attacker@evil.com>`` and
    ``lead@zillow.com.evil.com``, and this check decides whether a stranger's
    email wakes the agent.
    """
    if not from_address:
        return False
    domain = parseaddr(from_address)[1].lower().rpartition("@")[2]
    return domain == ZILLOW_EMAIL_DOMAIN or domain.endswith(f".{ZILLOW_EMAIL_DOMAIN}")


# ---------------------------------------------------------------------------
# Enqueue — called from the Pub/Sub webhook
# ---------------------------------------------------------------------------


async def enqueue_zillow_email(
    *,
    message_id: str,
    thread_id: str,
    subject: str | None,
    from_address: str | None,
    received_at: datetime | None = None,
) -> bool:
    """Record a Zillow email for processing. Returns True if a job was created.

    Safe to call any number of times for the same email: only the first call
    inserts. That is deliberate — the caller does not have to work out whether
    a Pub/Sub notification is a new email, a redelivery, or a label change.

    Nothing is queued when automated runs are switched off (the kill switch;
    always off outside production), or when the email is already older than
    ``JOB_MAX_AGE``. Raises if the insert fails, so the webhook reports the
    message as failed and Pub/Sub delivers it again.
    """
    if not message_id or not thread_id:
        logfire.warn("zillow_email_queue: email without ids not queued", subject=subject)
        return False
    if not await is_sernia_ai_enabled():
        logfire.info("zillow_email_queue: sernia_ai disabled — not queued", message_id=message_id)
        return False

    now = _utcnow()
    if received_at is not None and received_at.tzinfo is None:
        # A Date header without an offset parses to a naive datetime; comparing
        # it with `now` would raise, and since a raise makes Pub/Sub redeliver,
        # it would raise again on every redelivery. Read it as UTC.
        received_at = received_at.replace(tzinfo=UTC)
    expires_at = (received_at or now) + JOB_MAX_AGE
    if expires_at <= now:
        logfire.info(
            "zillow_email_queue: email too old — not queued",
            message_id=message_id,
            received_at=received_at,
        )
        return False

    config = await get_zillow_email_config()
    stmt = (
        pg_insert(ZillowEmailJob)
        .values(
            message_id=message_id,
            thread_id=thread_id,
            subject=subject,
            from_address=from_address,
            state=_STATE_PENDING,
            attempts=0,
            created_at=now,
            next_attempt_at=now + timedelta(seconds=config["debounce_seconds"]),
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(ZillowEmailJob.id)
    )
    async with AsyncSessionFactory() as session:
        inserted = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()

    if inserted is None:
        logfire.info("zillow_email_queue: already queued", message_id=message_id)
        return False
    logfire.info(
        "zillow_email_queue: queued",
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        debounce_seconds=config["debounce_seconds"],
    )
    return True


# ---------------------------------------------------------------------------
# Claim / complete / fail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimedEmail:
    job_id: int
    message_id: str
    thread_id: str
    subject: str
    from_address: str


@dataclass(frozen=True)
class ClaimedThread:
    """One batch of a lead thread's jobs, owned by this worker until the lease ends."""

    thread_id: str
    lease_token: str
    emails: tuple[ClaimedEmail, ...]
    attempt: int  # which attempt this is for the batch (1 on first run)
    # Fixed when the batch is first claimed and stored on its rows, so every
    # retry of the batch writes to the same conversation.
    conversation_id: str

    @property
    def job_ids(self) -> list[int]:
        return [e.job_id for e in self.emails]


def _claimable(now: datetime):
    """Rows a worker may take: waiting, or held by a worker whose lease ran out."""
    return or_(
        ZillowEmailJob.state == _STATE_PENDING,
        and_(ZillowEmailJob.state == _STATE_RUNNING, ZillowEmailJob.lease_until <= now),
    )


async def claim_next_thread(now: datetime | None = None) -> ClaimedThread | None:
    """Take ownership of the jobs of the next due lead thread, if any.

    A thread is due once its earliest waiting job's ``next_attempt_at`` has
    passed; every claimable job of that thread comes along. One transaction:
    expire stale jobs, park jobs out of retries, then mark the rest ``running``
    under a fresh lease.
    """
    now = now or _utcnow()
    async with AsyncSessionFactory() as session, session.begin():
        # Serialize claimers. Without this, two workers could each take part
        # of one thread and run the agent on it at the same time.
        if not await session.scalar(select(func.pg_try_advisory_xact_lock(_CLAIM_LOCK_KEY))):
            return None

        expired = await session.execute(
            update(ZillowEmailJob)
            .where(_claimable(now), ZillowEmailJob.expires_at <= now)
            .values(
                state=_STATE_EXPIRED,
                lease_token=None,
                lease_until=None,
                last_error="expired before it could be processed",
            )
            .returning(ZillowEmailJob.message_id)
        )
        if expired_ids := expired.scalars().all():
            logfire.warn("zillow_email_queue: jobs expired unprocessed", message_ids=expired_ids)

        exhausted = await session.execute(
            update(ZillowEmailJob)
            .where(_claimable(now), ZillowEmailJob.attempts >= MAX_ATTEMPTS)
            .values(state=_STATE_FAILED, lease_token=None, lease_until=None)
            .returning(ZillowEmailJob.message_id)
        )
        if failed_ids := exhausted.scalars().all():
            logfire.error(
                "zillow_email_queue: jobs failed permanently — needs a human",
                message_ids=failed_ids,
                max_attempts=MAX_ATTEMPTS,
            )

        batch = await _next_due_batch(session, now)
        if batch is None:
            return None

        # Read everything off the rows before the UPDATE below changes them.
        thread_id = batch[0].thread_id
        job_ids = [r.id for r in batch]
        attempt = max(r.attempts for r in batch) + 1
        emails = tuple(
            ClaimedEmail(
                job_id=r.id,
                message_id=r.message_id,
                thread_id=thread_id,
                subject=r.subject or "",
                from_address=r.from_address or "",
            )
            for r in batch
        )
        # A batch keeps the conversation ID it got on its first claim. That is
        # what lets a retry find the conversation an earlier attempt saved.
        conversation_id = batch[0].conversation_id or f"zillow-{thread_id}-{max(job_ids)}"

        token = uuid.uuid4().hex
        await session.execute(
            update(ZillowEmailJob)
            .where(ZillowEmailJob.id.in_(job_ids))
            .values(
                state=_STATE_RUNNING,
                lease_token=token,
                lease_until=now + LEASE,
                attempts=ZillowEmailJob.attempts + 1,
                conversation_id=conversation_id,
            )
            .execution_options(synchronize_session=False)
        )
    return ClaimedThread(
        thread_id=thread_id,
        lease_token=token,
        emails=emails,
        attempt=attempt,
        conversation_id=conversation_id,
    )


async def _next_due_batch(session, now: datetime) -> list[ZillowEmailJob] | None:
    """Choose the batch to run next. Call only while holding the claim lock.

    A **batch** is the set of a thread's jobs that are run together. Jobs
    never claimed before (no ``conversation_id`` yet) form a new batch;
    jobs claimed before keep their batch, identified by the conversation ID
    they were given then. Membership is therefore fixed at first claim: a
    later email in the thread never joins a batch that is being retried, so
    the retry looks up the same conversation, and each batch counts its own
    attempts.

    Rules, per thread:

    - A thread with a live lease is skipped entirely. Its newer emails wait
      until the run in progress finishes — two runs on one lead at once
      could send conflicting replies.
    - Batches run oldest first; only the head batch is considered.
    - The head batch is due once its earliest ``next_attempt_at`` has passed.

    The queue is small (a busy season is a few leads an hour), so the
    selection is done here rather than in one large SQL statement.
    """
    leased_threads = set(
        await session.scalars(
            select(ZillowEmailJob.thread_id).where(
                ZillowEmailJob.state == _STATE_RUNNING, ZillowEmailJob.lease_until > now
            )
        )
    )
    candidates = (
        await session.scalars(
            select(ZillowEmailJob).where(_claimable(now)).order_by(ZillowEmailJob.id)
        )
    ).all()

    by_thread: dict[str, list[ZillowEmailJob]] = {}
    for row in candidates:
        if row.thread_id not in leased_threads:
            by_thread.setdefault(row.thread_id, []).append(row)

    due: list[tuple[datetime, list[ZillowEmailJob]]] = []
    for rows in by_thread.values():
        head = [r for r in rows if r.conversation_id == rows[0].conversation_id]
        ready_at = min(r.next_attempt_at for r in head)
        if ready_at <= now:
            due.append((ready_at, head))
    if not due:
        return None
    return min(due, key=lambda item: item[0])[1]


async def _finish(claim: ClaimedThread, **values) -> int:
    """Write a result onto the claim's rows — only if this worker still owns them.

    The ``lease_token`` match is the fence: a worker that outlived its lease
    finds its rows re-claimed under another token and changes nothing.
    """
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            update(ZillowEmailJob)
            .where(
                ZillowEmailJob.id.in_(claim.job_ids),
                ZillowEmailJob.lease_token == claim.lease_token,
            )
            .values(lease_token=None, lease_until=None, **values)
            .returning(ZillowEmailJob.id)
        )
        owned = len(result.scalars().all())
        await session.commit()
    if owned != len(claim.job_ids):
        logfire.warn(
            "zillow_email_queue: lease was lost before the result was written",
            thread_id=claim.thread_id,
            owned=owned,
            claimed=len(claim.job_ids),
        )
    return owned


async def complete_thread(claim: ClaimedThread) -> None:
    await _finish(claim, state=_STATE_DONE, last_error=None)


async def fail_thread(claim: ClaimedThread, error: str) -> None:
    """Put the jobs back for a later retry, or park them when out of attempts."""
    if claim.attempt >= MAX_ATTEMPTS:
        await _finish(claim, state=_STATE_FAILED, last_error=error)
        logfire.error(
            "zillow_email_queue: thread failed permanently — needs a human",
            thread_id=claim.thread_id,
            message_ids=[e.message_id for e in claim.emails],
            attempts=claim.attempt,
            error=error,
        )
        return
    delay = RETRY_BASE * 2 ** (claim.attempt - 1)
    await _finish(claim, state=_STATE_PENDING, next_attempt_at=_utcnow() + delay, last_error=error)
    logfire.warn(
        "zillow_email_queue: thread will be retried",
        thread_id=claim.thread_id,
        attempt=claim.attempt,
        retry_in_seconds=int(delay.total_seconds()),
        error=error,
    )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


async def find_shared_copies(emails: Sequence[ClaimedEmail]) -> dict[str, SharedCopy]:
    """Each queued email's copy in the shared mailbox, keyed by its queued message ID.

    The queue holds IDs from the watched mailbox; the agent reads and replies
    in the shared one, where the IDs are different. An email with no shared
    copy is left out and the caller hands the agent its original ID instead.
    """
    copies: dict[str, SharedCopy] = {}
    for email in emails:
        copy = await find_shared_copy(email.message_id)
        if copy is None:
            logfire.warn(
                "zillow_email_queue: no shared-mailbox copy found",
                message_id=email.message_id,
                thread_id=email.thread_id,
            )
        else:
            copies[email.message_id] = copy
    return copies


async def _already_completed(conversation_id: str) -> bool:
    """True if an earlier attempt saved this conversation in full.

    Covers the gap between "the run finished and was saved" and "the jobs
    were marked done": a worker that died in between must not run the agent
    again — it would draft (or send) a second reply.
    """
    async with AsyncSessionFactory() as session:
        conversation = await get_agent_conversation(session, conversation_id, retries=1)
    if conversation is None:
        return False
    return not (conversation.metadata_ or {}).get("partial")


async def process_thread(claim: ClaimedThread) -> None:
    """Hand one claimed lead thread to the lead agent and record the result. Never raises.

    Zillow email goes to the dedicated lead agent and nowhere else. It used to
    start a run of the general Sernia agent, which can read company email,
    files and contacts and can send messages. A lead's email is text from a
    stranger, and that agent would read it with all of that within reach, so
    the only thing between an instruction hidden in an inquiry and an outbound
    message was a person approving every send. The lead agent has no tools and
    sees nothing private (see ``zillow_leads/agent.py``), which is what makes
    possible to opt into automatic routine replies in its own settings. Do not route
    this queue back to the general agent.
    """
    conversation_id = claim.conversation_id
    try:
        if await _already_completed(conversation_id):
            logfire.info(
                "zillow_email_queue: already completed by an earlier attempt",
                thread_id=claim.thread_id,
                conversation_id=conversation_id,
            )
            await complete_thread(claim)
            return

        # The queue's IDs are from the watched mailbox; the lead agent works
        # in the shared one. The newest email that has a copy there names the
        # thread.
        copies = await find_shared_copies(claim.emails)
        shared = next(
            (copies[e.message_id] for e in reversed(claim.emails) if e.message_id in copies), None
        )
        if shared is None:
            await fail_thread(claim, "no copy in the shared mailbox yet")
            return

        handled = await handle_thread(
            shared.thread_id,
            conversation_id=conversation_id,
            metadata={
                "trigger_source": TRIGGER_SOURCE,
                "trigger_type": "email_event",
                "email_count": len(claim.emails),
                "subjects": [e.subject for e in claim.emails],
                "thread_ids": [claim.thread_id],
            },
        )
    except Exception as exc:
        logfire.exception("zillow_email_queue: processing error", thread_id=claim.thread_id)
        await fail_thread(claim, f"{type(exc).__name__}: {exc}")
        return

    if handled is None:
        await fail_thread(claim, "thread not found in the shared mailbox")
    elif not handled.persisted:
        await fail_thread(claim, "conversation was not saved")
    else:
        await complete_thread(claim)
        logfire.info(
            "zillow_email_queue: thread processed",
            thread_id=claim.thread_id,
            conversation_id=conversation_id,
            email_count=len(claim.emails),
            action=handled.action,
            has_pending=handled.pending,
        )


# Held while a tick is working. An agent run outlasts the poll interval, so
# the next tick arrives mid-run; it sees the lock and leaves quietly.
_worker_busy = asyncio.Lock()


async def process_zillow_email_queue() -> None:
    """One worker tick: run every due lead thread, one at a time.

    Each thread is claimed just before it runs, so its lease starts fresh
    rather than ticking away behind the threads ahead of it.
    """
    if _worker_busy.locked() or not await is_sernia_ai_enabled():
        return
    async with _worker_busy:
        for _ in range(MAX_THREADS_PER_TICK):
            claim = await claim_next_thread()
            if claim is None:
                return
            await process_thread(claim)


def register_zillow_email_queue() -> None:
    """Schedule the worker. Called at startup when background automation is on."""
    from api.src.apscheduler_service.service import get_scheduler, upsert_job

    upsert_job(
        get_scheduler(),
        id=JOB_ID,
        name="Sernia AI: Zillow email queue",
        func=process_zillow_email_queue,
        trigger="interval",
        seconds=POLL_SECONDS,
        # Two, so a tick that arrives while one is mid-run can start, see
        # `_worker_busy`, and return — rather than APScheduler logging a
        # "max instances reached" warning every 30s for the length of a run.
        max_instances=2,
        coalesce=True,
    )
