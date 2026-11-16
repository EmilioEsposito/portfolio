"""The reminders the evening before a showing: the prospect's, and the residents' list.

"The evening before" is 6 PM in the showing rules' time zone on the day
before the showing (``due_at``). ``queue_due(now=)`` is one step of the job
every minute (``holds.send_pending``): it queues every reminder that is due by
``now`` and not queued yet, so it is safe every minute, and after a missed
evening it catches up while the showing is still ahead.

- A prospect's reminder (``prospect_reminder``, one per booking): to someone
  still booked, with a mobile, who accepted texts and whose number is not
  known to be wrong. A booking made at or after 6 PM the day before gets none:
  the confirmation it just received is enough. It carries the cancel link.
- The residents' reminder (``resident_reminder``, one per unit and day): one
  text listing the times of the next day's scheduled showings at the unit, in
  order, to the unit's residents found afresh that evening (``residents.find``,
  the announcement's lookup, with the same stand-in outside production). A
  vacant unit gets none. A lookup problem marks those showings for the team
  (``resident_attention``), as for an announcement. A showing booked at or
  after 6 PM the day before is not listed: its own announcement covers it.
  The notice belongs to the day's first showing and names every showing it
  lists (``TextPayload.showing_ids``); a cancelled showing never stops it, and
  at delivery it lists only the ones still ahead (``delivery_checks``).

Every reminder is a notice in the outbox behind the guard, checked again when
it is delivered (still booked, still scheduled, not started) and sent only from
8 AM to 9 PM. One notice per kind, showing and recipient key means it is never
queued twice; the residents' one is also looked for by unit and day.

The step reads the residents before its transaction, as that may reach Quo, and
then queues everything holding the shared booking lock, so no booking or
cancellation runs in between. A unit whose lookup found no one to text (vacant,
or a problem) is not looked up again for an hour in this process.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.showing_bookings import bookings, delivery_checks, outbox, residents, wording
from api.src.showing_bookings.delivery_checks import (
    PROSPECT_REMINDER_KIND,
    RESIDENT_REMINDER_KIND,
)
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.schema import TextPayload

EVENING = time(18, 0)
# Showings this far ahead are the only ones whose evening can have come.
LOOKAHEAD = timedelta(days=2)
# A unit looked up with no one to text is looked up again after this.
LOOK_AGAIN_AFTER = timedelta(hours=1)

_looked_up: dict[tuple[str, date], datetime] = {}


class ReminderReport(BaseModel):
    """What one run of ``queue_due`` queued."""

    prospect_reminders: int = 0
    resident_reminders: int = 0


def forget_lookups() -> None:
    """Forget which units were looked up with no one to text. For tests."""
    _looked_up.clear()


def local_day(moment: datetime, timezone: str) -> date:
    return moment.astimezone(ZoneInfo(timezone)).date()


def due_at(starts_at: datetime, timezone: str) -> datetime:
    """6 PM, in ``timezone``, on the day before the showing's day there."""
    day = local_day(starts_at, timezone) - timedelta(days=1)
    return datetime.combine(day, EVENING, ZoneInfo(timezone))


def recipient_key(day: date) -> str:
    """The residents' reminder for one day, as its notice's recipient key."""
    return f"residents:{day.isoformat()}"


async def _due_showings(session: AsyncSession, now: datetime, timezone: str) -> list[Showing]:
    """Scheduled showings not started whose evening before has come, by start."""
    showings = await session.scalars(
        select(Showing)
        .where(
            Showing.status == "scheduled",
            Showing.starts_at > now,
            Showing.starts_at <= now + LOOKAHEAD,
        )
        .order_by(Showing.starts_at, Showing.id)
    )
    return [showing for showing in showings if due_at(showing.starts_at, timezone) <= now]


async def _first_booked(session: AsyncSession, showing_ids: list[int]) -> dict[int, datetime]:
    """When each showing was first booked: its earliest attendee, cancelled ones too."""
    rows = await session.execute(
        select(ShowingAttendee.showing_id, func.min(ShowingAttendee.created_at))
        .where(ShowingAttendee.showing_id.in_(showing_ids))
        .group_by(ShowingAttendee.showing_id)
    )
    return dict(rows.all())


async def _resident_groups(
    session: AsyncSession, showings: list[Showing], timezone: str
) -> dict[tuple[str, date], list[Showing]]:
    """The showings a residents' reminder would list, by unit and day, for the units
    and days with no residents' reminder queued yet."""
    booked = await _first_booked(session, [showing.id for showing in showings])
    groups: dict[tuple[str, date], list[Showing]] = defaultdict(list)
    for showing in showings:
        first = booked.get(showing.id)
        if first is not None and first < due_at(showing.starts_at, timezone):
            groups[(showing.unit, local_day(showing.starts_at, timezone))].append(showing)
    if not groups:
        return {}
    queued = await session.execute(
        select(Showing.unit, ShowingNotice.recipient_key)
        .join(Showing, Showing.id == ShowingNotice.showing_id)
        .where(
            ShowingNotice.kind == RESIDENT_REMINDER_KIND,
            ShowingNotice.recipient_key.in_({recipient_key(day) for _, day in groups}),
        )
    )
    done = {(unit.casefold(), key) for unit, key in queued.all()}
    return {
        (unit, day): group
        for (unit, day), group in groups.items()
        if (unit.casefold(), recipient_key(day)) not in done
    }


async def _prospects(
    session: AsyncSession, showings: list[Showing], timezone: str
) -> list[ShowingAttendee]:
    """The booked people in ``showings`` owed a reminder and not yet sent one, locked."""
    by_id = {showing.id: showing for showing in showings}
    reminded = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == PROSPECT_REMINDER_KIND,
        ShowingNotice.showing_id.in_(by_id),
        ShowingNotice.attendee_id.is_not(None),
    )
    people = await session.scalars(
        select(ShowingAttendee)
        .where(
            ShowingAttendee.showing_id.in_(by_id),
            ShowingAttendee.status == "booked",
            ShowingAttendee.id.not_in(reminded),
        )
        .order_by(ShowingAttendee.id)
        .with_for_update()
    )
    return [
        person
        for person in people
        if delivery_checks.can_text(person)
        and person.created_at < due_at(by_id[person.showing_id].starts_at, timezone)
    ]


def _skip_lookup(unit: str, day: date, now: datetime) -> bool:
    looked = _looked_up.get((unit.casefold(), day))
    return looked is not None and now - looked < LOOK_AGAIN_AFTER


async def queue_due(*, now: datetime) -> ReminderReport:
    """Queue every reminder due by ``now`` that is not queued yet. Safe to run every minute."""
    report = ReminderReport()
    async with AsyncSessionFactory() as session:
        rules = await listings_service.read_showing_rules(session)
        due = await _due_showings(session, now, rules.timezone)
        if not due:
            return report
        groups = await _resident_groups(session, due, rules.timezone)
    # Read before the transaction, as it may reach Quo.
    found = {
        (unit, day): await residents.find(unit, day)
        for unit, day in groups
        if not _skip_lookup(unit, day, now)
    }

    async with AsyncSessionFactory() as session, session.begin():
        # The module's lock order: the shared booking lock, then attendees,
        # then their notices, then showings.
        await bookings.lock(session, bookings.BOOKING_LOCK)
        due = await _due_showings(session, now, rules.timezone)
        by_id = {showing.id: showing for showing in due}
        for person in await _prospects(session, due, rules.timezone):
            showing = by_id[person.showing_id]
            await outbox.enqueue(
                session,
                kind=PROSPECT_REMINDER_KIND,
                channel="sms",
                showing_id=showing.id,
                attendee_id=person.id,
                recipient=person.mobile,
                recipient_key=f"attendee:{person.id}",
                payload=TextPayload(
                    body=wording.prospect_reminder_text(
                        bookings.visit_of(showing, rules), bookings.cancel_url(person.cancel_token)
                    ),
                    daytime_timezone=rules.timezone,
                ),
                now=now,
            )
            report.prospect_reminders += 1
        groups = await _resident_groups(session, due, rules.timezone)
        to_mark: dict[int, str] = {}
        for (unit, day), group in groups.items():
            lookup = found.get((unit, day))
            if lookup is None:
                continue
            if lookup.problem is not None or not lookup.mobiles:
                _looked_up[(unit.casefold(), day)] = now
                for showing in group:
                    if lookup.problem is not None:
                        to_mark[showing.id] = lookup.problem
                continue
            first, *others = lookup.mobiles
            await outbox.enqueue(
                session,
                kind=RESIDENT_REMINDER_KIND,
                channel="sms",
                showing_id=group[0].id,
                recipient=first,
                recipient_key=recipient_key(day),
                payload=TextPayload(
                    body=wording.resident_reminder_text(
                        [bookings.visit_of(showing, rules) for showing in group]
                    ),
                    also_to=others,
                    daytime_timezone=rules.timezone,
                    showing_ids=[showing.id for showing in group],
                ),
                now=now,
            )
            report.resident_reminders += 1
        if to_mark:
            marked = await session.scalars(
                select(Showing)
                .where(Showing.id.in_(to_mark))
                .order_by(Showing.id)
                .with_for_update()
            )
            for showing in marked:
                showing.resident_attention = to_mark[showing.id]
            logfire.warn(
                "showing residents not reminded: lookup problem",
                showing_ids=sorted(to_mark),
                problems=sorted(set(to_mark.values())),
            )
    if report.prospect_reminders or report.resident_reminders:
        logfire.info("showing reminders queued", **report.model_dump())
    return report
