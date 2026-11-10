"""
Zillow email event trigger for the Sernia AI agent — a durable queue.

When a Zillow email arrives via Gmail Pub/Sub it is written to the
``zillow_email_jobs`` table. A scheduled worker picks up due jobs, groups them
by lead thread, and runs the agent once per thread.

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
- **One worker per thread** — a claim marks rows ``running`` under a lease
  (``lease_until``). Claims are serialized by a Postgres advisory lock, so two
  workers can never split one thread's emails between them.
- **A dead worker's jobs come back** — a ``running`` row whose lease has run
  out is claimable again.
- **A retry never duplicates work** — delivery is *at-least-once*, so the same
  thread can be handed to the agent twice. Two things make that safe: the run
  writes to a conversation ID derived from the jobs (a retry overwrites, it
  doesn't add a second conversation), and a job whose conversation was already
  saved complete is marked done without running again.

Batching: the first email in a thread starts that thread's debounce window
(``zillow_email_config.debounce_seconds``); further emails in the same thread
before it fires ride along in the same run.

Configuration: debounce length and the HITL approval gate are both
overridable via the DB-backed ``zillow_email_config`` AppSetting (JSONB):

    {
        "debounce_seconds": 300,
        "require_approval": true
    }

Defaults come from ``DEFAULT_ZILLOW_DEBOUNCE_SECONDS`` and
``DEFAULT_ZILLOW_REQUIRE_APPROVAL`` in ``config.py``. ``require_approval=False``
lets the agent send threaded replies to Zillow relay addresses without the
standard external email HITL approval card. Email to any other external
address still pauses for approval, even in these runs.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from textwrap import dedent

import logfire
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.src.ai_demos.models import get_agent_conversation
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.config import (
    DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
    DEFAULT_ZILLOW_REQUIRE_APPROVAL,
    FRONTEND_BASE_URL,
)
from api.src.sernia_ai.models import AppSetting, ZillowEmailJob, is_sernia_ai_enabled
from api.src.sernia_ai.triggers.background_agent_runner import execute_trigger_run

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
    """Read ``zillow_email_config`` from the DB, returning defaults if not set.

    Defaults come from ``config.DEFAULT_ZILLOW_*`` so the constants stay the
    single source of truth. Failures fall back to defaults — never raises.
    """
    defaults = {
        "debounce_seconds": DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
        "require_approval": DEFAULT_ZILLOW_REQUIRE_APPROVAL,
    }
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "zillow_email_config")
            )
            row = result.scalar_one_or_none()
            if isinstance(row, dict):
                return {
                    "debounce_seconds": int(
                        row.get("debounce_seconds", defaults["debounce_seconds"])
                    ),
                    "require_approval": bool(
                        row.get("require_approval", defaults["require_approval"])
                    ),
                }
    except Exception:
        logfire.warn("Failed to read zillow_email_config from DB, using defaults")
    return defaults


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
    """The jobs of one lead thread, owned by this worker until the lease ends."""

    thread_id: str
    lease_token: str
    emails: tuple[ClaimedEmail, ...]
    attempt: int  # the highest attempt number among the jobs (1 on first run)

    @property
    def job_ids(self) -> list[int]:
        return [e.job_id for e in self.emails]

    @property
    def conversation_id(self) -> str:
        """Same jobs -> same conversation, so a retry overwrites, not duplicates."""
        return f"zillow-{self.thread_id}-{max(self.job_ids)}"


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

        thread_id = await session.scalar(
            select(ZillowEmailJob.thread_id)
            .where(_claimable(now))
            .group_by(ZillowEmailJob.thread_id)
            .having(func.min(ZillowEmailJob.next_attempt_at) <= now)
            .order_by(func.min(ZillowEmailJob.next_attempt_at))
            .limit(1)
        )
        if thread_id is None:
            return None

        token = uuid.uuid4().hex
        rows = (
            await session.execute(
                update(ZillowEmailJob)
                .where(_claimable(now), ZillowEmailJob.thread_id == thread_id)
                .values(
                    state=_STATE_RUNNING,
                    lease_token=token,
                    lease_until=now + LEASE,
                    attempts=ZillowEmailJob.attempts + 1,
                )
                .returning(
                    ZillowEmailJob.id,
                    ZillowEmailJob.message_id,
                    ZillowEmailJob.subject,
                    ZillowEmailJob.from_address,
                    ZillowEmailJob.attempts,
                )
            )
        ).all()

    rows = sorted(rows, key=lambda r: r.id)
    return ClaimedThread(
        thread_id=thread_id,
        lease_token=token,
        emails=tuple(
            ClaimedEmail(
                job_id=r.id,
                message_id=r.message_id,
                thread_id=thread_id,
                subject=r.subject or "",
                from_address=r.from_address or "",
            )
            for r in rows
        ),
        attempt=max(r.attempts for r in rows),
    )


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
    await _finish(claim, state=_STATE_DONE, conversation_id=claim.conversation_id, last_error=None)


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


def build_trigger_prompt(
    emails: tuple[ClaimedEmail, ...] | list[ClaimedEmail], deeplink: str
) -> str:
    """The synthetic user message that starts a Zillow run."""
    if len(emails) == 1:
        email = emails[0]
        email_details = dedent(f"""\
            **Email details:**
            - Gmail Message ID (all@ account): {email.message_id}
            - Thread ID (Gmail): {email.thread_id}
            - Subject: {email.subject}
            - From: {email.from_address}""")
        reply_hint = (
            f'When replying, pass reply_to_message_id="{email.message_id}" to thread correctly.'
        )
    else:
        lines = [
            f"{i}. Subject: {email.subject} | From: {email.from_address} "
            f"| Message ID: {email.message_id} | Thread ID: {email.thread_id}"
            for i, email in enumerate(emails, 1)
        ]
        email_details = "**Emails received (oldest first):**\n" + "\n".join(lines)
        reply_hint = "For each email that needs a reply, pass the corresponding reply_to_message_id to thread correctly."

    return dedent(f"""\
        {len(emails)} new Zillow email(s) arrived. Load the zillow-auto-reply skill and follow it.

        {email_details}
        - Conversation deeplink: {deeplink}

        Read each email using its Message ID, then draft replies or NoAction per email.
        {reply_hint}
        Always search/read from all@serniacapital.com (use user_email_account="all@serniacapital.com").""")


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
    """Run the agent for one claimed lead thread and record the result. Never raises."""
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

        config = await get_zillow_email_config()
        outcome = await execute_trigger_run(
            trigger_source=TRIGGER_SOURCE,
            trigger_prompt=build_trigger_prompt(
                claim.emails, f"{FRONTEND_BASE_URL}/sernia-chat?id={conversation_id}"
            ),
            trigger_metadata={
                "trigger_source": TRIGGER_SOURCE,
                "trigger_type": "email_event",
                "email_count": len(claim.emails),
                "subjects": [e.subject for e in claim.emails],
                "thread_ids": [claim.thread_id],
            },
            conversation_id=conversation_id,
            auto_approved_reply_domains=(
                frozenset() if config["require_approval"] else frozenset({ZILLOW_EMAIL_DOMAIN})
            ),
        )
    except Exception as exc:
        logfire.exception("zillow_email_queue: processing error", thread_id=claim.thread_id)
        await fail_thread(claim, f"{type(exc).__name__}: {exc}")
        return

    if outcome is None:
        await fail_thread(claim, "agent run crashed")
    elif not outcome.persisted:
        await fail_thread(claim, "conversation was not saved")
    else:
        await complete_thread(claim)
        logfire.info(
            "zillow_email_queue: thread processed",
            thread_id=claim.thread_id,
            conversation_id=conversation_id,
            email_count=len(claim.emails),
            has_pending=bool(outcome.pending),
            no_action=outcome.no_action_reason is not None,
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
