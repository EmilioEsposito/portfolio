"""The checks a reminder or a host's text gets each time it is about to be delivered.

``outbox._prepare`` asks ``check`` about every notice of these kinds, first
try or retry, under the lock on the notice:

- A prospect's reminder goes only while the person is still booked, the
  showing still scheduled and not started, and the number still one we text
  (a mobile, texts accepted, and no sign it is wrong). It goes to the mobile
  on the booking now, so a corrected number gets it.
- The residents' reminder lists only the showings it was queued for that are
  still scheduled and not started; with none left it is not sent.
- Both reminders go out only from 8 AM to 9 PM in the showing rules' time zone
  (``TextPayload.daytime_timezone``), waiting for 8 AM otherwise.
- A host's text goes to the current host's own mobile while the showing has
  not started; a host with no mobile gets none. "Someone booked" goes only
  while that person is still booked in a scheduled showing. After a change of
  host, each of the two texts goes to the host it names while the showing is
  scheduled: the new host's only while they host it, the old host's only while
  they do not (a quick move back makes it false).
- The host's morning text (``daily_texts.py``) is written afresh: the host's
  scheduled showings that day not started yet, with the people booked now, to
  the host's mobile now. With none left, a host without a mobile, or from
  noon that day (a missed morning), it is not sent.
- The apply text goes only while the person is still booked in a showing that
  was not cancelled, is not marked a no-show, and is still a number we text,
  to the mobile on the booking now, and only from 8 AM to 9 PM.

Nothing here imports the outbox or sends anything; the outbox applies the
answer (``Verdict``).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import residents, wording
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import TextPayload

PROSPECT_REMINDER_KIND = "prospect_reminder"
RESIDENT_REMINDER_KIND = "resident_reminder"
REMINDER_KINDS = frozenset({PROSPECT_REMINDER_KIND, RESIDENT_REMINDER_KIND})
HOST_JOIN_KIND = "host_late_join"
HOST_LEFT_KIND = "host_late_cancel"
HOST_SHOWING_CANCELLED_KIND = "host_showing_cancelled"
# The team gave the showing to another host: to the new host, and to the old one.
HOST_MOVED_TO_KIND = "host_moved_to"
HOST_MOVED_FROM_KIND = "host_moved_from"
HOST_MOVED_KINDS = frozenset({HOST_MOVED_TO_KIND, HOST_MOVED_FROM_KIND})
HOST_TEXT_KINDS = (
    frozenset({HOST_JOIN_KIND, HOST_LEFT_KIND, HOST_SHOWING_CANCELLED_KIND}) | HOST_MOVED_KINDS
)
# The host's text at 8 AM with the day's showings, and the prospect's text with
# the apply link two hours after a showing (``daily_texts.py``).
HOST_MORNING_KIND = "host_morning"
APPLY_KIND = "prospect_apply"
DAILY_TEXT_KINDS = frozenset({HOST_MORNING_KIND, APPLY_KIND})
KINDS = REMINDER_KINDS | HOST_TEXT_KINDS | DAILY_TEXT_KINDS

# A booking whose confirmation text could not reach the number: it is texted
# nothing more (``cancelling.will_text``).
WRONG_NUMBER = frozenset({"confirmation_undelivered", "confirmation_refused"})

BOOKING_CANCELLED = "Not sent: the booking was cancelled."
STARTED = "Not sent: the showing had started."
NOT_TEXTED = (
    "Not sent: this person is not texted any more (no mobile, no consent, or a wrong number)."
)
NOTHING_LEFT = "Not sent: every showing it listed was cancelled or has started."
NO_HOST_MOBILE = "Not sent: the host has no mobile number."
MOVED_AGAIN = "Not sent: the showing has changed host again."
HOSTING_AGAIN = "Not sent: this host has the showing again."
SHOWING_CANCELLED = "Not sent: the showing was cancelled."
NO_SHOW = "Not sent: this person was marked a no-show."
MORNING_MISSED = "Not sent: the morning had passed."
NOTHING_TODAY = "Not sent: the host has no showing left today."

MORNING = time(8, 0)
# A morning text not sent by noon is not sent at all.
MORNING_ENDS = time(12, 0)
# The apply text is owed two hours after a showing's start, for showings that
# started up to two days ago.
APPLY_AFTER = timedelta(hours=2)
APPLY_LOOKBACK = timedelta(days=2)


@dataclass(frozen=True)
class Verdict:
    """Go (both empty), cancel with a note, or wait until ``later``."""

    cancel: str | None = None
    later: datetime | None = None


GO = Verdict()


def can_text(attendee: ShowingAttendee) -> bool:
    """A mobile, texts accepted, and no sign the number is wrong."""
    return bool(
        attendee.mobile
        and attendee.texts_accepted
        and attendee.attention_reason not in WRONG_NUMBER
    )


def _daytime(notice: ShowingNotice, now: datetime) -> Verdict:
    timezone = notice.payload.get("daytime_timezone")
    later = residents.next_daytime(now, timezone) if timezone else None
    return Verdict(later=later) if later is not None else GO


async def check(session: AsyncSession, notice: ShowingNotice, now: datetime) -> Verdict:
    """Whether ``notice`` (one of ``KINDS``) goes now. May re-point its recipient
    or re-render its text from things as they are now."""
    if notice.kind == PROSPECT_REMINDER_KIND:
        return await _prospect_reminder(session, notice, now)
    if notice.kind == RESIDENT_REMINDER_KIND:
        return await _resident_reminder(session, notice, now)
    if notice.kind == HOST_MORNING_KIND:
        return await _host_morning(session, notice, now)
    if notice.kind == APPLY_KIND:
        return await _apply_text(session, notice, now)
    return await _host_text(session, notice, now)


async def _prospect_reminder(
    session: AsyncSession, notice: ShowingNotice, now: datetime
) -> Verdict:
    row = (
        await session.execute(
            select(ShowingAttendee, Showing.status, Showing.starts_at)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(ShowingAttendee.id == notice.attendee_id)
        )
    ).first()
    if row is None or row[0].status != "booked" or row[1] != "scheduled":
        return Verdict(cancel=BOOKING_CANCELLED)
    attendee, _status, starts_at = row
    if starts_at <= now:
        return Verdict(cancel=STARTED)
    if not can_text(attendee):
        return Verdict(cancel=NOT_TEXTED)
    notice.recipient = attendee.mobile
    return _daytime(notice, now)


async def _resident_reminder(
    session: AsyncSession, notice: ShowingNotice, now: datetime
) -> Verdict:
    payload = TextPayload.model_validate(notice.payload)
    ahead = list(
        await session.scalars(
            select(Showing)
            .where(
                Showing.id.in_(payload.showing_ids),
                Showing.status == "scheduled",
                Showing.starts_at > now,
            )
            .order_by(Showing.starts_at)
        )
    )
    if not ahead:
        return Verdict(cancel=NOTHING_LEFT)
    if len(ahead) < len(payload.showing_ids):
        timezone = payload.daytime_timezone or "UTC"
        body = wording.resident_reminder_text(
            [
                wording.Visit(
                    unit=showing.unit,
                    address=showing.address,
                    starts_at=showing.starts_at,
                    ends_at=showing.ends_at,
                    timezone=timezone,
                )
                for showing in ahead
            ]
        )
        notice.payload = payload.model_copy(update={"body": body}).model_dump(
            mode="json", exclude_defaults=True
        )
    return _daytime(notice, now)


async def _host_text(session: AsyncSession, notice: ShowingNotice, now: datetime) -> Verdict:
    showing = await session.get_one(Showing, notice.showing_id)
    if showing.starts_at <= now:
        return Verdict(cancel=STARTED)
    if notice.kind in HOST_MOVED_KINDS:
        # Each goes to the host it names (``agent:<id>:moved:<move>``), the old one too.
        if showing.status != "scheduled":
            return Verdict(cancel=SHOWING_CANCELLED)
        # Each says something about the host it names, true only while it still
        # holds: "now yours" while they host it, "no longer yours" while they do
        # not. A quick move back (A to B to A) makes the older one false.
        named = int(notice.recipient_key.split(":")[1])
        if notice.kind == HOST_MOVED_TO_KIND and named != showing.agent_id:
            return Verdict(cancel=MOVED_AGAIN)
        if notice.kind == HOST_MOVED_FROM_KIND and named == showing.agent_id:
            return Verdict(cancel=HOSTING_AGAIN)
        return GO
    if notice.kind == HOST_JOIN_KIND:
        status = await session.scalar(
            select(ShowingAttendee.status).where(ShowingAttendee.id == notice.attendee_id)
        )
        if status != "booked" or showing.status != "scheduled":
            return Verdict(cancel=BOOKING_CANCELLED)
    # The showing's host now, should the team have given it to someone else.
    mobile = await session.scalar(
        select(ShowingAgentRecord.mobile).where(ShowingAgentRecord.id == showing.agent_id)
    )
    if not mobile:
        return Verdict(cancel=NO_HOST_MOBILE)
    notice.recipient = mobile
    return GO


# ---------------------------------------------------------------------------
# The host's morning text and the apply text
# ---------------------------------------------------------------------------


def morning_key(agent_id: int, day: date) -> str:
    """The recipient key of one host's morning text for one day."""
    return f"agent:{agent_id}:morning:{day.isoformat()}"


def _morning_of(recipient_key: str) -> tuple[int, date]:
    _, agent_id, _, day = recipient_key.split(":")
    return int(agent_id), date.fromisoformat(day)


async def day_showings(
    session: AsyncSession, agent_id: int, day: date, timezone: str, now: datetime
) -> list[tuple[Showing, int]]:
    """The agent's scheduled showings on ``day`` in ``timezone`` that have not
    started by ``now``, by start, each with how many people are booked."""
    zone = ZoneInfo(timezone)
    day_starts = datetime.combine(day, time(0, 0), zone)
    day_ends = datetime.combine(day + timedelta(days=1), time(0, 0), zone)
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
            Showing.starts_at >= day_starts,
            Showing.starts_at < day_ends,
            Showing.starts_at > now,
        )
        .order_by(Showing.starts_at, Showing.id)
    )
    return [(showing, count) for showing, count in rows.all()]


def morning_body(showings: list[tuple[Showing, int]], timezone: str) -> str:
    return wording.host_morning_text(
        [
            wording.DayShowing(
                visit=wording.Visit(
                    unit=showing.unit,
                    address=showing.address,
                    starts_at=showing.starts_at,
                    ends_at=showing.ends_at,
                    timezone=timezone,
                ),
                booked=count,
            )
            for showing, count in showings
        ]
    )


async def _host_morning(session: AsyncSession, notice: ShowingNotice, now: datetime) -> Verdict:
    payload = TextPayload.model_validate(notice.payload)
    timezone = payload.daytime_timezone or "UTC"
    agent_id, day = _morning_of(notice.recipient_key)
    if now >= datetime.combine(day, MORNING_ENDS, ZoneInfo(timezone)):
        return Verdict(cancel=MORNING_MISSED)
    showings = await day_showings(session, agent_id, day, timezone, now)
    if not showings:
        return Verdict(cancel=NOTHING_TODAY)
    mobile = await session.scalar(
        select(ShowingAgentRecord.mobile).where(ShowingAgentRecord.id == agent_id)
    )
    if not mobile:
        return Verdict(cancel=NO_HOST_MOBILE)
    notice.recipient = mobile
    notice.payload = payload.model_copy(
        update={
            "body": morning_body(showings, timezone),
            "showing_ids": [showing.id for showing, _ in showings],
        }
    ).model_dump(mode="json", exclude_defaults=True)
    return GO


async def _apply_text(session: AsyncSession, notice: ShowingNotice, now: datetime) -> Verdict:
    row = (
        await session.execute(
            select(ShowingAttendee, Showing.status)
            .join(Showing, Showing.id == ShowingAttendee.showing_id)
            .where(ShowingAttendee.id == notice.attendee_id)
        )
    ).first()
    if row is None or row[0].status != "booked":
        return Verdict(cancel=BOOKING_CANCELLED)
    attendee, showing_status = row
    if showing_status == "cancelled":
        return Verdict(cancel=SHOWING_CANCELLED)
    if attendee.attendance == "no_show":
        return Verdict(cancel=NO_SHOW)
    if not can_text(attendee):
        return Verdict(cancel=NOT_TEXTED)
    notice.recipient = attendee.mobile
    return _daytime(notice, now)
