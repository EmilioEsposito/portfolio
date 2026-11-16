"""Public bookings: what the booking page reads, a booking, a booking read back, a corrected
mobile, and the team's number behind Turnstile.

A booking, in order:

1. The unit must be a listing on offer; the fields (name, a US mobile, an
   email, a start time) and the confirmations the listing asks for are
   checked, all at once, so the page can mark every field that needs fixing.
2. Turnstile (``turnstile.py``).
3. In one transaction, holding advisory locks on the unit and start, the
   mobile and the email: the limits per mobile, email and IP address
   (``settings.PublicLimits``); ``open_times.recheck`` of the time, with fresh
   calendars; then the showing (new, or the one already booked at that unit
   and start, locked), the attendee with the consent record, the
   confirmations, the lead token (and the Zillow thread it belongs to, if any:
   ``zillow_leads.lead_links``; one place per thread in a showing), the user
   agent and the ``Signature-Agent`` header, and the notices: the confirmation text, due now; the host's event
   (created with the first attendee, an update for each one who joins) and
   the prospect's own invite, both held until the text is reported delivered
   (``holds.py``).
4. After the commit, the confirmation text is delivered at once. If Quo
   refuses the number (invalid, unreachable or opted out), the booking is
   withdrawn: the attendee is cancelled with that reason, a showing left with
   no one is cancelled, its notices are stopped, and the answer names the
   mobile field. Otherwise, for a showing starting within 2 hours, the host
   is texted at once (``host_texts.queue_join``, queued in step 3).

The locks make two bookings of one unit and start at the same moment one
showing with two attendees: the second waits, and its re-check then sees the
first as a join.

Every refusal is a ``Refused`` with an HTTP status, a code, a sentence and the
fields it is about.
"""

import hashlib
import hmac
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import (
    agents,
    holds,
    host_event,
    host_texts,
    open_times,
    outbox,
    resident_notices,
    residents,
    turnstile,
    wording,
)
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import (
    SHOWING_MINUTES,
    CalendarPayload,
    RequirementConfirmation,
    TextPayload,
    normalize_email,
    normalize_us_mobile,
)
from api.src.showing_bookings.settings import PublicLimits, settings_for_delivery
from api.src.utils.phone_config import PhoneConfigError, team_phone, us_phone_display
from api.src.zillow_leads import lead_links

PUBLIC_BASE_URL_ENV = "SHOWING_PUBLIC_BASE_URL"
DEFAULT_PUBLIC_BASE_URL = "https://serniacapital.com"
TEAM_EXTENSION_ENV = "SERNIA_TEAM_PHONE_EXTENSION"
DEFAULT_TEAM_EXTENSION = "1"
MAX_MOBILE_CORRECTIONS = 2
# A showings calendar that names no connected account: the outbox cannot
# write to it, so in live mode such a notice fails visibly instead of waiting.
NO_SHOWINGS_CALENDAR = "no-showings-calendar"
CONFIRMATION_KIND = host_event.CONFIRMATION_KIND
HOST_EVENT_KIND = host_event.CREATE_KIND
HOST_UPDATE_KIND = host_event.UPDATE_KIND
# Taken first by every public booking, so one booking's limits, re-check and
# insert never interleave with another's (travel time between buildings is
# checked in code, which the database's no-overlap rule cannot see).
BOOKING_LOCK = "showing-bookings:public-booking"
SITE_LIMIT_MESSAGE = "Online booking is paused right now. Call us to book."
INVITE_KIND = host_event.INVITE_KIND
WITHDRAWN_NOTE = "Not sent: the booking was withdrawn because the number could not be texted."
_LEAD_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")


class Refused(Exception):
    """A public request refused, in the shape the page shows next to its fields."""

    def __init__(
        self, status: int, code: str, message: str, fields: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or {}

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "fields": self.fields}


NUMBER_REFUSED = "We couldn't text this number. Check it, or call us instead."


@dataclass(frozen=True)
class RequestFacts:
    """What a public request says about where it came from."""

    ip: str | None = None
    user_agent: str | None = None
    signature_agent: str | None = None


def public_base_url() -> str:
    return (os.getenv(PUBLIC_BASE_URL_ENV, "").strip() or DEFAULT_PUBLIC_BASE_URL).rstrip("/")


def cancel_url(cancel_token: str) -> str:
    return f"{public_base_url()}/showings/cancel/{cancel_token}"


def unit_url(unit: str) -> str:
    """The unit's booking page, where a new time is a new booking."""
    return f"{public_base_url()}/showings/{unit}"


def units_url() -> str:
    """The units still on offer."""
    return f"{public_base_url()}/showings"


def team_extension() -> str:
    value = os.getenv(TEAM_EXTENSION_ENV, "").strip()
    return value if value.isdigit() else DEFAULT_TEAM_EXTENSION


def ip_hash(ip: str | None) -> str | None:
    """A keyed hash of the address, so the limit works without keeping the address."""
    if not ip:
        return None
    key = (os.getenv("SESSION_SECRET_KEY") or "showing-bookings").encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


def masked_mobile(e164: str | None) -> str | None:
    """ "•••-•••-••17": all but the last two digits hidden."""
    if not e164:
        return None
    return f"•••-•••-••{e164[-2:]}"


# ---------------------------------------------------------------------------
# What the page reads
# ---------------------------------------------------------------------------


class PublicOffer(BaseModel):
    start: datetime
    join: bool
    preferred: bool
    places_left: int


class UnitPage(BaseModel):
    """One unit as the booking page shows it. No host names."""

    unit: str
    address: str
    rent: int | None
    showing_minutes: int
    timezone: str
    confirmations: list[wording.Confirmation]
    # Send it back with a booking: if the wording changed in between, the
    # booking is refused so the person sees the new wording.
    confirmations_version: str
    consent_small_print: str
    privacy_path: str
    sms_terms_path: str
    turnstile_site_key: str | None
    offers: list[PublicOffer]


def _confirmations_version(confirmations: list[wording.Confirmation]) -> str:
    raw = json.dumps([item.model_dump() for item in confirmations], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _listing(
    session: AsyncSession, unit: str, now: datetime
) -> tuple[RentalListing, ShowingRules]:
    """The unit's listing and the rules; ``Refused`` 404 unless it is on offer."""
    try:
        rules = await listings_service.read_showing_rules(session)
        listings = await listings_service.read_listings(session)
    except RentalDataInvalid:
        logfire.exception("showing bookings: rental data fails its checks")
        raise Refused(503, "unavailable", "Booking is not available right now.") from None
    listing = next((item for item in listings if item.unit.casefold() == unit.casefold()), None)
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    if listing is None or not open_times.on_offer(listing, today):
        raise Refused(404, "unknown_unit", "This unit is not open for showings.")
    return listing, rules


async def unit_page(unit: str, *, now: datetime) -> UnitPage:
    async with AsyncSessionFactory() as session:
        listing, rules = await _listing(session, unit, now)
        try:
            found = await open_times.offers_for_unit(listing.unit, now=now, session=session)
        except (LookupError, RentalDataInvalid):
            raise Refused(404, "unknown_unit", "This unit is not open for showings.") from None
    confirmations = wording.confirmations_for(listing)
    return UnitPage(
        unit=listing.unit,
        address=listing.address,
        rent=listing.asking_rent,
        showing_minutes=SHOWING_MINUTES,
        timezone=rules.timezone,
        confirmations=confirmations,
        confirmations_version=_confirmations_version(confirmations),
        consent_small_print=wording.CONSENT_SMALL_PRINT,
        privacy_path=wording.PRIVACY_PATH,
        sms_terms_path=wording.SMS_TERMS_PATH,
        turnstile_site_key=turnstile.site_key(),
        offers=[
            PublicOffer(
                start=offer.start,
                join=offer.kind == "join",
                preferred=offer.preferred,
                places_left=offer.places_left,
            )
            for offer in found.offers
        ],
    )


# ---------------------------------------------------------------------------
# A booking
# ---------------------------------------------------------------------------


class BookingRequest(BaseModel):
    """The booking form's body. Fields are checked by ``_check_fields`` so every
    problem is answered at once, next to its field."""

    model_config = ConfigDict(extra="ignore")

    start: str = ""
    name: str = ""
    mobile: str = ""
    email: str = ""
    # Each box the person ticked, by key: "lease_start", "pets", "texts".
    confirmations: dict[str, StrictBool] = Field(default_factory=dict)
    confirmations_version: str | None = None
    turnstile_token: str | None = None
    # The ``?t=`` token from the link the lead agent sent.
    lead_token: str | None = None


class BookingView(BaseModel):
    """A booking as the confirmation screen shows it."""

    status: str  # "booked" or "cancelled"
    unit: str
    address: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    mobile: str | None  # all but the last two digits hidden
    corrections_left: int
    # The showing's start has passed, so it can no longer be cancelled or changed here.
    started: bool
    # Cancelled by the team, or because the unit went off the market, rather than by the person.
    cancelled_by_team: bool


class BookingResult(BaseModel):
    cancel_token: str
    # "join" when the person joined a showing already booked.
    kind: str
    # The confirmation text's status right after sending: "sent", "captured",
    # "pending" (Quo will be tried again), "unknown" or "skipped".
    confirmation: str | None
    booking: BookingView


def parse_body(model: type[BaseModel], raw: object) -> BaseModel:
    try:
        return model.model_validate(raw if isinstance(raw, dict) else {})
    except ValidationError:
        raise Refused(422, "invalid", "Some fields could not be read.") from None


@dataclass
class _Checked:
    start: datetime
    name: str
    mobile: str
    email: str
    confirmations: list[RequirementConfirmation] = field(default_factory=list)


def _parse_start(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
    return moment if moment.tzinfo is not None else None


def _check_fields(body: BookingRequest, listing: RentalListing, now: datetime) -> _Checked:
    problems: dict[str, str] = {}
    start = _parse_start(body.start)
    if start is None:
        problems["start"] = "Pick a time."
    name = " ".join(body.name.split())
    if not name:
        problems["name"] = "Enter your name."
    elif len(name) > 100:
        problems["name"] = "Enter a shorter name."
    mobile = normalize_us_mobile(body.mobile)
    if mobile is None:
        problems["mobile"] = "Enter a ten-digit US mobile number."
    email = normalize_email(body.email) if len(body.email) <= 254 else None
    if email is None:
        problems["email"] = "Enter one email address."
    confirmed: list[RequirementConfirmation] = []
    for item in wording.confirmations_for(listing):
        if body.confirmations.get(item.key) is not True:
            problems[f"confirmations.{item.key}"] = "Tick this box to book online."
        elif item.key != "texts":
            confirmed.append(
                RequirementConfirmation(
                    key=item.key, wording=item.wording, confirmed_at=now, version=item.version
                )
            )
    if problems:
        raise Refused(422, "invalid", "Check the marked fields.", problems)
    if body.confirmations_version is not None and body.confirmations_version != (
        _confirmations_version(wording.confirmations_for(listing))
    ):
        raise Refused(
            409,
            "confirmations_changed",
            "The details for this unit changed. Read the boxes again and tick each one.",
            {"confirmations": "The wording changed."},
        )
    return _Checked(start=start, name=name, mobile=mobile, email=email, confirmations=confirmed)


async def _check_turnstile(token: str | None, ip: str | None) -> None:
    try:
        ok = await turnstile.verify(token, ip)
    except turnstile.TurnstileUnavailable:
        logfire.error("showing bookings: TURNSTILE_SECRET_KEY is not set in production")
        raise Refused(503, "unavailable", "Booking is not available right now.") from None
    if not ok:
        raise Refused(
            403,
            "turnstile_failed",
            "We couldn't confirm you're not a bot. Try again.",
            {"turnstile": "Try the check again."},
        )


async def lock(session: AsyncSession, *keys: str) -> None:
    """Transaction-level advisory locks, always taken in the order given."""
    for key in keys:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )


async def _upcoming(
    session: AsyncSession, column, value: str, now: datetime, exclude: int | None = None
) -> int:
    query = (
        select(func.count())
        .select_from(ShowingAttendee)
        .join(Showing, Showing.id == ShowingAttendee.showing_id)
        .where(
            column == value,
            ShowingAttendee.status == "booked",
            Showing.status == "scheduled",
            Showing.starts_at > now,
        )
    )
    if exclude is not None:
        query = query.where(ShowingAttendee.id != exclude)
    return await session.scalar(query)


async def _check_limits(
    session: AsyncSession,
    limits: PublicLimits,
    *,
    mobile: str,
    email: str | None,
    ip: str | None,
    now: datetime,
    exclude: int | None = None,
) -> None:
    """The page's limits. ``exclude``: a booked place being completed, not counted."""
    if (
        await _upcoming(session, ShowingAttendee.mobile, mobile, now, exclude)
        >= limits.upcoming_per_mobile
    ):
        raise Refused(
            429,
            "limit_mobile",
            "This number already has the most upcoming showings we allow. "
            "Cancel one first, or call us.",
            {"mobile": "This number has too many upcoming showings."},
        )
    if email is not None and (
        await _upcoming(session, ShowingAttendee.email, email, now, exclude)
        >= limits.upcoming_per_email
    ):
        raise Refused(
            429,
            "limit_email",
            "This email already has the most upcoming showings we allow. "
            "Cancel one first, or call us.",
            {"email": "This email has too many upcoming showings."},
        )
    today = await session.scalar(
        select(func.count())
        .select_from(ShowingAttendee)
        .where(
            ShowingAttendee.source == "page",
            ShowingAttendee.created_at > now - timedelta(days=1),
        )
    )
    if today >= limits.bookings_per_day:
        logfire.warn(
            "showing bookings: the daily cap on page bookings was reached",
            bookings_per_day=limits.bookings_per_day,
        )
        raise Refused(429, "limit_site", SITE_LIMIT_MESSAGE)
    hashed = ip_hash(ip)
    if hashed is not None:
        recent = await session.scalar(
            select(func.count())
            .select_from(ShowingAttendee)
            .where(
                ShowingAttendee.client_ip_hash == hashed,
                ShowingAttendee.created_at > now - timedelta(days=1),
            )
        )
        if recent >= limits.bookings_per_ip_per_day:
            raise Refused(
                429,
                "limit_ip",
                "Too many bookings from this connection today. Try again tomorrow, or call us.",
            )


_RECHECK_MESSAGES = {
    "full": "That showing just filled up. Pick another time.",
}


def _time_gone(code: str) -> Refused:
    if code == "unknown_unit":
        return Refused(404, "unknown_unit", "This unit is not open for showings.")
    if code == "full":
        return Refused(409, "full", _RECHECK_MESSAGES["full"], {"start": "That showing is full."})
    return Refused(
        409,
        "time_gone",
        "That time is no longer open. Pick another time.",
        {"start": "That time is no longer open."},
    )


async def _showings_calendar(session: AsyncSession, agent: ShowingAgentRecord) -> str:
    rows = list(await session.scalars(select(CalendarConnection)))
    connection = agents.connection_for(agent, rows)
    if connection is not None and connection.showings_calendar_id:
        return connection.showings_calendar_id
    return NO_SHOWINGS_CALENDAR


def visit_of(showing: Showing, rules: ShowingRules) -> wording.Visit:
    return wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=rules.timezone,
    )


async def _enqueue_booking_notices(
    session: AsyncSession,
    *,
    showing: Showing,
    attendee: ShowingAttendee,
    host: ShowingAgentRecord,
    kind: str,
    rules: ShowingRules,
    now: datetime,
) -> ShowingNotice | None:
    """The confirmation text, and the host's event and the invite held behind it.

    Someone booked without texts (or with no mobile) gets no text, and nothing
    is held: the host's event, and the invite when there is an email, are due
    now. No email, no invite.
    """
    visit = visit_of(showing, rules)
    link = cancel_url(attendee.cancel_token)
    confirmation = None
    if attendee.texts_accepted and attendee.mobile:
        confirmation = await outbox.enqueue(
            session,
            kind=CONFIRMATION_KIND,
            channel="sms",
            showing_id=showing.id,
            attendee_id=attendee.id,
            recipient=attendee.mobile,
            recipient_key=f"attendee:{attendee.id}",
            payload=TextPayload(body=wording.confirmation_text(visit, link)),
            now=now,
        )
    held_by = confirmation.id if confirmation is not None else None
    calendar_id = await _showings_calendar(session, host)
    # As things stand now; the outbox renders it again when it is delivered.
    host_payload = await host_event.render(session, showing, calendar_id=calendar_id)
    await outbox.enqueue(
        session,
        kind=HOST_EVENT_KIND if kind == "new" else HOST_UPDATE_KIND,
        channel="calendar_event",
        showing_id=showing.id,
        attendee_id=attendee.id,
        recipient=host.email,
        recipient_key=(
            f"agent:{host.id}" if kind == "new" else f"agent:{host.id}:attendee:{attendee.id}"
        ),
        payload=host_payload,
        now=now,
        waits_for_notice_id=held_by,
    )
    if not attendee.email:
        return confirmation
    await outbox.enqueue(
        session,
        kind=INVITE_KIND,
        channel="calendar_invite",
        showing_id=showing.id,
        attendee_id=attendee.id,
        recipient=attendee.email,
        recipient_key=f"attendee:{attendee.id}",
        payload=CalendarPayload(
            operation="create",
            calendar_id=calendar_id,
            summary=wording.invite_summary(visit),
            description=wording.invite_description(link),
            location=showing.address,
            starts_at=showing.starts_at,
            ends_at=showing.ends_at,
            attendees=[attendee.email],
        ),
        now=now,
        waits_for_notice_id=held_by,
    )
    return confirmation


def view_of(
    showing: Showing, attendee: ShowingAttendee, timezone: str, now: datetime
) -> BookingView:
    booked = attendee.status == "booked" and showing.status == "scheduled"
    reason = attendee.cancel_reason if attendee.status == "cancelled" else showing.cancel_reason
    return BookingView(
        status="booked" if booked else "cancelled",
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=timezone,
        mobile=masked_mobile(attendee.mobile),
        corrections_left=max(0, MAX_MOBILE_CORRECTIONS - attendee.mobile_corrections),
        started=showing.starts_at <= now,
        cancelled_by_team=not booked and reason in ("team", "off_market"),
    )


async def book(
    unit: str,
    body: BookingRequest,
    facts: RequestFacts,
    *,
    now: datetime,
    texts: TextSender | None = None,
) -> BookingResult:
    """Book ``body.start`` at ``unit`` or join the showing already there. Raises ``Refused``."""
    async with AsyncSessionFactory() as session:
        listing, rules = await _listing(session, unit, now)
        settings = await settings_for_delivery(session)
    checked = _check_fields(body, listing, now)
    await _check_turnstile(body.turnstile_token, facts.ip)
    lead_token = (
        body.lead_token if body.lead_token and _LEAD_TOKEN.fullmatch(body.lead_token) else None
    )
    # A token from a lead agent's link ties the booking to its Zillow thread;
    # an unknown one is kept as it came and ties it to nothing.
    thread_id = await lead_links.thread_for_token(lead_token)

    async def limits(session: AsyncSession) -> None:
        await _check_limits(
            session,
            settings.public_limits,
            mobile=checked.mobile,
            email=checked.email,
            ip=facts.ip,
            now=now,
        )

    placed = await place_booking(
        listing,
        rules,
        checked.start,
        Person(
            name=checked.name,
            source="page",
            mobile=checked.mobile,
            email=checked.email,
            texts_accepted=True,
            consent_page=f"{public_base_url()}/showings/{listing.unit}",
            confirmations=tuple(checked.confirmations),
            lead_token=lead_token,
            zillow_thread_id=thread_id,
            user_agent=(facts.user_agent or None) and facts.user_agent[:500],
            signature_agent=(facts.signature_agent or None) and facts.signature_agent[:500],
            client_ip_hash=ip_hash(facts.ip),
        ),
        now=now,
        checks=limits,
    )
    showing, attendee = placed.showing, placed.attendee
    status = await _send_confirmation(placed.confirmation_id, now=now, texts=texts)
    if status == "refused":
        await withdraw(attendee.id, placed.confirmation_id, now=now)
        raise Refused(
            422, "number_refused", NUMBER_REFUSED, {"mobile": "We couldn't text this number."}
        )
    await deliver_host_text(placed.host_text_id, now=now, texts=texts)
    return BookingResult(
        cancel_token=attendee.cancel_token,
        kind=placed.kind,
        confirmation=status,
        booking=view_of(showing, attendee, rules.timezone, now),
    )


@dataclass(frozen=True)
class Person:
    """Who a booking is for, however it was made.

    The page always has a mobile, an email and consent to texts. The team may
    have a name alone, and the Zillow lead agent a name and a thread. A
    confirmation text is queued only with ``texts_accepted`` and a mobile;
    without one nothing is held behind it.
    """

    name: str
    source: str  # "page", "team" or "lead_agent"
    mobile: str | None = None
    email: str | None = None
    texts_accepted: bool = False
    # Where consent to texts was given: the page's address, or who on the team took it.
    consent_page: str | None = None
    confirmations: tuple[RequirementConfirmation, ...] = ()
    lead_token: str | None = None
    zillow_thread_id: str | None = None
    # The lead conversation whose reply books this place (source ``lead_agent``).
    lead_conversation_id: str | None = None
    user_agent: str | None = None
    signature_agent: str | None = None
    client_ip_hash: str | None = None


@dataclass
class Placed:
    showing: Showing
    attendee: ShowingAttendee
    kind: str  # "new" or "join"
    # The confirmation text, delivered by the caller at once; None without texts.
    confirmation_id: int | None
    # The residents' text this booking queued or brought back, if any.
    residents_notice_id: int | None = None
    # The host's text when the showing starts within 2 hours (``host_texts``),
    # delivered by the caller at once after the confirmation.
    host_text_id: int | None = None


async def place_booking(
    listing: RentalListing,
    rules: ShowingRules,
    start: datetime,
    person: Person,
    *,
    now: datetime,
    checks: Callable[[AsyncSession], Awaitable[None]] | None = None,
    hold_notices_until: datetime | None = None,
) -> Placed:
    """Book ``person`` at ``start``, or into the showing already there, in one transaction.

    Every booking goes through here: the page, the team and later the lead
    agent. In order: the shared booking lock and the locks on the unit and
    start, the mobile and the email; ``checks(session)`` when given (the
    page's limits); ``open_times.recheck`` with fresh calendars; the showing,
    new or joined and locked; the attendee; the notices. Raises ``Refused``.
    Nothing is sent here; the caller delivers the confirmation text.

    The unit's residents are looked up first, outside the transaction, as that
    may read Quo. A new showing queues their text, held behind the confirmation
    text, or due at once (in the daytime) when there is none; a join brings back
    an announcement stopped with an earlier failed confirmation the same way. A
    lookup that went wrong marks the showing and never stops the booking.

    ``hold_notices_until`` keeps this booking's notices that are due now (the
    host's event, the residents' text) from going out before that time. The
    lead agent books before its reply is sent and passes it, so a reply that
    then fails to send can cancel the booking before anything went out
    (``lead_bookings.py``); ``release_notices`` lets them go once it is sent.
    """
    texted = person.texts_accepted and person.mobile is not None
    # Read before the transaction, as it may reach Quo; used only for a new showing.
    found = await resident_notices.find_for(listing.unit, start, rules)
    keys = [BOOKING_LOCK, f"showing:{listing.unit.casefold()}:{start.astimezone(UTC).isoformat()}"]
    if person.mobile:
        keys.append(f"showing-mobile:{person.mobile}")
    if person.email:
        keys.append(f"showing-email:{person.email}")
    if person.zillow_thread_id:
        keys.append(f"showing-thread:{person.zillow_thread_id}")
    try:
        async with AsyncSessionFactory() as session, session.begin():
            await lock(session, *keys)
            if checks is not None:
                await checks(session)
            check = await open_times.recheck(listing.unit, start, now=now, session=session)
            if not check.ok:
                raise _time_gone(check.refusal or "time_gone")
            host = await session.get_one(ShowingAgentRecord, check.host_id)
            if check.kind == "join":
                showing = await session.scalar(
                    select(Showing).where(Showing.id == check.showing_id).with_for_update()
                )
                booked = await session.scalar(
                    select(func.count())
                    .select_from(ShowingAttendee)
                    .where(
                        ShowingAttendee.showing_id == showing.id,
                        ShowingAttendee.status == "booked",
                    )
                )
                if showing.status != "scheduled":
                    raise _time_gone("time_gone")
                if booked >= showing.capacity:
                    raise _time_gone("full")
                already = person.mobile and await session.scalar(
                    select(ShowingAttendee.id).where(
                        ShowingAttendee.showing_id == showing.id,
                        ShowingAttendee.status == "booked",
                        ShowingAttendee.mobile == person.mobile,
                    )
                )
                if already:
                    raise Refused(
                        409,
                        "already_booked",
                        "This number is already booked for that showing.",
                        {"mobile": "Already booked for this showing."},
                    )
                # One Zillow thread holds one place in a showing, whatever number it gives.
                if person.zillow_thread_id and await session.scalar(
                    select(ShowingAttendee.id).where(
                        ShowingAttendee.showing_id == showing.id,
                        ShowingAttendee.status == "booked",
                        ShowingAttendee.zillow_thread_id == person.zillow_thread_id,
                    )
                ):
                    raise Refused(409, "already_booked", "You're already booked for that showing.")
            else:
                showing = Showing(
                    unit=listing.unit,
                    address=listing.address,
                    starts_at=start,
                    ends_at=start + open_times.SLOT,
                    agent_id=host.id,
                )
                session.add(showing)
                await session.flush()
            attendee = ShowingAttendee(
                showing_id=showing.id,
                name=person.name,
                mobile=person.mobile,
                email=person.email,
                source=person.source,
                texts_accepted=texted,
                texts_consent_at=now if texted else None,
                texts_consent_version=wording.TEXTS_CONSENT_VERSION if texted else None,
                texts_consent_page=person.consent_page if texted else None,
                requirement_confirmations=[
                    item.model_dump(mode="json") for item in person.confirmations
                ],
                lead_token=person.lead_token,
                zillow_thread_id=person.zillow_thread_id,
                lead_conversation_id=person.lead_conversation_id,
                user_agent=person.user_agent,
                signature_agent=person.signature_agent,
                client_ip_hash=person.client_ip_hash,
                created_at=now,
            )
            session.add(attendee)
            await session.flush()
            confirmation = await _enqueue_booking_notices(
                session,
                showing=showing,
                attendee=attendee,
                host=host,
                kind=check.kind,
                rules=rules,
                now=now,
            )
            # No confirmation text (no texts, or no mobile): nothing to wait for.
            held_by = confirmation.id if confirmation is not None else None
            residents_notice_id = None
            if check.kind == "new":
                announced = await resident_notices.queue_new_showing(
                    session,
                    showing,
                    found,
                    rules=rules,
                    now=now,
                    waits_for_notice_id=held_by,
                )
                residents_notice_id = announced.id if announced is not None else None
            elif await resident_notices.bring_back(
                session, showing.id, waits_for_notice_id=held_by, now=now
            ):
                # An announcement stopped with an earlier confirmation that failed
                # waits for this one instead; one that went out is left as it is.
                residents_notice_id = await session.scalar(
                    select(ShowingNotice.id).where(
                        ShowingNotice.showing_id == showing.id,
                        ShowingNotice.kind == residents.NEW_SHOWING_KIND,
                    )
                )
            host_text = await host_texts.queue_join(
                session, showing, attendee, rules=rules, now=now
            )
            if hold_notices_until is not None:
                await _hold_until(
                    session,
                    _booking_notices(attendee.id, residents_notice_id),
                    hold_notices_until,
                )
    except IntegrityError:
        # Another booking took the host or the time between the re-check and the save.
        logfire.info("showing booking lost a race", unit=listing.unit)
        raise _time_gone("time_gone") from None

    logfire.info(
        "showing booked",
        unit=listing.unit,
        showing_id=showing.id,
        attendee_id=attendee.id,
        kind=check.kind,
        source=person.source,
        texts=texted,
        lead_token=person.lead_token is not None,
        signed=person.signature_agent is not None,
    )
    return Placed(
        showing=showing,
        attendee=attendee,
        kind=check.kind,
        confirmation_id=confirmation.id if confirmation is not None else None,
        residents_notice_id=residents_notice_id,
        host_text_id=host_text,
    )


def _booking_notices(attendee_id: int, residents_notice_id: int | None):
    """The notices one booking queued: its own, and the residents' text it queued or brought back."""
    condition = ShowingNotice.attendee_id == attendee_id
    if residents_notice_id is not None:
        condition = condition | (ShowingNotice.id == residents_notice_id)
    return condition


async def _hold_until(session: AsyncSession, condition, until: datetime) -> None:
    """Push the matching notices that are due and not being sent back to ``until``."""
    await session.execute(
        update(ShowingNotice)
        .where(
            condition,
            ShowingNotice.status == "pending",
            ShowingNotice.sending_started_at.is_(None),
        )
        .values(not_before=func.greatest(ShowingNotice.not_before, until)),
        execution_options={"synchronize_session": False},
    )


async def release_notices(
    attendee_id: int, residents_notice_id: int | None, *, now: datetime
) -> None:
    """Let a booking's notices held by ``hold_notices_until`` go now.

    Only those still waiting past ``now``; the daytime rule for the residents'
    text is applied when it is delivered, as always.
    """
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(
                _booking_notices(attendee_id, residents_notice_id),
                ShowingNotice.status == "pending",
                ShowingNotice.sending_started_at.is_(None),
                ShowingNotice.not_before > now,
            )
            .values(not_before=now),
            execution_options={"synchronize_session": False},
        )


async def deliver_host_text(
    notice_id: int | None, *, now: datetime, texts: TextSender | None
) -> None:
    """Deliver the host's text about a booking inside 2 hours at once, after the
    confirmation went (a withdrawn booking's is stopped when it is delivered)."""
    if notice_id is None:
        return
    try:
        await outbox.deliver(notice_id, now=now, texts=texts)
    except Exception:
        # The row stays pending or is saved as unknown; the job takes it from there.
        logfire.exception("showing host text could not be delivered now")


async def _send_confirmation(
    notice_id: int, *, now: datetime, texts: TextSender | None
) -> str | None:
    """Deliver a confirmation text now. "refused" when Quo refused the number."""
    status = await outbox.deliver(notice_id, now=now, texts=texts)
    if status == "failed":
        async with AsyncSessionFactory() as session:
            reason = await session.scalar(
                select(ShowingNotice.provider_status).where(ShowingNotice.id == notice_id)
            )
        if reason in holds.NUMBER_REFUSALS:
            return "refused"
    return status


async def withdraw(attendee_id: int, confirmation_id: int, *, now: datetime) -> None:
    """Withdraw a booking whose number Quo refused, in one transaction.

    It takes the shared booking lock first, as a cancellation does, so a
    cancellation of someone else in the showing cannot run in between.
    """
    from api.src.showing_bookings import cancelling  # cancelling imports this module

    async with AsyncSessionFactory() as session, session.begin():
        await lock(session, BOOKING_LOCK)
        attendee = await session.scalar(
            select(ShowingAttendee).where(ShowingAttendee.id == attendee_id).with_for_update()
        )
        attendee.status = "cancelled"
        attendee.cancel_reason = "number_refused"
        attendee.cancelled_at = now
        # The session does not flush on its own before the count below.
        await session.flush()
        await resident_notices.hand_over(session, confirmation_id, attendee_id)
        stopped = await holds.stop_waiting_on(
            session, confirmation_id, note=WITHDRAWN_NOTE, include_released=True
        )
        showing = await session.scalar(
            select(Showing).where(Showing.id == attendee.showing_id).with_for_update()
        )
        remaining = await session.scalar(
            select(func.count())
            .select_from(ShowingAttendee)
            .where(ShowingAttendee.showing_id == showing.id, ShowingAttendee.status == "booked")
        )
        update_queued = False
        if remaining == 0 and showing.status == "scheduled":
            showing.status = "cancelled"
            showing.cancel_reason = "no_attendees"
            showing.cancelled_at = now
            # Only when someone else left in between could the event be out already.
            await cancelling.queue_host_cancel(session, showing, now)
            await cancelling.queue_resident_cancellation(session, showing, now=now)
        elif showing.status == "scheduled":
            update_queued = await queue_host_update_after_leaving(
                session, showing=showing, attendee_id=attendee_id, now=now, why="withdrawn"
            )
    logfire.info(
        "showing booking withdrawn: number refused",
        attendee_id=attendee_id,
        showing_cancelled=remaining == 0,
        notices_stopped=stopped,
        host_update_queued=update_queued,
    )


async def queue_host_update_after_leaving(
    session: AsyncSession, *, showing: Showing, attendee_id: int, now: datetime, why: str
) -> bool:
    """Tell the host when the event may already list someone who left (``why``:
    "withdrawn" or "cancelled").

    That is when a host notice for the showing went out (sent or captured), may
    have gone out (unknown), or is being sent now. The update is due at once and
    renders at delivery, so it lists whoever is booked then; one queued while
    another is mid-send waits for it. When every host notice is still held or
    pending and not started, nothing is queued: they render at delivery.
    """
    out = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing.id,
            ShowingNotice.kind.in_(host_event.KINDS),
            host_event.since_last_move(showing.id),
            ShowingNotice.status.in_(["sent", "captured", "unknown"])
            | ((ShowingNotice.status == "pending") & ShowingNotice.sending_started_at.is_not(None)),
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    if out is None:
        return False
    calendar_id = out.payload.get("calendar_id") or NO_SHOWINGS_CALENDAR
    payload = await host_event.render(session, showing, calendar_id=calendar_id)
    if payload is None:
        return False
    await outbox.enqueue(
        session,
        kind=HOST_UPDATE_KIND,
        channel="calendar_event",
        showing_id=showing.id,
        attendee_id=attendee_id,
        recipient=out.recipient,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee_id}:{why}",
        payload=payload,
        now=now,
    )
    return True


# ---------------------------------------------------------------------------
# A booking read back, and a corrected mobile
# ---------------------------------------------------------------------------


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cancel_token: str = ""


class MobileCorrection(TokenRequest):
    mobile: str = ""


async def by_token(session: AsyncSession, token: str, *, lock: bool = False):
    if not token or len(token) > 100:
        raise Refused(404, "unknown_booking", "We couldn't find this booking.")
    query = (
        select(ShowingAttendee, Showing)
        .join(Showing, Showing.id == ShowingAttendee.showing_id)
        .where(ShowingAttendee.cancel_token == token)
    )
    if lock:
        query = query.with_for_update(of=ShowingAttendee)
    row = (await session.execute(query)).first()
    if row is None:
        raise Refused(404, "unknown_booking", "We couldn't find this booking.")
    return row


async def _check_new_mobile(
    session: AsyncSession, showing_id: int, mobile: str, *, limit: int, now: datetime
) -> None:
    """Refuse a number already in this showing or at its limit.

    The caller holds the number's advisory lock, taken at the start of its
    transaction, before any row lock.
    """
    taken = await session.scalar(
        select(ShowingAttendee.id).where(
            ShowingAttendee.showing_id == showing_id,
            ShowingAttendee.status == "booked",
            ShowingAttendee.mobile == mobile,
        )
    )
    if taken is not None:
        raise Refused(
            409,
            "already_booked",
            "This number is already booked for that showing.",
            {"mobile": "Already booked for this showing."},
        )
    if await _upcoming(session, ShowingAttendee.mobile, mobile, now) >= limit:
        raise Refused(
            429,
            "limit_mobile",
            "This number already has the most upcoming showings we allow.",
            {"mobile": "This number has too many upcoming showings."},
        )


async def _queue_host_update_after_correction(
    session: AsyncSession,
    *,
    showing_id: int,
    attendee_id: int,
    correction: int,
    confirmation_id: int,
    now: datetime,
) -> None:
    """When the host's event already went out, queue an update behind the new text.

    A host notice still held or pending needs nothing: it is rendered when delivered.
    """
    delivered = await session.scalar(
        select(ShowingNotice)
        .where(
            ShowingNotice.showing_id == showing_id,
            ShowingNotice.kind.in_(host_event.KINDS),
            host_event.since_last_move(showing_id),
            ShowingNotice.status.in_(["sent", "captured"]),
        )
        .order_by(ShowingNotice.id.desc())
        .limit(1)
    )
    if delivered is None:
        return
    showing = await session.get_one(Showing, showing_id)
    calendar_id = delivered.payload.get("calendar_id") or NO_SHOWINGS_CALENDAR
    payload = await host_event.render(session, showing, calendar_id=calendar_id)
    if payload is None:
        return
    await outbox.enqueue(
        session,
        kind=HOST_UPDATE_KIND,
        channel="calendar_event",
        showing_id=showing_id,
        attendee_id=attendee_id,
        recipient=delivered.recipient,
        recipient_key=f"agent:{showing.agent_id}:attendee:{attendee_id}:correction:{correction}",
        payload=payload,
        now=now,
        waits_for_notice_id=confirmation_id,
    )


async def booking_by_token(token: str, *, now: datetime) -> BookingView:
    async with AsyncSessionFactory() as session:
        attendee, showing = await by_token(session, token)
        rules = await listings_service.read_showing_rules(session)
    return view_of(showing, attendee, rules.timezone, now)


class _Superseded(Exception):
    """A newer correction of the same booking was made while this one's text was sent."""


async def correct_mobile(
    body: MobileCorrection, *, now: datetime, texts: TextSender | None = None
) -> BookingView:
    """Change the booking's mobile and text the confirmation to it, at most twice per booking.

    The new number takes over only once Quo has not refused it; the calendar
    notices still held, or stopped because the old number's text was not
    delivered, then wait for the new text instead.
    """
    mobile = normalize_us_mobile(body.mobile)
    async with AsyncSessionFactory() as session:
        settings = await settings_for_delivery(session)
        rules = await listings_service.read_showing_rules(session)
    async with AsyncSessionFactory() as session, session.begin():
        # Advisory locks before row locks, as in a booking.
        if mobile is not None:
            await lock(session, f"showing-mobile:{mobile}")
        attendee, showing = await by_token(session, body.cancel_token, lock=True)
        if attendee.status != "booked" or showing.status != "scheduled":
            raise Refused(409, "not_booked", "This booking is cancelled.")
        if showing.starts_at <= now:
            raise Refused(409, "started", "This showing has already started.")
        if attendee.mobile_corrections >= MAX_MOBILE_CORRECTIONS:
            raise Refused(
                429,
                "corrections_used",
                "This number can't be changed again here. Call us instead.",
                {"mobile": "No more changes here."},
            )
        if mobile is None:
            raise Refused(
                422,
                "invalid",
                "Check the marked fields.",
                {"mobile": "Enter a ten-digit US mobile number."},
            )
        if mobile == attendee.mobile:
            raise Refused(
                422,
                "same_number",
                "That is the number we texted.",
                {"mobile": "Enter a different number."},
            )
        limit = settings.public_limits.upcoming_per_mobile
        await _check_new_mobile(session, showing.id, mobile, limit=limit, now=now)
        attendee.mobile_corrections += 1
        visit = visit_of(showing, rules)
        confirmation = await outbox.enqueue(
            session,
            kind=CONFIRMATION_KIND,
            channel="sms",
            showing_id=showing.id,
            attendee_id=attendee.id,
            recipient=mobile,
            recipient_key=f"attendee:{attendee.id}:correction:{attendee.mobile_corrections}",
            payload=TextPayload(
                body=wording.confirmation_text(visit, cancel_url(attendee.cancel_token))
            ),
            now=now,
        )
        attendee_id = attendee.id
        corrections = attendee.mobile_corrections
        showing_id = showing.id
    status = await _send_confirmation(confirmation.id, now=now, texts=texts)
    if status == "refused":
        raise Refused(
            422, "number_refused", NUMBER_REFUSED, {"mobile": "We couldn't text this number."}
        )
    try:
        async with AsyncSessionFactory() as session, session.begin():
            # The number's advisory lock, then the attendee, then its notices.
            await lock(session, f"showing-mobile:{mobile}")
            attendee = await session.scalar(
                select(ShowingAttendee).where(ShowingAttendee.id == attendee_id).with_for_update()
            )
            if attendee.mobile_corrections != corrections or attendee.status != "booked":
                # A newer correction of this booking was made while this text was
                # being sent, so the person's latest choice stands; or the booking
                # was cancelled meanwhile, so nothing it held comes back. Change nothing.
                raise _Superseded
            # Another correction may have taken this number since the first check.
            await _check_new_mobile(session, showing_id, mobile, limit=limit, now=now)
            earlier = select(ShowingNotice.id).where(
                ShowingNotice.attendee_id == attendee_id,
                ShowingNotice.kind == CONFIRMATION_KIND,
                ShowingNotice.id != confirmation.id,
            )
            await session.execute(
                update(ShowingNotice)
                .where(
                    ShowingNotice.waits_for_notice_id.in_(earlier),
                    (ShowingNotice.status == "held")
                    | (
                        (ShowingNotice.status == "cancelled")
                        & (ShowingNotice.last_error != WITHDRAWN_NOTE)
                    ),
                )
                .values(status="held", waits_for_notice_id=confirmation.id, last_error=None),
                execution_options={"synchronize_session": False},
            )
            # The residents' announcement may have been handed to someone else
            # and stopped there: bring it back by showing, as a join does.
            await resident_notices.bring_back(
                session, showing_id, waits_for_notice_id=confirmation.id, now=now
            )
            attendee.mobile = mobile
            if attendee.attention_reason and attendee.attention_reason.startswith("confirmation_"):
                attendee.attention_reason = None
            await session.flush()
            await _queue_host_update_after_correction(
                session,
                showing_id=showing_id,
                attendee_id=attendee_id,
                correction=corrections,
                confirmation_id=confirmation.id,
                now=now,
            )
    except IntegrityError:
        raise Refused(
            409,
            "already_booked",
            "This number is already booked for that showing.",
            {"mobile": "Already booked for this showing."},
        ) from None
    except _Superseded:
        logfire.info("showing booking mobile correction superseded", attendee_id=attendee_id)
        return await booking_by_token(body.cancel_token, now=now)
    logfire.info("showing booking mobile corrected", attendee_id=attendee_id)
    return await booking_by_token(body.cancel_token, now=now)


# ---------------------------------------------------------------------------
# The team's number, after Turnstile
# ---------------------------------------------------------------------------


class TurnstileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turnstile_token: str | None = None


class TeamPhone(BaseModel):
    phone: str  # E.164
    display: str  # 412-555-0100
    extension: str
    # Dials the number, pauses after connecting, then the extension.
    tel_href: str


async def team_phone_after_check(body: TurnstileRequest, facts: RequestFacts) -> TeamPhone:
    await _check_turnstile(body.turnstile_token, facts.ip)
    try:
        phone = team_phone()
    except PhoneConfigError:
        logfire.exception("showing bookings: team phone is not configured")
        raise Refused(503, "unavailable", "The phone number is not available right now.") from None
    extension = team_extension()
    return TeamPhone(
        phone=phone,
        display=us_phone_display(phone),
        extension=extension,
        tel_href=f"tel:{phone},{extension}",
    )
