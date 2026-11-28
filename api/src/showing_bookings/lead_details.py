"""What a lead's own link shows on a unit's booking page, and adding their details there.

A place the lead agent books from a reply (``lead_bookings.py``) is
provisional: a name, the Zillow thread, maybe a mobile from the thread, no
email and no consent to texts. The reply carries the lead's own booking link
(``?t=<token>``, ``zillow_leads.lead_links``). On that unit's booking page,
the token finds the provisional place, and the page shows the time already
booked with the usual short form (name, mobile, email, the ticks) instead of
the time picker.

- ``place(unit, token)``: the place's unit, address, start and end, time zone,
  whether the lead's details are in (``details_added``) and whether the
  confirmation text went (``texted``). Nothing about the
  host or anyone else in the showing, no name and no cancel token. With a
  provisional place, the page asks for the details. With none but a place that
  has them (the lead completed it, booked it through the link, or a move by
  email kept them), the page says they are booked, with when and where and
  that the text has (or will have) the link to cancel. 404 ``no_booking`` when the token's
  thread holds neither at that unit, or the lead agent's switch is off: the
  page then shows the normal booking form.
- ``unit_for_place(unit, token)``: the unit as the booking page shows it, for a
  lead with such a place at a unit that may not be booked now, so that their
  link keeps working.
- ``complete(unit, body)``: the fields and ticks are checked as for a page
  booking, then Turnstile, then, under the booking locks, the page's limits
  (not counting this place) and the place again. The same attendee gets the
  details and the consent record, and the confirmation text is queued with
  the prospect's invite held behind it; when the host's event already went
  out, an update held behind it too, so the host sees the new details. The
  host's event and the residents' text went when the reply was sent, so
  nothing else is queued. The confirmation text is delivered at once; if Quo
  refuses the number, the details are undone (the place stays booked, as the
  email reply said) and the answer names the mobile field, so the person can
  try another number. A completed place cannot be completed again (409
  ``already_completed``).

The token travels in the request body, never in a path or query string.
"""

from datetime import datetime

import logfire
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.showing_bookings import (
    bookings,
    delivery_checks,
    holds,
    host_event,
    lead_bookings,
    outbox,
    wording,
)
from api.src.showing_bookings.models import (
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import CalendarPayload, TextPayload
from api.src.showing_bookings.settings import settings_for_delivery
from api.src.zillow_leads import lead_links

NO_BOOKING = "We couldn't find a showing booked for you at this unit."
UNDONE_NOTE = "Not sent: the details were undone because the number could not be texted."


class LeadTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lead_token: str = ""


class LeadPlaceView(BaseModel):
    """The lead's own place, as their link's page shows it."""

    unit: str
    address: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    # False while the page asks for their details; true when it says they are booked.
    details_added: bool
    # Their confirmation text went out, or may have: the page says where the text with
    # the cancel link is only once it did.
    texted: bool


def _no_booking() -> bookings.Refused:
    return bookings.Refused(404, "no_booking", NO_BOOKING)


def _provisional(attendee: ShowingAttendee) -> bool:
    """Booked by the lead agent, and the lead's details not in yet."""
    return attendee.source == "lead_agent" and attendee.texts_consent_at is None


def _places(thread_id: str, unit: str, now: datetime):
    """The thread's booked places at ``unit``, their showings scheduled and ahead."""
    return (
        select(ShowingAttendee, Showing)
        .join(Showing, Showing.id == ShowingAttendee.showing_id)
        .where(
            ShowingAttendee.zillow_thread_id == thread_id,
            ShowingAttendee.status == "booked",
            Showing.status == "scheduled",
            Showing.starts_at > now,
            func.lower(Showing.unit) == unit.strip().casefold(),
        )
    )


async def _find(
    session: AsyncSession, unit: str, token: str | None, now: datetime, *, lock: bool = False
) -> tuple[ShowingAttendee, Showing] | None:
    """The token's thread's place at ``unit`` that the lead agent booked: the one
    still without details when there is one, else the first."""
    thread_id = await lead_links.thread_for_token(token, session)
    if thread_id is None:
        return None
    query = (
        _places(thread_id, unit, now)
        .where(ShowingAttendee.source == "lead_agent")
        .order_by(
            ShowingAttendee.texts_consent_at.is_not(None), Showing.starts_at, ShowingAttendee.id
        )
        .limit(1)
    )
    if lock:
        query = query.with_for_update(of=ShowingAttendee)
    row = (await session.execute(query)).first()
    return (row[0], row[1]) if row is not None else None


async def place(unit: str, body: LeadTokenRequest, *, now: datetime) -> LeadPlaceView:
    """The lead's own place for their link's page. Raises ``Refused`` 404.

    A provisional place first; else the first with the lead's details (an email
    and recorded consent to texts). Never one kept from a move whose reply is
    not settled: the lead may not have heard of it yet.
    """
    if not await lead_bookings.enabled():
        raise _no_booking()
    async with AsyncSessionFactory() as session:
        thread_id = await lead_links.thread_for_token(body.lead_token, session)
        if thread_id is None:
            raise _no_booking()
        query = _places(thread_id, unit, now).order_by(Showing.starts_at, ShowingAttendee.id)
        rows = (await session.execute(query)).all()
        found = next((row for row in rows if _provisional(row[0])), None) or next(
            (
                row
                for row in rows
                if row[0].email
                and row[0].texts_consent_at is not None
                and not delivery_checks.reply_unsettled(row[0])
            ),
            None,
        )
        if found is None:
            raise _no_booking()
        rules = await listings_service.read_showing_rules(session)
        attendee, showing = found
        texted = await bookings.was_texted(session, attendee)
    return LeadPlaceView(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=rules.timezone,
        details_added=not _provisional(attendee),
        texted=texted,
    )


async def unit_for_place(unit: str, body: LeadTokenRequest, *, now: datetime) -> bookings.UnitPage:
    """The unit as the booking page shows it, for a lead with a place there that
    ``place`` answers. Raises ``Refused`` 404.

    For a unit that may not be booked now (``settings.unit_open``), whose own read
    answers 404: the lead's link still shows their place and takes their details.
    No times to pick.
    """
    await place(unit, body, now=now)
    return await bookings.unit_page(unit, now=now, for_a_place=True)


class DetailsRequest(bookings.BookingRequest):
    """The booking form's body without a time: the place already has one."""


async def complete(
    unit: str,
    body: DetailsRequest,
    facts: bookings.RequestFacts,
    *,
    now: datetime,
    texts: TextSender | None = None,
) -> bookings.BookingResult:
    """Add the lead's details to their provisional place. Raises ``Refused``."""
    async with AsyncSessionFactory() as session:
        listing, rules = await bookings._listing(session, unit, now, only_open=False)
        settings = await settings_for_delivery(session)
        found = await _find(session, listing.unit, body.lead_token, now)
    if found is None or not await lead_bookings.enabled():
        raise _no_booking()
    attendee, showing = found
    if attendee.texts_consent_at is not None:
        raise _already_completed()
    checked = bookings._check_fields(
        body.model_copy(update={"start": showing.starts_at.isoformat()}), listing, now
    )
    await bookings._check_turnstile(body.turnstile_token, facts.ip)

    keys = [bookings.BOOKING_LOCK, f"showing-mobile:{checked.mobile}"]
    keys.append(f"showing-email:{checked.email}")
    keys.append(f"showing-thread:{attendee.zillow_thread_id}")
    try:
        async with AsyncSessionFactory() as session, session.begin():
            await bookings.lock(session, *keys)
            await bookings._check_limits(
                session,
                settings.public_limits,
                mobile=checked.mobile,
                email=checked.email,
                ip=facts.ip,
                now=now,
                exclude=attendee.id,
            )
            found = await _find(session, listing.unit, body.lead_token, now, lock=True)
            if found is None or found[0].id != attendee.id:
                raise _no_booking()
            attendee, showing = found
            if attendee.texts_consent_at is not None:
                raise _already_completed()
            taken = await session.scalar(
                select(ShowingAttendee.id).where(
                    ShowingAttendee.showing_id == showing.id,
                    ShowingAttendee.status == "booked",
                    ShowingAttendee.mobile == checked.mobile,
                    ShowingAttendee.id != attendee.id,
                )
            )
            if taken is not None:
                raise bookings.Refused(
                    409,
                    "already_booked",
                    "This number is already booked for that showing.",
                    {"mobile": "Already booked for this showing."},
                )
            before = _snapshot(attendee)
            attempt = 1 + await session.scalar(
                select(func.count())
                .select_from(ShowingNotice)
                .where(
                    ShowingNotice.attendee_id == attendee.id,
                    ShowingNotice.kind == bookings.CONFIRMATION_KIND,
                )
            )
            attendee.name = checked.name
            attendee.mobile = checked.mobile
            attendee.email = checked.email
            attendee.texts_accepted = True
            attendee.texts_consent_at = now
            attendee.texts_consent_version = wording.TEXTS_CONSENT_VERSION
            attendee.texts_consent_page = f"{bookings.public_base_url()}/showings/{listing.unit}"
            attendee.requirement_confirmations = [
                item.model_dump(mode="json") for item in checked.confirmations
            ]
            attendee.lead_token = body.lead_token
            attendee.user_agent = (facts.user_agent or None) and facts.user_agent[:500]
            attendee.signature_agent = (facts.signature_agent or None) and facts.signature_agent[
                :500
            ]
            attendee.client_ip_hash = bookings.ip_hash(facts.ip)
            if attendee.attention_reason == lead_bookings.REPLY_UNCERTAIN:
                # The lead has the time: the reply reached them.
                attendee.attention_reason = None
            await session.flush()
            confirmation_id = await _queue_notices(
                session, showing, attendee, rules=rules, attempt=attempt, now=now
            )
            attendee_id, cancel_token = attendee.id, attendee.cancel_token
    except IntegrityError:
        raise bookings.Refused(
            409,
            "already_booked",
            "This number is already booked for that showing.",
            {"mobile": "Already booked for this showing."},
        ) from None

    status = await bookings._send_confirmation(confirmation_id, now=now, texts=texts)
    if status == "refused":
        await _undo(attendee_id, confirmation_id, before)
        raise bookings.Refused(
            422,
            "number_refused",
            bookings.NUMBER_REFUSED,
            {"mobile": "We couldn't text this number."},
        )
    logfire.info("lead added details to a provisional showing", attendee_id=attendee_id)
    async with AsyncSessionFactory() as session:
        attendee, showing = await bookings.by_token(session, cancel_token)
        view = await bookings.view_of(session, showing, attendee, rules.timezone, now)
    return bookings.BookingResult(
        cancel_token=cancel_token, kind="details", confirmation=status, booking=view
    )


def _already_completed() -> bookings.Refused:
    return bookings.Refused(
        409,
        "already_completed",
        "Your details are already saved for this showing. Use the link in your text to change it.",
    )


def _snapshot(attendee: ShowingAttendee) -> dict:
    return {
        "name": attendee.name,
        "mobile": attendee.mobile,
        "email": attendee.email,
        "lead_token": attendee.lead_token,
        "user_agent": attendee.user_agent,
        "signature_agent": attendee.signature_agent,
        "client_ip_hash": attendee.client_ip_hash,
    }


async def _queue_notices(
    session: AsyncSession,
    showing: Showing,
    attendee: ShowingAttendee,
    *,
    rules,
    attempt: int,
    now: datetime,
) -> int:
    """The confirmation text, due now, and what waits behind it: the invite and,
    when the host's event already went out, an update with the new details."""
    key = f"attendee:{attendee.id}:details:{attempt}"
    visit = bookings.visit_of(showing, rules)
    link = bookings.cancel_url(attendee.cancel_token)
    confirmation = await outbox.enqueue(
        session,
        kind=bookings.CONFIRMATION_KIND,
        channel="sms",
        showing_id=showing.id,
        attendee_id=attendee.id,
        recipient=attendee.mobile,
        recipient_key=key,
        payload=TextPayload(body=wording.confirmation_text(visit, link)),
        now=now,
    )
    host = await session.get_one(ShowingAgentRecord, showing.agent_id)
    calendar_id = await host_event.showings_calendar(session, host)
    await outbox.enqueue(
        session,
        kind=bookings.INVITE_KIND,
        channel="calendar_invite",
        showing_id=showing.id,
        attendee_id=attendee.id,
        recipient=attendee.email,
        recipient_key=key,
        payload=CalendarPayload(
            operation="create",
            calendar_id=calendar_id,
            summary=wording.invite_summary(visit, attendee.name),
            description=wording.invite_description(visit, link, bookings.team_phone_or_none()),
            location=showing.address,
            starts_at=showing.starts_at,
            ends_at=showing.ends_at,
            attendees=[attendee.email],
        ),
        now=now,
        waits_for_notice_id=confirmation.id,
    )
    delivered = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing.id,
            ShowingNotice.kind.in_(host_event.KINDS),
            host_event.since_last_move(showing.id),
            ShowingNotice.status.in_(["sent", "captured"]),
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    if delivered is not None:
        payload = await host_event.render(
            session,
            showing,
            calendar_id=delivered.payload.get("calendar_id") or bookings.NO_SHOWINGS_CALENDAR,
        )
        if payload is not None:
            await outbox.enqueue(
                session,
                kind=bookings.HOST_UPDATE_KIND,
                channel="calendar_event",
                showing_id=showing.id,
                attendee_id=attendee.id,
                recipient=delivered.recipient,
                recipient_key=f"agent:{showing.agent_id}:{key}",
                payload=payload,
                now=now,
                waits_for_notice_id=confirmation.id,
            )
    return confirmation.id


async def _undo(attendee_id: int, confirmation_id: int, before: dict) -> None:
    """Quo refused the number: the place goes back to provisional, and what
    waited for the text is stopped. The place itself stays booked."""
    async with AsyncSessionFactory() as session, session.begin():
        await bookings.lock(session, bookings.BOOKING_LOCK)
        attendee = await session.scalar(
            select(ShowingAttendee).where(ShowingAttendee.id == attendee_id).with_for_update()
        )
        for name, value in before.items():
            setattr(attendee, name, value)
        attendee.texts_accepted = False
        attendee.texts_consent_at = None
        attendee.texts_consent_version = None
        attendee.texts_consent_page = None
        attendee.requirement_confirmations = []
        attendee.attention_reason = None
        await holds.stop_waiting_on(
            session, confirmation_id, note=UNDONE_NOTE, include_released=True
        )
    logfire.info("lead details undone: number refused", attendee_id=attendee_id)
