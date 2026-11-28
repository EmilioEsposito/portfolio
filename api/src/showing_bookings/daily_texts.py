"""The host's morning text and the prospect's texts 1 hour before and after a showing,
queued by the job every minute.

``queue_due(now=)`` is one step of ``holds.send_pending``. Each part queues
only what is due by ``now`` and not queued yet, so it is safe every minute and
catches up after a missed run.

- The host's morning text (``host_morning``): from 8 AM in the showing rules'
  time zone, one text to each agent hosting at least one scheduled showing that
  day that has not started, at the agent's own mobile, listing the day's
  showings in order (time, place, how many people). Not for an agent already
  texted about a change since 8 AM that day: that text listed their upcoming
  showings, today's included. Changes due at 8 AM go in it (``digests.py``). One per agent per day
  (recipient key ``agent:<id>:morning:<day>``). The guard judges a notice by
  one unit, so the text lists only the showings it would really send for to
  the agent's number (``delivery_checks.morning_selection``) and is attached to
  the first of them; with none, it lists them all and is captured. A
  morning missed past noon is not sent; an agent with no mobile gets none.
  It is written again when it is delivered (``delivery_checks``), so it lists
  the day as it is then. It names no prospect and no phone number.
- The apply text (``prospect_apply``): two hours after a showing's start, one
  text with the apply link (``<public base URL>/apply``) to each person still
  booked in a showing that was not cancelled, with a mobile and consent to
  texts, whose number is not known to be wrong and who is not marked a
  no-show. One per person, never repeated. It goes from 8 AM to 9 PM, waiting
  for 8 AM otherwise, and is checked again when delivered, so someone marked
  a no-show after it was queued gets nothing. Showings older than
  ``APPLY_LOOKBACK`` are left alone, so a job that was off for days does not
  text people about a showing long past.

- The text 1 hour before (``prospect_hour_reminder``): one text to each person
  still booked in a scheduled showing whom we may text (a mobile, consent, a
  number not known to be wrong, a settled reply), from 1 hour before the start,
  unless they booked less than 75 minutes before it (``HOUR_BOOKED_BY``): the
  confirmation just told them. It goes at any hour, and is not sent once less
  than 45 minutes are left (``HOUR_LAST``), after the start, or after a
  cancellation, which stops it with the booking's other notices.

Every text is a notice in the outbox behind the guard. Every part queues holding
the shared booking lock, in the module's lock order.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import bookings, delivery_checks, outbox, wording
from api.src.showing_bookings.delivery_checks import (
    APPLY_KIND,
    HOST_MORNING_KIND,
    HOUR_BEFORE,
    HOUR_BOOKED_BY,
    HOUR_KIND,
    HOUR_LAST,
    MORNING,
    MORNING_ENDS,
)
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import TextPayload

APPLY_AFTER = delivery_checks.APPLY_AFTER
APPLY_LOOKBACK = delivery_checks.APPLY_LOOKBACK


class DailyTextReport(BaseModel):
    """What one run of ``queue_due`` queued."""

    host_mornings: int = 0
    apply_texts: int = 0
    hour_texts: int = 0


def apply_url() -> str:
    """Where the apply text points."""
    return f"{bookings.public_base_url()}/apply"


def _morning_open(now: datetime, timezone: str) -> date | None:
    """Today in ``timezone`` while it is 8 AM to noon there, else None."""
    local = now.astimezone(ZoneInfo(timezone))
    return local.date() if MORNING <= local.time() < MORNING_ENDS else None


async def _queue_mornings(session: AsyncSession, now: datetime, timezone: str) -> int:
    day = _morning_open(now, timezone)
    if day is None:
        return 0
    zone = ZoneInfo(timezone)
    day_ends = datetime.combine(day + timedelta(days=1), time(0, 0), zone)
    hosts = list(
        await session.scalars(
            select(Showing.agent_id)
            .where(
                Showing.status == "scheduled",
                Showing.starts_at > now,
                Showing.starts_at < day_ends,
            )
            .distinct()
        )
    )
    if not hosts:
        return 0
    keys = {delivery_checks.morning_key(agent_id, day): agent_id for agent_id in hosts}
    queued = set(
        await session.scalars(
            select(ShowingNotice.recipient_key).where(
                ShowingNotice.kind == HOST_MORNING_KIND,
                ShowingNotice.recipient_key.in_(keys),
            )
        )
    )
    mobiles = dict(
        (
            await session.execute(
                select(ShowingAgentRecord.id, ShowingAgentRecord.mobile).where(
                    ShowingAgentRecord.id.in_(hosts)
                )
            )
        ).all()
    )
    # A host texted about a change since 8 AM has had their upcoming showings
    # listed already, today's included, so no morning text follows it.
    opened = datetime.combine(day, MORNING, zone)
    told_today = {
        delivery_checks.named_agent(recipient_key)
        for recipient_key in await session.scalars(
            select(ShowingNotice.recipient_key).where(
                ShowingNotice.kind.in_(delivery_checks.HOST_TEXT_KINDS),
                ShowingNotice.status.in_(["sent", "captured", "unknown"]),
                ShowingNotice.delivered_at >= opened,
            )
        )
    }
    count = 0
    for key, agent_id in sorted(keys.items(), key=lambda item: item[1]):
        mobile = mobiles.get(agent_id)
        if key in queued or not mobile or agent_id in told_today:
            continue
        showings = await delivery_checks.day_showings(session, agent_id, day, timezone, now)
        if not showings:
            continue
        showings = await delivery_checks.morning_selection(session, showings, mobile)
        await outbox.enqueue(
            session,
            kind=HOST_MORNING_KIND,
            channel="sms",
            showing_id=showings[0][0].id,
            recipient=mobile,
            recipient_key=key,
            payload=TextPayload(
                body=delivery_checks.morning_body(showings, timezone),
                daytime_timezone=timezone,
                showing_ids=[showing.id for showing, _ in showings],
            ),
            now=now,
        )
        count += 1
    return count


async def _queue_apply_texts(session: AsyncSession, now: datetime, rules: ShowingRules) -> int:
    sent = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == APPLY_KIND, ShowingNotice.attendee_id.is_not(None)
    )
    people = list(
        await session.scalars(
            select(ShowingAttendee)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(
                Showing.status != "cancelled",
                Showing.starts_at <= now - APPLY_AFTER,
                Showing.starts_at > now - APPLY_LOOKBACK,
                ShowingAttendee.status == "booked",
                ShowingAttendee.attendance != "no_show",
                ShowingAttendee.id.not_in(sent),
            )
            .order_by(ShowingAttendee.id)
            .with_for_update(of=ShowingAttendee)
        )
    )
    people = [person for person in people if delivery_checks.can_text(person)]
    if not people:
        return 0
    showings = {
        showing.id: showing
        for showing in await session.scalars(
            select(Showing).where(Showing.id.in_({person.showing_id for person in people}))
        )
    }
    link = apply_url()
    for person in people:
        await outbox.enqueue(
            session,
            kind=APPLY_KIND,
            channel="sms",
            showing_id=person.showing_id,
            attendee_id=person.id,
            recipient=person.mobile,
            recipient_key=f"attendee:{person.id}",
            payload=TextPayload(
                body=wording.apply_text(
                    bookings.visit_of(showings[person.showing_id], rules), link
                ),
                daytime_timezone=rules.timezone,
            ),
            now=now,
        )
    return len(people)


async def _queue_hour_texts(session: AsyncSession, now: datetime, rules: ShowingRules) -> int:
    queued = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == HOUR_KIND, ShowingNotice.attendee_id.is_not(None)
    )
    rows = (
        await session.execute(
            select(ShowingAttendee, Showing)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(
                Showing.status == "scheduled",
                Showing.starts_at <= now + HOUR_BEFORE,
                Showing.starts_at >= now + HOUR_LAST,
                ShowingAttendee.status == "booked",
                ShowingAttendee.id.not_in(queued),
            )
            .order_by(ShowingAttendee.id)
            .with_for_update(of=ShowingAttendee)
        )
    ).all()
    people = [
        (person, showing)
        for person, showing in rows
        if delivery_checks.can_text(person)
        and person.created_at <= showing.starts_at - HOUR_BOOKED_BY
    ]
    for person, showing in people:
        await outbox.enqueue(
            session,
            kind=HOUR_KIND,
            channel="sms",
            showing_id=showing.id,
            attendee_id=person.id,
            recipient=person.mobile,
            recipient_key=f"attendee:{person.id}",
            payload=TextPayload(
                body=wording.hour_reminder_text(bookings.visit_of(showing, rules), person.name)
            ),
            now=now,
        )
    return len(people)


async def queue_due(*, now: datetime) -> DailyTextReport:
    """Queue the morning texts and apply texts due by ``now`` and not queued yet."""
    report = DailyTextReport()
    async with AsyncSessionFactory() as session, session.begin():
        rules = await listings_service.read_showing_rules(session)
        # The module's lock order: the shared booking lock, then attendees,
        # then their notices, then showings.
        await bookings.lock(session, bookings.BOOKING_LOCK)
        report.apply_texts = await _queue_apply_texts(session, now, rules)
        report.hour_texts = await _queue_hour_texts(session, now, rules)
        report.host_mornings = await _queue_mornings(session, now, rules.timezone)
    if report.host_mornings or report.apply_texts or report.hour_texts:
        logfire.info("showing daily texts queued", **report.model_dump())
    return report
