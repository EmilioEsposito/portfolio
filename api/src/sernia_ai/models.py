import os
from datetime import datetime

import logfire
from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base

_IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT_NAME", "") == "production"


async def is_sernia_ai_enabled() -> bool:
    """Check if automated Sernia AI runs are enabled.

    Universal kill switch for all automated agent runs — scheduled checks,
    AI SMS event triggers, and email event triggers. Web chat and HITL
    approvals are never gated by this.

    Non-production environments are hard-gated off regardless of the DB
    value. Neon PR branches inherit rows from the parent DB, so the
    ``triggers_enabled`` row in a PR env is whatever production had at
    branch time — we can't trust it to reflect operator intent on that
    environment. Forcing False on non-prod means a PR deployment will
    never fire automated triggers, even if its inherited DB row says
    True. Local dev is the same — set RAILWAY_ENVIRONMENT_NAME=production
    in a throwaway shell if you genuinely need to exercise triggers.

    On production, the DB setting acts as the kill switch: missing row
    defaults to enabled, an explicit ``false`` disables.
    """
    from api.src.database.database import AsyncSessionFactory

    if not _IS_PRODUCTION:
        return False

    enabled = True
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "triggers_enabled")
            )
            value = result.scalar_one_or_none()
            if value is not None:
                enabled = value
    except Exception:
        logfire.warn(
            "sernia_ai enabled check failed (table may not exist), using env default",
            default=enabled,
        )
    return bool(enabled)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<AppSetting(key={self.key!r})>"


class ZillowEmailJob(Base):
    """One inbound Zillow email waiting for (or finished with) an agent run.

    The durable replacement for an in-memory list: a row is written when the
    email arrives and survives restarts. ``message_id`` is the idempotency
    key — Pub/Sub may deliver the same email many times and only the first
    insert takes. The row holds routing metadata only; the agent reads the
    email itself from Gmail when it runs. See
    ``triggers/zillow_email_event_trigger.py`` for the lifecycle.
    """

    __tablename__ = "zillow_email_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String, unique=True)  # Gmail message ID
    thread_id: Mapped[str] = mapped_column(String, index=True)  # Gmail thread ID
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    from_address: Mapped[str | None] = mapped_column(String, nullable=True)
    # pending -> running -> done | failed (retries exhausted) | expired (too old)
    state: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Not processed before this time: the debounce window, then retry backoff.
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Not processed after this time: a late reply to a stale lead is worse than none.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set while a worker owns the row. A lease that runs out (the worker died)
    # makes the row claimable again; the token stops a worker that lost its
    # lease from overwriting the next owner's result.
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<ZillowEmailJob(message_id={self.message_id!r}, state={self.state!r})>"


class AutoApprovedSend(Base):
    """One row per message the agent has replied to WITHOUT human approval.

    The idempotency record for auto-approved sends. An external send is a
    side effect that cannot be undone or overwritten, and the run that makes
    it can still fail afterwards and be retried. The row is written *before*
    the send, so an auto-approved reply to a given message happens at most
    once — a second attempt finds the row and goes to a human instead.
    """

    __tablename__ = "auto_approved_sends"

    # What was replied to: the RFC Message-ID of the original email. Unlike a
    # Gmail message ID it is the same in every mailbox that holds the email.
    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def claim_auto_approved_send(
    idempotency_key: str, *, tool_name: str, conversation_id: str | None
) -> bool:
    """Reserve the one automatic send allowed for ``idempotency_key``.

    Returns True the first time a key is claimed and False ever after. Call it
    immediately before the send and only send on True. The claim is committed
    first on purpose: if the send then fails, or the process dies mid-send,
    the retry is refused and falls back to human approval. That trades a
    possibly-missing automatic reply for never sending two.

    Raises on a database error — the caller must treat that as "not claimed".
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from api.src.database.database import AsyncSessionFactory

    stmt = (
        pg_insert(AutoApprovedSend)
        .values(
            idempotency_key=idempotency_key, tool_name=tool_name, conversation_id=conversation_id
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AutoApprovedSend.idempotency_key)
    )
    async with AsyncSessionFactory() as session:
        claimed = (await session.execute(stmt)).scalar_one_or_none() is not None
        await session.commit()
    return claimed
