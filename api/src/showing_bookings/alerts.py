"""One alert to the team for each notice that failed or is stuck.

A notice needs the team (``attention``) when:

- failed: it was refused (``failed``) or may have gone out (``unknown``), and
  either way it will not be tried again. Not a prospect's confirmation text
  whose number Quo refused: the person was told on the booking page at once,
  and the booking is withdrawn or marked for the team on the Showings section.
  One sent by hand (``captured.py``) counts, as nobody is on the page by then.
- stuck: it was due ten minutes ago and has neither gone out nor been
  captured, skipped or cancelled (``pending``, and no send is under way; a
  send cut off midway becomes unknown after ten minutes and counts as failed).
  Or it is still held ten minutes after the five-minute wait for its
  confirmation's delivery report ran out: fifteen minutes after that text went.

A notice merely captured is never alerted about.

``check(now=)`` is the last step of the job every minute
(``holds.send_pending``), after the drain, so a notice the drain has just
delivered is not alerted about. For each notice that needs the team it first
saves ``alerted_at`` and ``alert_reason`` on the row and commits, then sends
the alert through ``outbox.send_team_alert``: so it is never alerted about
twice, even when the alert itself fails. The alert names the kind of notice,
the unit and the showing's time, never a prospect's or a resident's number.

The channel is the Sernia web push the approval flow already uses
(``push.py``), pushed in production only. Outside production the alert is
logged and kept on the row, never pushed; tests pass a stand-in.

Only notices from the last two days count, so a first run in production never
alerts about old rows.
"""

from datetime import datetime, timedelta

import logfire
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import aliased

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.showing_bookings import holds, outbox, wording
from api.src.showing_bookings.host_event import CONFIRMATION_KIND
from api.src.showing_bookings.models import Showing, ShowingNotice
from api.src.showing_bookings.providers import TeamAlerter
from api.src.utils import team_links

STUCK_AFTER = timedelta(minutes=10)
# Held: the five-minute wait for a delivery report, then ten more minutes.
HELD_STUCK_AFTER = holds.REPORT_WAIT + STUCK_AFTER
LOOKBACK = timedelta(days=2)
PER_RUN = 20
# Where the alert opens: the Booking settings tab, whose Messages list shows it.
TAB_PATH = team_links.LEAD_BOOKINGS_PATH
FAILED_STATUSES = ("failed", "unknown")


def attention_of(
    notice: ShowingNotice, now: datetime, *, holder_delivered_at: datetime | None = None
) -> str | None:
    """ "failed", "stuck" or None for one notice; ``holder_delivered_at`` is when the
    confirmation a held notice waits for went out. The same rule as ``_needs_team``."""
    if notice.status in FAILED_STATUSES:
        refused = (
            notice.status == "failed"
            and notice.kind == CONFIRMATION_KIND
            and notice.provider_status in holds.NUMBER_REFUSALS
            and notice.send_approved_at is None
        )
        return None if refused else "failed"
    if notice.status == "pending":
        if notice.sending_started_at is None and notice.not_before <= now - STUCK_AFTER:
            return "stuck"
        return None
    if notice.status == "held" and holder_delivered_at is not None:
        return "stuck" if holder_delivered_at <= now - HELD_STUCK_AFTER else None
    return None


def _needs_team(now: datetime, holder):
    """The SQL for ``attention_of``, over the last two days."""
    since = now - LOOKBACK
    failed = and_(
        ShowingNotice.status.in_(FAILED_STATUSES),
        ShowingNotice.delivered_at >= since,
        ~and_(
            ShowingNotice.status == "failed",
            ShowingNotice.kind == CONFIRMATION_KIND,
            ShowingNotice.provider_status.in_(holds.NUMBER_REFUSALS),
            ShowingNotice.send_approved_at.is_(None),
        ),
    )
    stuck = and_(
        ShowingNotice.status == "pending",
        ShowingNotice.sending_started_at.is_(None),
        ShowingNotice.not_before <= now - STUCK_AFTER,
        ShowingNotice.not_before >= since,
    )
    held = and_(
        ShowingNotice.status == "held",
        holder.delivered_at <= now - HELD_STUCK_AFTER,
        holder.delivered_at >= since,
    )
    return or_(failed, stuck, held)


async def check(*, now: datetime, alerter: TeamAlerter | None = None) -> int:
    """Alert the team once about each notice that failed or is stuck. Returns how many."""
    holder = aliased(ShowingNotice)
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(ShowingNotice, Showing, holder.delivered_at)
                .join(Showing, Showing.id == ShowingNotice.showing_id)
                .outerjoin(holder, holder.id == ShowingNotice.waits_for_notice_id)
                .where(ShowingNotice.alerted_at.is_(None), _needs_team(now, holder))
                .order_by(ShowingNotice.id)
                .limit(PER_RUN)
            )
        ).all()
        if not rows:
            return 0
        rules = await listings_service.read_showing_rules(session)
        session.expunge_all()
    alerted = 0
    for notice, showing, holder_delivered_at in rows:
        reason = attention_of(notice, now, holder_delivered_at=holder_delivered_at)
        if reason is None:
            continue
        async with AsyncSessionFactory() as session, session.begin():
            claimed = await session.scalar(
                update(ShowingNotice)
                .where(ShowingNotice.id == notice.id, ShowingNotice.alerted_at.is_(None))
                .values(alerted_at=now, alert_reason=reason)
                .returning(ShowingNotice.id),
                execution_options={"synchronize_session": False},
            )
        if claimed is None:
            continue  # another run took it
        alerted += 1
        visit = wording.Visit(
            unit=showing.unit,
            address=showing.address,
            starts_at=showing.starts_at,
            ends_at=showing.ends_at,
            timezone=rules.timezone,
        )
        try:
            pushed = await outbox.send_team_alert(
                wording.alert_title(reason),
                wording.alert_body(notice.kind, visit, reason),
                url=TAB_PATH,
                alerter=alerter,
            )
        except Exception:
            # Saved as alerted already: never sent twice, and the tab still shows it.
            logfire.exception("showing alert could not be sent", notice_id=notice.id)
            continue
        logfire.warn(
            "showing notice needs the team",
            notice_id=notice.id,
            kind=notice.kind,
            reason=reason,
            unit=showing.unit,
            pushed=pushed,
        )
    return alerted
