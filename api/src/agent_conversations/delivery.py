"""Idempotency reservations for automatic email delivery by either agent."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


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
