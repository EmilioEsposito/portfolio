"""The checks a reminder or a host's text gets each time it is about to be delivered.

``outbox._prepare`` asks ``check`` about every notice of these kinds, first
try or retry, under the lock on the notice:

- A prospect's reminder goes only while the person is still booked, the
  showing still scheduled and not started, and the number still one we text
  (``can_text``: a mobile, texts accepted, no sign it is wrong, and not a place
  whose lead agent's reply is not settled). It goes to the mobile on the
  booking now, so a corrected number gets it.
- The residents' reminder lists only the showings it was queued for that are
  still scheduled and not started; with none left it is not sent.
- A lead's reminder by email (``LEAD_EMAIL_REMINDER_KIND``) goes only while
  the place is still booked, the showing still scheduled and not started, the
  lead agent's switch on, and the lead still has no mobile we text (once they
  add one with consent, the reminder text is theirs instead). Its recipient,
  subject and the message it answers are read again from the lead
  conversation's stored route (``lead_email.route_of``); when that cannot be
  found, or is no longer a Zillow relay address, the notice fails with the
  reason, so the team is alerted, and nothing is sent.
- Every reminder goes out only from 8 AM to 9 PM in the showing rules' time
  zone (the payload's ``daytime_timezone``), waiting for 8 AM otherwise.
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

A captured message a person sends by hand (``captured.py``) gets one check more,
``by_hand_problem``, first in the same place: it is not too late (``too_late``,
the one rule for that, which the Captured messages list and the send use too),
and what it says still stands.

Nothing here imports the outbox or sends anything; the outbox applies the
answer (``Verdict``).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings import guard, host_event, lead_email, residents, wording
from api.src.showing_bookings.lead_email import RouteProblem
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.schema import EmailPayload, TextPayload
from api.src.showing_bookings.settings import (
    current_environment,
    read_test_recipients,
    settings_for_delivery,
)

PROSPECT_REMINDER_KIND = "prospect_reminder"
RESIDENT_REMINDER_KIND = "resident_reminder"
# The reminder by email in a lead's Zillow thread (``lead_email.py``).
LEAD_EMAIL_REMINDER_KIND = "lead_email_reminder"
REMINDER_KINDS = frozenset(
    {PROSPECT_REMINDER_KIND, RESIDENT_REMINDER_KIND, LEAD_EMAIL_REMINDER_KIND}
)
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
# Quo's refusals of a number, as ``quo.py`` and ``fakes.py`` name them.
NUMBER_REFUSALS = frozenset({"opted_out", "invalid_number", "unreachable_number"})
# The texts to a prospect that their showing is cancelled (``cancelling.py``).
CANCELLED_TEXT_KINDS = frozenset({"prospect_cancelled", "prospect_cancelled_by_team"})

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
TEXTED_INSTEAD = "Not sent: they added a mobile for texts, so the reminder goes by text."
REPLY_NOT_SENT = "Not sent: the reply that booked it was not sent."
SWITCH_OFF = "Not sent: the lead agent's bookings are switched off."
NO_SHOW = "Not sent: this person was marked a no-show."
MORNING_MISSED = "Not sent: the morning had passed."
NOTHING_TODAY = "Not sent: the host has no showing left today."
APPLY_MISSED = "Not sent: the showing was more than two days ago."
NUMBER_CHANGED = "Not sent: the booking's mobile was changed after this was written."

MORNING = time(8, 0)
# A morning text not sent by noon is not sent at all.
MORNING_ENDS = time(12, 0)
# The apply text is owed two hours after a showing's start, for showings that
# started up to two days ago.
APPLY_AFTER = timedelta(hours=2)
APPLY_LOOKBACK = timedelta(days=2)


@dataclass(frozen=True)
class Verdict:
    """Go (all empty), cancel with a note, fail with a reason, or wait until ``later``.

    ``fail`` is for a notice that cannot go and needs the team: saved as
    failed, so the team's alert picks it up."""

    cancel: str | None = None
    later: datetime | None = None
    fail: RouteProblem | None = None


GO = Verdict()


def reply_unsettled(attendee: ShowingAttendee) -> bool:
    """A place the lead agent booked for a reply whose send is not settled: neither
    sent nor possibly sent (uncertain), so the lead may never have heard of it.

    The process that booked it is about to send the reply, or stopped before it
    did. Nothing is texted to the lead about such a place, even when it kept
    their details from the showing it replaces (``lead_bookings.py``). A lead
    who completed it through their own link (``lead_token``) knows of it.
    """
    return bool(
        attendee.source == "lead_agent"
        and attendee.lead_conversation_id is not None
        and attendee.lead_token is None
        and attendee.lead_reply_sent_at is None
        and attendee.lead_reply_uncertain_at is None
    )


def reply_unsettled_clause():
    """The SQL for ``reply_unsettled``."""
    return and_(
        ShowingAttendee.source == "lead_agent",
        ShowingAttendee.lead_conversation_id.is_not(None),
        ShowingAttendee.lead_token.is_(None),
        ShowingAttendee.lead_reply_sent_at.is_(None),
        ShowingAttendee.lead_reply_uncertain_at.is_(None),
    )


def can_text(attendee: ShowingAttendee) -> bool:
    """A mobile, texts accepted, no sign the number is wrong, and not a place whose
    reply is not settled (``reply_unsettled``)."""
    return bool(
        attendee.mobile
        and attendee.texts_accepted
        and attendee.attention_reason not in WRONG_NUMBER
        and not reply_unsettled(attendee)
    )


# ---------------------------------------------------------------------------
# A captured message sent by hand
# ---------------------------------------------------------------------------


async def too_late(session: AsyncSession, notice: ShowingNotice, now: datetime) -> str | None:
    """Why a captured message can no longer be sent by hand, or None while it can.

    The one place that says when each kind is too late:

    - the apply text: two days after the showing's start, as it is owed for;
    - the host's morning text: noon of its day;
    - the residents' reminder, which lists a day's showings: the start of the
      last one it lists;
    - everything else: the showing's start. A cancelled showing keeps its start,
      so its cancellations can be sent until then too.
    """
    starts_at = await session.scalar(
        select(Showing.starts_at).where(Showing.id == notice.showing_id)
    )
    # Read without the payload's model: one cleared after 12 months no longer fits it.
    payload = notice.payload
    if notice.kind == APPLY_KIND:
        return APPLY_MISSED if now >= starts_at + APPLY_LOOKBACK else None
    if notice.kind == HOST_MORNING_KIND:
        _, day = _morning_of(notice.recipient_key)
        ends = _morning_ends(day, payload.get("daytime_timezone") or "UTC")
        return MORNING_MISSED if now >= ends else None
    if notice.kind == RESIDENT_REMINDER_KIND:
        last = await session.scalar(
            select(func.max(Showing.starts_at)).where(
                Showing.id.in_(payload.get("showing_ids") or [])
            )
        )
        return STARTED if now >= (last or starts_at) else None
    return STARTED if now >= starts_at else None


async def by_hand_problem(
    session: AsyncSession, notice: ShowingNotice, now: datetime
) -> str | None:
    """Why a message a person sends by hand does not go, beyond what any message is
    checked for when it is delivered; None when there is nothing more."""
    late = await too_late(session, notice, now)
    if late is not None:
        return late
    if notice.kind == host_event.CONFIRMATION_KIND or notice.kind in CANCELLED_TEXT_KINDS:
        attendee = await session.get(ShowingAttendee, notice.attendee_id or 0)
        if attendee is None or not can_text(attendee):
            return NOT_TEXTED
        if attendee.mobile != notice.recipient:
            return NUMBER_CHANGED
    if notice.kind in host_event.KINDS | {host_event.CANCEL_KIND}:
        if await host_event.before_last_move(session, notice):
            return host_event.HOST_CHANGED
    return None


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
    if notice.kind == LEAD_EMAIL_REMINDER_KIND:
        return await _lead_email_reminder(session, notice, now)
    if notice.kind == HOST_MORNING_KIND:
        return await _host_morning(session, notice, now)
    if notice.kind == APPLY_KIND:
        return await _apply_text(session, notice, now)
    return await _host_text(session, notice, now)


def owes_lead_email(attendee: ShowingAttendee) -> bool:
    """A place booked in a Zillow thread whose confirming reply was sent, for a lead
    with no mobile we text. One whose reply is uncertain (``reply_uncertain``,
    no ``lead_reply_sent_at``) or was never sent is owed nothing."""
    return (
        attendee.source == "lead_agent"
        and attendee.lead_reply_sent_at is not None
        and not can_text(attendee)
    )


async def _lead_email_reminder(
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
    if can_text(attendee):
        return Verdict(cancel=TEXTED_INSTEAD)
    if not owes_lead_email(attendee):
        return Verdict(cancel=REPLY_NOT_SENT)
    if not (await settings_for_delivery(session)).lead_agent_uses_open_times:
        return Verdict(cancel=SWITCH_OFF)
    # Who it goes to: the stored route of the conversation that booked the
    # place, read now. Never the attendee's own fields or anything a lead wrote.
    route = await lead_email.route_of(
        session, attendee.lead_conversation_id, attendee.zillow_thread_id
    )
    if isinstance(route, RouteProblem):
        return Verdict(fail=route)
    payload = EmailPayload.model_validate(notice.payload)
    notice.recipient = route.to
    notice.payload = payload.model_copy(
        update={
            "subject": route.subject,
            "conversation_id": attendee.lead_conversation_id,
            "reply_to_message_id": route.reply_to_message_id,
        }
    ).model_dump(mode="json", exclude_defaults=True)
    return _daytime(notice, now)


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


def named_agent(recipient_key: str) -> int:
    """The agent a host's notice names in its recipient key, ``agent:<id>...``."""
    return int(recipient_key.split(":")[1])


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
        named = named_agent(notice.recipient_key)
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


def _morning_ends(day: date, timezone: str) -> datetime:
    return datetime.combine(day, MORNING_ENDS, ZoneInfo(timezone))


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


async def morning_selection(
    session: AsyncSession,
    showings: list[tuple[Showing, int]],
    mobile: str,
    *,
    approved: bool = False,
) -> list[tuple[Showing, int]]:
    """The showings a host's morning text lists, from the day's ``showings``.

    The guard judges a notice by the unit of the showing it is attached to, and
    this text lists several. So it lists only the showings the guard would really
    send for to ``mobile`` now, and is attached to the first of them; when there are
    none it lists them all and is captured as usual. Never a list mixing showings
    the guard would send for and ones it would not. Sent by hand (``approved``) it
    lists them all, as the guard then lets every unit through."""
    settings = await settings_for_delivery(session)
    environment = current_environment()
    test_recipients = read_test_recipients()
    sent_for = [
        item
        for item in showings
        if guard.would_send(
            settings=settings,
            environment=environment,
            unit=item[0].unit,
            addresses=[mobile],
            test_recipients=test_recipients,
            approved=approved,
        )
    ]
    return sent_for or showings


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
    if now >= _morning_ends(day, timezone):
        return Verdict(cancel=MORNING_MISSED)
    showings = await day_showings(session, agent_id, day, timezone, now)
    if not showings:
        return Verdict(cancel=NOTHING_TODAY)
    mobile = await session.scalar(
        select(ShowingAgentRecord.mobile).where(ShowingAgentRecord.id == agent_id)
    )
    if not mobile:
        return Verdict(cancel=NO_HOST_MOBILE)
    showings = await morning_selection(
        session, showings, mobile, approved=notice.send_approved_at is not None
    )
    notice.recipient = mobile
    # Attached to the first showing it lists, which the guard then judges.
    notice.showing_id = showings[0][0].id
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
