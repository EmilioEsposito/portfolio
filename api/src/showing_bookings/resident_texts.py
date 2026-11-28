"""What a unit's residents have been told, and whether one of their texts can go now.

A residents' text tells them of a showing when it is that showing's own
announcement (``residents.NEW_SHOWING_KIND``) or lists it (``showing_ids``):
several texts due together go as one (``digests.py``), and that one names every
showing it tells of. "Went" is sent, may have gone out (``unknown``), being sent
now, or captured (which counts as told while capture mode keeps everything;
``told.py`` asks again before anything is really sent).

``check`` is the delivery check of every residents' text (the announcement, the
cancellation and the 8 AM reminder): still true, not overtaking the
announcement, and only from 8 AM to 9 PM. Nothing here sends or changes a row
but the one it is asked about.
"""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import residents
from api.src.showing_bookings.delivery_checks import (
    GO,
    NOTHING_LEFT,
    RESIDENT_REMINDER_KIND,
    Verdict,
)
from api.src.showing_bookings.models import Showing, ShowingNotice
from api.src.showing_bookings.schema import TextPayload

KINDS = residents.TEXT_KINDS | {RESIDENT_REMINDER_KIND}
WENT = ("sent", "captured", "unknown")
# How long a cancellation waits while the announcement it follows is being sent.
WAIT = timedelta(minutes=1)


def _tells(showing_id: int):
    """A residents' text about ``showing_id``: its announcement, or one listing it."""
    return or_(
        and_(
            ShowingNotice.showing_id == showing_id,
            ShowingNotice.kind == residents.NEW_SHOWING_KIND,
        ),
        and_(
            ShowingNotice.kind.in_(KINDS),
            ShowingNotice.payload["showing_ids"].contains([showing_id]),
        ),
    )


def _went(status: str, started: object, *, captured: bool = True) -> bool:
    went = WENT if captured else ("sent", "unknown")
    return status in went or (status == "pending" and started is not None)


async def told_ids(
    session: AsyncSession, showing_ids: list[int], *, captured: bool = True
) -> set[int]:
    """The showings of ``showing_ids`` their residents were told of. With
    ``captured`` False, a text only captured does not count."""
    found = set()
    for showing_id in showing_ids:
        rows = await session.execute(
            select(ShowingNotice.status, ShowingNotice.sending_started_at).where(_tells(showing_id))
        )
        if any(_went(status, started, captured=captured) for status, started in rows):
            found.add(showing_id)
    return found


async def announcement_state(
    session: AsyncSession, showing_id: int, *, captured: bool = True
) -> Literal["went", "sending", "none"]:
    """Whether the residents were told of ``showing_id``: "went", "sending" while the
    text that tells them is being sent, or "none"."""
    rows = (
        await session.execute(
            select(ShowingNotice.status, ShowingNotice.sending_started_at).where(_tells(showing_id))
        )
    ).all()
    if any(status in (WENT if captured else ("sent", "unknown")) for status, _ in rows):
        return "went"
    if any(status == "pending" and started is not None for status, started in rows):
        return "sending"
    return "none"


async def telling_notice(session: AsyncSession, showing_id: int) -> ShowingNotice | None:
    """The latest residents' text that told them of ``showing_id``, whose numbers a
    cancellation goes to."""
    rows = list(
        await session.scalars(
            select(ShowingNotice).where(_tells(showing_id)).order_by(ShowingNotice.id.desc())
        )
    )
    return next((row for row in rows if _went(row.status, row.sending_started_at)), None)


async def check(session: AsyncSession, notice: ShowingNotice, now: datetime) -> Verdict:
    """Whether a residents' text can go now.

    - The announcement: only while its showing is scheduled and ahead.
    - The cancellation: never ahead of what told them of the showing; it waits
      a minute while that is being sent, and is cancelled when nothing did.
    - The 8 AM reminder: while one of the day's showings it lists is ahead.
    - Each only from 8 AM to 9 PM, waiting for 8 AM otherwise.
    """
    payload = TextPayload.model_validate(notice.payload)
    if notice.kind == RESIDENT_REMINDER_KIND:
        ahead = await session.scalar(
            select(Showing.id)
            .where(
                Showing.id.in_(payload.showing_ids),
                Showing.status == "scheduled",
                Showing.starts_at > now,
            )
            .limit(1)
        )
        if ahead is None:
            return Verdict(cancel=NOTHING_LEFT)
    else:
        showing = (
            await session.execute(
                select(Showing)
                .where(Showing.id == notice.showing_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if notice.kind == residents.NEW_SHOWING_KIND and showing.status != "scheduled":
            return Verdict(cancel=residents.SHOWING_CANCELLED_NOTE)
        if showing.starts_at <= now:
            return Verdict(cancel=residents.TOO_LATE_NOTE)
        if notice.kind == residents.CANCELLED_KIND:
            state = await announcement_state(session, notice.showing_id)
            if state == "sending":
                return Verdict(later=now + WAIT)
            if state == "none":
                return Verdict(cancel=residents.NEVER_TOLD_NOTE)
    timezone = payload.daytime_timezone
    later = residents.next_daytime(now, timezone) if timezone else None
    return Verdict(later=later) if later is not None else GO
