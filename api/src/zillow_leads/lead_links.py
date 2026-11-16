"""A lead thread's own booking-link token.

The booking link the lead agent gives a lead carries ``?t=<token>``. The
token is random, one per Zillow thread, and holds no personal data: on its
own it says nothing about the lead. When a booking arrives from the page with
a token that belongs to a thread, the booking service ties the attendee to
that thread (``showing_attendees.zillow_thread_id``), and the thread's next
case shows the lead's own showing.

``token_for_thread`` makes the token the first time it is asked for and
returns the same one ever after. ``thread_for_token`` is what the booking
service calls; an unknown or malformed token answers None.
"""

import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.zillow_leads.models import ZillowLeadLink

# What a token looks like: ``secrets.token_urlsafe(16)`` gives 22 characters.
# The booking page accepts the same alphabet for any ``?t=`` value.
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")


def new_token() -> str:
    return secrets.token_urlsafe(16)


async def token_for_thread(thread_id: str, *, now: datetime | None = None) -> str:
    """The thread's token, made on the first call. Safe to call at the same time twice."""
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            insert(ZillowLeadLink)
            .values(thread_id=thread_id, token=new_token(), created_at=now or datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[ZillowLeadLink.thread_id])
        )
        return await session.scalar(
            select(ZillowLeadLink.token).where(ZillowLeadLink.thread_id == thread_id)
        )


async def thread_for_token(token: str | None, session: AsyncSession | None = None) -> str | None:
    """The thread a token belongs to; None for an unknown or malformed token."""
    if not token or not TOKEN.fullmatch(token):
        return None
    query = select(ZillowLeadLink.thread_id).where(ZillowLeadLink.token == token)
    if session is not None:
        return await session.scalar(query)
    async with AsyncSessionFactory() as own:
        return await own.scalar(query)
