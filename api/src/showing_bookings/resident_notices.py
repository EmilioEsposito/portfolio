"""The texts a unit's residents get: a new showing at their home, and its cancellation.

- ``queue_new_showing``: in the booking transaction, for a new showing only
  (someone joining a showing already booked texts the residents nothing).
  One notice for all of the unit's residents (``residents.RECIPIENT_KEY``): an
  ordinary text for one resident, one group text for roommates, so they share
  one thread. It is held behind the booking prospect's confirmation text like
  the host's event, and stopped with it. A unit with no resident on a current
  lease gets nothing. A lookup that went wrong queues nothing and marks the
  showing for the team (``showings.resident_attention``); the booking stands.
- ``queue_cancellation``: when a showing is cancelled, if the residents' text
  went out (sent or captured), may have (unknown), or is being sent now. One
  that never went out was stopped with the showing's other notices, and the
  residents hear nothing.
- ``hand_over``: the residents' text held behind a confirmation that will not
  be delivered waits instead for another booked person's confirmation, so a
  showing that still goes ahead is still announced.

Both texts carry the showing rules' time zone, and the outbox sends them only
from 8 AM to 9 PM there.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import logfire
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import host_event, outbox, residents, wording
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.schema import TextPayload


async def find_for(unit: str, start: datetime, rules: ShowingRules) -> residents.Found:
    """The residents of ``unit`` on the local day of ``start``. Never raises for a
    failed lookup; call it before the booking transaction, as it may read Quo."""
    day = start.astimezone(ZoneInfo(rules.timezone)).date()
    return await residents.find(unit, day)


def _visit(showing: Showing, timezone: str) -> wording.Visit:
    return wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=timezone,
    )


async def queue_new_showing(
    session: AsyncSession,
    showing: Showing,
    found: residents.Found,
    *,
    rules: ShowingRules,
    now: datetime,
    waits_for_notice_id: int | None,
) -> ShowingNotice | None:
    """The residents' text for a new showing, in the caller's transaction.

    Held behind ``waits_for_notice_id`` (the booking prospect's confirmation
    text); with None it is due at once, in the daytime. Returns the notice, or
    None for a vacant unit or a lookup that went wrong (the showing is marked).
    """
    if found.problem is not None:
        showing.resident_attention = found.problem
        logfire.warn(
            "showing residents not texted: lookup problem",
            showing_id=showing.id,
            problem=found.problem,
        )
        return None
    if not found.mobiles:
        return None
    first, *others = found.mobiles
    return await outbox.enqueue(
        session,
        kind=residents.NEW_SHOWING_KIND,
        channel="sms",
        showing_id=showing.id,
        recipient=first,
        recipient_key=residents.RECIPIENT_KEY,
        payload=TextPayload(
            body=wording.resident_new_showing_text(_visit(showing, rules.timezone)),
            also_to=others,
            daytime_timezone=rules.timezone,
        ),
        now=now,
        waits_for_notice_id=waits_for_notice_id,
    )


def _went_out():
    """A notice that went out, may have, or is being sent now."""
    return ShowingNotice.status.in_(["sent", "captured", "unknown"]) | (
        (ShowingNotice.status == "pending") & ShowingNotice.sending_started_at.is_not(None)
    )


async def queue_cancellation(
    session: AsyncSession, showing: Showing, *, now: datetime
) -> ShowingNotice | None:
    """The residents' cancellation text, when they were told of the showing.

    Called after the showing's unsent notices were stopped. Goes to the same
    numbers as the first text, due now and sent in the daytime.
    """
    told = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing.id,
            ShowingNotice.kind == residents.NEW_SHOWING_KIND,
            _went_out(),
        )
        .limit(1)
    )
    if told is None:
        return None
    first = TextPayload.model_validate(told.payload)
    timezone = first.daytime_timezone or "UTC"
    return await outbox.enqueue(
        session,
        kind=residents.CANCELLED_KIND,
        channel="sms",
        showing_id=showing.id,
        recipient=told.recipient,
        recipient_key=residents.RECIPIENT_KEY,
        payload=TextPayload(
            body=wording.resident_cancelled_text(_visit(showing, timezone)),
            also_to=first.also_to,
            daytime_timezone=first.daytime_timezone,
        ),
        now=now,
    )


def _stopped_with_its_confirmation() -> frozenset[str]:
    """The notes of an announcement stopped because the confirmation it waited for
    failed (undelivered, refused or not sent) or its booking was withdrawn. Not a
    cancelled showing, a passed start time, or a cancelled booking."""
    from api.src.showing_bookings import bookings, holds  # both import this module

    return frozenset({holds.UNDELIVERED_NOTE, holds.NOT_SENT_NOTE, bookings.WITHDRAWN_NOTE})


async def bring_back(
    session: AsyncSession, showing_id: int, *, waits_for_notice_id: int, now: datetime
) -> bool:
    """When someone joins a showing whose announcement was stopped with a failed
    confirmation, hold it again behind the joiner's confirmation, in the caller's
    transaction. Returns whether it was brought back.

    A showing has one announcement row at most (one per kind, showing and
    recipient key), so this never makes a second; a cancelled one never went
    out. A showing whose lookup went wrong has none, and keeps its mark.
    """
    result = await session.execute(
        update(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing_id,
            ShowingNotice.kind == residents.NEW_SHOWING_KIND,
            ShowingNotice.status == "cancelled",
            ShowingNotice.last_error.in_(_stopped_with_its_confirmation()),
        )
        .values(
            status="held",
            waits_for_notice_id=waits_for_notice_id,
            last_error=None,
            not_before=now,
        ),
        execution_options={"synchronize_session": False},
    )
    brought = bool(result.rowcount)
    if brought:
        logfire.info("showing residents' text brought back by a join", showing_id=showing_id)
    return brought


async def hand_over(session: AsyncSession, holder_id: int, attendee_id: int | None) -> int:
    """Re-point the residents' text held behind ``holder_id`` to another booked
    person's confirmation text, in the caller's transaction. Returns how many moved.

    Called before the notices held behind a confirmation are stopped (it was
    undelivered, or its booking withdrawn): while someone else is booked whose
    confirmation is not known to be wrong, the showing goes ahead and the
    residents are still told once theirs is delivered. Otherwise the text stays
    held and is stopped with the rest.

    The other person's current confirmation is the latest one sent to the
    mobile on their booking now: after a corrected mobile, the replacement,
    never the earlier text to the old number; while a correction is still
    being sent (its number not yet on the booking, and it may be refused or
    superseded), the text to the number they have.
    """
    held = list(
        await session.scalars(
            select(ShowingNotice).where(
                ShowingNotice.waits_for_notice_id == holder_id,
                ShowingNotice.kind == residents.NEW_SHOWING_KIND,
                ShowingNotice.status == "held",
            )
        )
    )
    moved = 0
    for notice in held:
        other = await session.scalar(
            select(ShowingNotice.id)
            .join(ShowingAttendee, ShowingAttendee.id == ShowingNotice.attendee_id)
            .where(
                ShowingNotice.kind == host_event.CONFIRMATION_KIND,
                ShowingNotice.id != holder_id,
                ShowingNotice.recipient == ShowingAttendee.mobile,
                ShowingAttendee.showing_id == notice.showing_id,
                ShowingAttendee.status == "booked",
                ShowingAttendee.attention_reason.is_(None),
                *([ShowingAttendee.id != attendee_id] if attendee_id is not None else []),
            )
            # The first person still booked, and their latest text to that number.
            .order_by(ShowingAttendee.id, ShowingNotice.id.desc())
            .limit(1)
        )
        if other is None:
            continue
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id == notice.id)
            .values(waits_for_notice_id=other),
            execution_options={"synchronize_session": False},
        )
        moved += 1
    return moved
