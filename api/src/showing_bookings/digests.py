"""Texts that go out together: one text to a host, or to a unit's residents, out of
every notice due for them at the same moment.

A host's texts (``HOST_KINDS``: each change, and the 8 AM morning text) and a
unit's residents' texts (``resident_texts.KINDS``: each announcement, each
cancellation, and the 8 AM reminder) are queued one per thing that happened.
When one of them is about to be delivered (captured, or claimed for sending, by
the outbox), ``compose`` takes every other notice of the same group that is
due now and not being sent, checks each as its own delivery would, and writes
one text for all of them on the notice being delivered:

- a host's: what changed, in the order it happened, then their upcoming
  showings in order; with the morning text among them, the changes and then
  the day's showings (``wording.host_text``, ``wording.host_morning_text``).
- a unit's residents': the showings cancelled, the day's reminder, and the
  new showings among the unit's upcoming ones they have been told of; a
  showing whose own announcement is not out yet is left out until it goes
  (``wording.resident_text``).

A text alone is written the same way, so every host's and residents' text says
the same things whether or not others went with it.

The others are saved ``cancelled`` with "Not sent by itself: it went in the same
text as notice N." The text names every showing it tells of in
``showing_ids`` and the notices it took in ``merged_ids``, so ``told.py`` and
``resident_texts.told_ids`` count those showings told once it went. Only
notices the guard would treat alike go together (``same``), and only ones
sent by hand with one sent by hand. ``lock`` False (a preview) reads the others
without changing them. Nothing here sends.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import delivery_checks, resident_texts, residents, told, wording
from api.src.showing_bookings.delivery_checks import (
    HOST_MORNING_KIND,
    HOST_MOVED_KINDS,
    HOST_TEXT_KINDS,
    RESIDENT_REMINDER_KIND,
)
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.schema import TextPayload

HOST_KINDS = HOST_TEXT_KINDS | {HOST_MORNING_KIND}
KINDS = HOST_KINDS | resident_texts.KINDS
NOTHING_TO_SAY = "Not sent: nothing it said is still true."

# Whether the guard would decide ``notice``, judged by ``unit``, as it did the
# one being delivered.
Same = Callable[[ShowingNotice, str], bool]


def merged_note(notice_id: int) -> str:
    return f"Not sent by itself: it went in the same text as notice {notice_id}."


def _copy(notice: ShowingNotice) -> ShowingNotice:
    return ShowingNotice(
        **{column.key: getattr(notice, column.key) for column in inspect(ShowingNotice).columns}
    )


def _visit(showing: Showing, timezone: str) -> wording.Visit:
    return wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=timezone,
    )


async def compose(
    session: AsyncSession,
    notice: ShowingNotice,
    *,
    now: datetime,
    sending: bool,
    lock: bool,
    same: Same,
) -> str | None:
    """Write ``notice``'s text with every other one due for the same people now.

    ``sending``: the guard will really send it, so a change whose message
    follows one that was only captured is left out (``told.missing``). Returns
    a note to cancel ``notice`` with when nothing it would say is still true, else
    None.
    """
    if notice.kind in HOST_KINDS:
        return await _host(session, notice, now=now, sending=sending, lock=lock, same=same)
    if notice.kind in resident_texts.KINDS:
        return await _residents(session, notice, now=now, sending=sending, lock=lock, same=same)
    return None


async def _others(
    session: AsyncSession,
    notice: ShowingNotice,
    kinds: frozenset[str],
    now: datetime,
    lock: bool,
) -> list[tuple[ShowingNotice, str]]:
    """The other notices of ``kinds`` due now and not being sent, with their units,
    sent by hand only with one sent by hand. Locked, skipping any another run holds."""
    by_hand = ShowingNotice.send_approved_at.is_not(None)
    query = (
        select(ShowingNotice, Showing.unit)
        .join(Showing, Showing.id == ShowingNotice.showing_id)
        .where(
            ShowingNotice.id != notice.id,
            ShowingNotice.kind.in_(kinds),
            ShowingNotice.status == "pending",
            ShowingNotice.not_before <= now,
            ShowingNotice.sending_started_at.is_(None),
            by_hand if notice.send_approved_at is not None else ~by_hand,
        )
        .order_by(ShowingNotice.id)
    )
    if lock:
        query = query.with_for_update(of=ShowingNotice, skip_locked=True)
    return [(row, unit) for row, unit in (await session.execute(query)).all()]


async def _take(
    session: AsyncSession,
    others: list[tuple[ShowingNotice, str]],
    belongs: Callable[[ShowingNotice], Awaitable[bool]],
    check: Callable[[ShowingNotice], Awaitable[delivery_checks.Verdict]],
    *,
    sending: bool,
    lock: bool,
    same: Same,
) -> list[ShowingNotice]:
    """The others that belong with the notice, are true now and go now. One that a
    check stops is cancelled with its note (when ``lock``)."""
    taken = []
    for row, unit in others:
        if not await belongs(row) or not same(row, unit):
            continue
        work = row if lock else _copy(row)
        verdict = await check(work)
        note = verdict.cancel
        if note is None and (verdict.later is not None or verdict.fail is not None):
            continue  # not now: it goes by itself later
        if note is None and sending:
            note = await told.missing(session, work, None)
        if note is not None:
            if lock:
                row.status, row.last_error = "cancelled", note
            continue
        taken.append(work)
    return taken


def _absorb(notice: ShowingNotice, taken: list[ShowingNotice], lock: bool) -> list[int]:
    if lock:
        for row in taken:
            row.status, row.last_error = "cancelled", merged_note(notice.id)
    return [row.id for row in taken]


def _save(notice: ShowingNotice, body: str, showing_ids: list[int], merged: list[int]) -> None:
    payload = TextPayload.model_validate(notice.payload)
    notice.payload = payload.model_copy(
        update={
            "body": body,
            "showing_ids": sorted(set(showing_ids)),
            "merged_ids": merged,
        }
    ).model_dump(mode="json", exclude_defaults=True)


# ---------------------------------------------------------------------------
# A host's texts
# ---------------------------------------------------------------------------


async def host_agent(session: AsyncSession, notice: ShowingNotice) -> int:
    """The agent a host's text is for: the one its key names for the morning text
    and a change of host, else the showing's host now."""
    if notice.kind == HOST_MORNING_KIND or notice.kind in HOST_MOVED_KINDS:
        return delivery_checks.named_agent(notice.recipient_key)
    return await session.scalar(select(Showing.agent_id).where(Showing.id == notice.showing_id))


async def upcoming_for(
    session: AsyncSession, agent_id: int, now: datetime, timezone: str
) -> list[tuple[Showing, wording.DayShowing]]:
    """The agent's scheduled showings not started by ``now``, by start, with how
    many people are booked."""
    booked = (
        select(func.count())
        .where(ShowingAttendee.showing_id == Showing.id, ShowingAttendee.status == "booked")
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Showing, booked)
        .where(
            Showing.agent_id == agent_id,
            Showing.status == "scheduled",
            Showing.starts_at > now,
        )
        .order_by(Showing.starts_at, Showing.id)
    )
    return [
        (showing, wording.DayShowing(visit=_visit(showing, timezone), booked=count))
        for showing, count in rows.all()
    ]


async def _host(
    session: AsyncSession,
    notice: ShowingNotice,
    *,
    now: datetime,
    sending: bool,
    lock: bool,
    same: Same,
) -> str | None:
    agent_id = await host_agent(session, notice)

    async def belongs(row: ShowingNotice) -> bool:
        return await host_agent(session, row) == agent_id

    others = await _others(session, notice, HOST_KINDS, now, lock)
    taken = await _take(
        session,
        others,
        belongs,
        lambda row: delivery_checks.check(session, row, now),
        sending=sending,
        lock=lock,
        same=same,
    )
    group = sorted([notice, *taken], key=lambda row: row.id or 0)
    lines = [
        TextPayload.model_validate(row.payload).line or TextPayload.model_validate(row.payload).body
        for row in group
        if row.kind != HOST_MORNING_KIND
    ]
    named = [row.showing_id for row in group if row.kind != HOST_MORNING_KIND]
    timezone = next(
        (
            TextPayload.model_validate(row.payload).daytime_timezone
            for row in group
            if TextPayload.model_validate(row.payload).daytime_timezone
        ),
        "UTC",
    )
    morning = next((row for row in group if row.kind == HOST_MORNING_KIND), None)
    if morning is not None:
        # Its check listed the day's showings (``delivery_checks._host_morning``).
        listed = TextPayload.model_validate(morning.payload).showing_ids
        upcoming = [
            item
            for showing, item in await upcoming_for(session, agent_id, now, timezone)
            if showing.id in listed
        ]
        body = wording.host_morning_text(upcoming, lines=lines) if upcoming else None
    else:
        listed_pairs = await upcoming_for(session, agent_id, now, timezone)
        listed = [showing.id for showing, _ in listed_pairs]
        body = wording.host_text(lines, [item for _, item in listed_pairs]) if lines else None
    if body is None:
        return delivery_checks.NOTHING_TODAY if morning is not None else NOTHING_TO_SAY
    _save(notice, body, [*named, *listed], _absorb(notice, taken, lock))
    return None


# ---------------------------------------------------------------------------
# A unit's residents' texts
# ---------------------------------------------------------------------------


def _numbers(notice: ShowingNotice) -> tuple[str, ...]:
    payload = TextPayload.model_validate(notice.payload)
    return tuple(sorted([notice.recipient, *payload.also_to]))


async def _residents(
    session: AsyncSession,
    notice: ShowingNotice,
    *,
    now: datetime,
    sending: bool,
    lock: bool,
    same: Same,
) -> str | None:
    unit = await session.scalar(select(Showing.unit).where(Showing.id == notice.showing_id))
    numbers = _numbers(notice)

    async def belongs(row: ShowingNotice) -> bool:
        row_unit = await session.scalar(select(Showing.unit).where(Showing.id == row.showing_id))
        return row_unit == unit and _numbers(row) == numbers

    others = await _others(session, notice, resident_texts.KINDS, now, lock)
    taken = await _take(
        session,
        others,
        belongs,
        lambda row: resident_texts.check(session, row, now),
        sending=sending,
        lock=lock,
        same=same,
    )
    group = sorted([notice, *taken], key=lambda row: row.id or 0)
    timezone = next(
        (
            TextPayload.model_validate(row.payload).daytime_timezone
            for row in group
            if TextPayload.model_validate(row.payload).daytime_timezone
        ),
        "UTC",
    )
    new_ids = {row.showing_id for row in group if row.kind == residents.NEW_SHOWING_KIND}
    cancelled_ids = {row.showing_id for row in group if row.kind == residents.CANCELLED_KIND}
    reminder_ids = {
        showing_id
        for row in group
        if row.kind == RESIDENT_REMINDER_KIND
        for showing_id in TextPayload.model_validate(row.payload).showing_ids
    }
    ahead = list(
        await session.scalars(
            select(Showing)
            .where(Showing.unit == unit, Showing.status == "scheduled", Showing.starts_at > now)
            .order_by(Showing.starts_at, Showing.id)
        )
    )
    told_already = await resident_texts.told_ids(
        session, [showing.id for showing in ahead if showing.id not in new_ids]
    )
    listed = [
        showing
        for showing in ahead
        if (showing.id in new_ids or showing.id in told_already) and showing.id not in cancelled_ids
    ]
    cancelled = [
        _visit(showing, timezone)
        for showing in await session.scalars(
            select(Showing).where(Showing.id.in_(cancelled_ids)).order_by(Showing.starts_at)
        )
    ]
    today = []
    if reminder_ids:
        day = min(
            (showing.starts_at for showing in ahead if showing.id in reminder_ids),
            default=None,
        )
        if day is not None:
            local_day = day.astimezone(ZoneInfo(timezone)).date()
            # A showing announced in this very text needs no reminder besides.
            today = [
                _visit(showing, timezone)
                for showing in listed
                if showing.starts_at.astimezone(ZoneInfo(timezone)).date() == local_day
                and showing.id not in new_ids
            ]
    if not cancelled and not today and not new_ids & {showing.id for showing in listed}:
        return delivery_checks.NOTHING_LEFT
    body = wording.resident_text(
        upcoming=[
            wording.Upcoming(visit=_visit(showing, timezone), new=showing.id in new_ids)
            for showing in listed
        ],
        cancelled=cancelled,
        today=today,
    )
    _save(
        notice,
        body,
        [showing.id for showing in listed],
        _absorb(notice, taken, lock),
    )
    return None
