"""The reminders on the day of a showing, at 8 AM: the prospect's, a lead's by email,
and the residents' list.

"On the day" is 8 AM in the showing rules' time zone on the showing's day
(``due_at``). ``queue_due(now=)`` is one step of the job every minute
(``holds.send_pending``): it queues every reminder that is due by ``now`` and
not queued yet, so it is safe every minute, and after a missed morning it
catches up while the showing is still ahead.

- A prospect's reminder (``prospect_reminder``, one per booking): to someone
  still booked, with a mobile, who accepted texts and whose number is not
  known to be wrong. A booking made at or after 8 AM on the day gets none: the
  confirmation it just received is enough. Nor does someone whose text 1 hour
  before (``daily_texts``) would come within 90 minutes of it
  (``delivery_checks.REMINDER_GAP``): the 1-hour text wins. It carries the
  cancel link.
- The residents' reminder (``resident_reminder``, one per unit and day): one
  text naming the times of the day's scheduled showings at the unit, to the
  unit's residents found afresh that morning (``residents.find``, the
  announcement's lookup, with the same stand-in outside production). A vacant
  unit gets none. A lookup problem marks those showings for the team
  (``resident_attention``), as for an announcement. A showing first booked at
  or after 8 AM on the day is not listed: its own announcement covers it. The
  notice belongs to the day's first showing and names every showing it lists
  (``TextPayload.showing_ids``); a cancelled showing never stops it. It is
  written when it goes (``digests.py``): the day's showings still ahead that
  the residents were told of, with any announcement due at 8 AM in the same text.

- A lead's reminder by email (``lead_email_reminder``, one per booking): for a
  place the Zillow lead agent booked in an email thread whose confirming reply
  was sent (``lead_reply_sent_at``, before 8 AM on the day), for a lead with no
  mobile we text, and only while ``lead_agent_uses_open_times`` is on. It goes
  as a reply in the lead's thread, to the route stored on the conversation
  that booked the place (``lead_email.py``), with the cancel link and the
  lead's own link to add their details. Someone owed the reminder text never
  gets it, and someone already sent it (or with it on its way) is not queued
  the reminder text: one reminder per place.

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
from api.src.rental_listings.schema import ShowingRules
from api.src.showing_bookings import (
    bookings,
    delivery_checks,
    lead_email,
    lead_tours,
    outbox,
    residents,
    wording,
)
from api.src.showing_bookings.delivery_checks import (
    LEAD_EMAIL_REMINDER_KIND,
    PROSPECT_REMINDER_KIND,
    RESIDENT_REMINDER_KIND,
)
from api.src.showing_bookings.lead_email import RouteProblem
from api.src.showing_bookings.models import Showing, ShowingAttendee, ShowingNotice
from api.src.showing_bookings.schema import EmailPayload, TextPayload
from api.src.showing_bookings.settings import settings_for_delivery
from api.src.zillow_leads import lead_links

# A reminder by email that still stands: sent, on its way, or kept in capture
# mode. Someone with one is not queued the reminder text as well.
_EMAIL_STANDS = ("pending", "held", "sent", "captured", "unknown", "skipped")

DAY_OF = time(8, 0)
# Showings this far ahead are the only ones whose morning can have come.
LOOKAHEAD = timedelta(days=2)
# A unit looked up with no one to text is looked up again after this.
LOOK_AGAIN_AFTER = timedelta(hours=1)

_looked_up: dict[tuple[str, date], datetime] = {}


class ReminderReport(BaseModel):
    """What one run of ``queue_due`` queued."""

    prospect_reminders: int = 0
    resident_reminders: int = 0
    lead_email_reminders: int = 0


def forget_lookups() -> None:
    """Forget which units were looked up with no one to text. For tests."""
    _looked_up.clear()


def local_day(moment: datetime, timezone: str) -> date:
    return moment.astimezone(ZoneInfo(timezone)).date()


def due_at(starts_at: datetime, timezone: str) -> datetime:
    """8 AM, in ``timezone``, on the showing's day there."""
    return datetime.combine(local_day(starts_at, timezone), DAY_OF, ZoneInfo(timezone))


def recipient_key(day: date) -> str:
    """The residents' reminder for one day, as its notice's recipient key."""
    return f"residents:{day.isoformat()}"


async def _due_showings(session: AsyncSession, now: datetime, timezone: str) -> list[Showing]:
    """Scheduled showings not started whose morning has come, by start."""
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
    # A lead reminded by email (or about to be) is not texted the reminder too.
    emailed = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == LEAD_EMAIL_REMINDER_KIND,
        ShowingNotice.showing_id.in_(by_id),
        ShowingNotice.attendee_id.is_not(None),
        ShowingNotice.status.in_(_EMAIL_STANDS),
    )
    people = await session.scalars(
        select(ShowingAttendee)
        .where(
            ShowingAttendee.showing_id.in_(by_id),
            ShowingAttendee.status == "booked",
            ShowingAttendee.id.not_in(reminded),
            ShowingAttendee.id.not_in(emailed),
        )
        .order_by(ShowingAttendee.id)
        .with_for_update()
    )
    return [
        person
        for person in people
        if delivery_checks.can_text(person)
        and person.created_at < due_at(by_id[person.showing_id].starts_at, timezone)
        and not delivery_checks.hour_text_wins(
            person,
            by_id[person.showing_id].starts_at,
            due_at(by_id[person.showing_id].starts_at, timezone),
        )
    ]


async def _email_leads(
    session: AsyncSession, showings: list[Showing], timezone: str, *, lock: bool
) -> list[ShowingAttendee]:
    """The places in ``showings`` owed a reminder by email and not yet queued one:
    booked by the lead agent in a Zillow thread, the confirming reply sent before
    8 AM on the day, and no mobile we text (``delivery_checks.owes_lead_email``)."""
    by_id = {showing.id: showing for showing in showings}
    queued = select(ShowingNotice.attendee_id).where(
        ShowingNotice.kind == LEAD_EMAIL_REMINDER_KIND,
        ShowingNotice.showing_id.in_(by_id),
        ShowingNotice.attendee_id.is_not(None),
    )
    query = (
        select(ShowingAttendee)
        .where(
            ShowingAttendee.showing_id.in_(by_id),
            ShowingAttendee.status == "booked",
            ShowingAttendee.source == "lead_agent",
            ShowingAttendee.lead_reply_sent_at.is_not(None),
            ShowingAttendee.id.not_in(queued),
        )
        .order_by(ShowingAttendee.id)
    )
    people = await session.scalars(query.with_for_update() if lock else query)
    return [
        person
        for person in people
        if delivery_checks.owes_lead_email(person)
        and person.lead_reply_sent_at < due_at(by_id[person.showing_id].starts_at, timezone)
    ]


async def _queue_lead_email(
    session: AsyncSession,
    person: ShowingAttendee,
    showing: Showing,
    rules: ShowingRules,
    token: str,
    now: datetime,
) -> None:
    """Queue one lead's reminder by email. The recipient and subject are the stored
    route of the conversation that booked the place, read again at delivery; when
    there is none now, a placeholder the notice is never sent to, so the check at
    delivery fails it for the team."""
    route = await lead_email.route_of(session, person.lead_conversation_id, person.zillow_thread_id)
    found = not isinstance(route, RouteProblem)
    await outbox.enqueue(
        session,
        kind=LEAD_EMAIL_REMINDER_KIND,
        channel="email",
        showing_id=showing.id,
        attendee_id=person.id,
        recipient=route.to if found else lead_email.NO_ROUTE_RECIPIENT,
        recipient_key=f"attendee:{person.id}",
        payload=EmailPayload(
            subject=route.subject if found else lead_email.NO_ROUTE_SUBJECT,
            body=wording.lead_email_reminder_text(
                bookings.visit_of(showing, rules),
                bookings.cancel_url(person.cancel_token),
                lead_tours.lead_link(showing.unit, token),
            ),
            conversation_id=person.lead_conversation_id,
            reply_to_message_id=route.reply_to_message_id if found else None,
            daytime_timezone=rules.timezone,
        ),
        now=now,
    )


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
        emails_on = (await settings_for_delivery(session)).lead_agent_uses_open_times
        email_threads = (
            sorted(
                {
                    person.zillow_thread_id
                    for person in await _email_leads(session, due, rules.timezone, lock=False)
                    if person.zillow_thread_id
                }
            )
            if emails_on
            else []
        )
    # Read before the transaction, as it may reach Quo.
    found = {
        (unit, day): await residents.find(unit, day)
        for unit, day in groups
        if not _skip_lookup(unit, day, now)
    }
    # Each lead's own link carries their thread's token, made in its own
    # transaction (it is normally made already, when the lead agent sent the link).
    tokens = {
        thread: await lead_links.token_for_thread(thread, now=now) for thread in email_threads
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
        if emails_on:
            for person in await _email_leads(session, due, rules.timezone, lock=True):
                token = tokens.get(person.zillow_thread_id or "")
                if token is None:
                    continue  # booked since the read above: the next run queues it
                await _queue_lead_email(
                    session, person, by_id[person.showing_id], rules, token, now
                )
                report.lead_email_reminders += 1
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
                    # Written again when it goes, with what the residents were told.
                    body=wording.resident_text(
                        upcoming=[],
                        today=[bookings.visit_of(showing, rules) for showing in group],
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
    if any(report.model_dump().values()):
        logfire.info("showing reminders queued", **report.model_dump())
    return report
