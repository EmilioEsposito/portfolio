"""Durable jobs for the Zillow lead queue; table names are deployment-stable."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


class ZillowEmailJob(Base):
    """One inbound Zillow email waiting for (or finished with) an agent run.

    The durable replacement for an in-memory list: a row is written when the
    email arrives and survives restarts. ``message_id`` is the idempotency
    key — Pub/Sub may deliver the same email many times and only the first
    insert takes. The row holds routing metadata only; the agent reads the
    email itself from Gmail when it runs. See
    ``queue.py`` for the lifecycle.
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
