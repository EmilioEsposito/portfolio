"""Tools the general agent uses to read, book and cancel showings.

The booking service (``api/src/showing_bookings``) holds the tour times: who
can host, when, and the showings booked. The Booking settings tab, the public
booking page and the Zillow lead agent's replies all book through it. These
tools make the general agent one more caller of the same code the tab uses,
so the same checks run and the same texts and calendar changes follow:

- ``showings_read`` reads what the tab's Showings section shows: each unit's
  open times, each unit's booking link (or why it has none), the showings
  booked and who is in them. With a Zillow thread id it also gives that
  thread's own booking links and showings, as the lead agent's case shows them
  (``showing_bookings/lead_tours.py``). No approval.
- ``showing_book`` books a person into one of a unit's open times through
  ``team_actions.book_for_caller``, the function behind the tab's "Add a
  booking" form, which books through ``bookings.place_booking``.
- ``showing_cancel`` cancels a whole showing (``cancelling.cancel_showing``)
  or removes one person (``cancelling.remove_attendee``), as the tab does.

Booking and cancelling queue real messages: the host's calendar event, the
residents' text, the prospect's confirmation text and invite, cancellation
texts. A person approves every call that books or cancels, and the card they
approve says what the click does. That is the pattern of
``rental_listing_update``'s ``off_market_effect``: the first call changes
nothing and answers with the effect in plain words; the agent calls again
with that exact text as ``effect``, which the card shows, and a person
approves. If the effect differs by the time it is approved (someone else
booked the time, a person left), nothing is done and the agent asks again.

There is no move tool: a move is a booking at the new time, then a removal
from the old one.

The docstrings and ``GUIDE`` below are read by the model: they are prompts.
"""

import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset
from sqlalchemy import func, select

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools._logging import ErrorLoggingToolset
from api.src.sernia_ai.tools.rental_listing_tools import _UnlessDisabled
from api.src.showing_bookings import (
    bookings,
    cancelling,
    host_texts,
    lead_tours,
    open_times,
    team_actions,
    wording,
)
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.models import Showing, ShowingAgentRecord, ShowingAttendee
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
    current_environment,
    public_booking_on,
    read_settings,
    unit_is_live,
)
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.models import ZillowEmailJob, ZillowLeadLink

CAPABILITY_ID = "showing-bookings"

DESCRIPTION = (
    "Tour times and showings, from the booking service: the times each rental "
    "unit can be booked for, each unit's booking link (and a Zillow lead "
    "thread's own link), the showings booked and who is in them; and booking or "
    "cancelling a showing for someone, each with a person's approval. Tour times "
    "and booking links are not in the listing. Load it whenever a lead or a "
    "person asks about tours, showings, availability or a booking link, and "
    "before asking anyone on the team when they are free."
)

# Read by the general agent when it loads the capability.
GUIDE = """\
# Showings

The booking service holds tour times and booked showings. The rental
listing does not: it has no tour times and no booking links. Use
`showings_read` for both, before anything else about a tour.

Where availability comes from: each showing agent's hours and calendars,
which a person sets on the Booking settings tab. These tools cannot change
them. `showings_read` turns them into the open times of each unit. Do not
text or email the team to ask when someone is free: read `showings_read`.
If it shows no open times, say why (it gives the reason) and let the person
in the chat decide.

Answering a lead about a tour:

- Give the lead the unit's booking link, where they pick a time themselves.
  Do not list times for them to choose from, and do not ask a teammate when
  they are free.
- For a Zillow lead, pass the thread's id (`zillow_thread_id`) to
  `showings_read`. It returns the thread's own link for each unit, which ties
  the lead's booking to their thread so the lead agent sees it as theirs, and
  the showings the thread already has. Give the lead that link, not the plain
  one. Reading a thread's link saves its token the first time; that is all
  it changes.
- When a unit has no link, `showings_read` says why (public booking is off,
  the unit is not open for bookings, it is off the market). Tell the person
  in the chat; do not make up a link or a time.

Booking or cancelling for someone, only when a person in the chat asks:

- `showing_book` books a person into one of a unit's open times, as listed
  by `showings_read` (pass `start` exactly as listed). Pass
  `zillow_thread_id` when the person is a Zillow lead, so the booking is the
  thread's own.
- `showing_cancel` cancels a whole showing, or removes one person from it
  with `attendee_id`. Ids come from `showings_read`.
- Each one first books or cancels nothing: it answers with what it would do
  and who would be told, in one paragraph. Call it again with the same
  arguments and that exact paragraph as `effect`; a person approves it on
  the card. If things changed meanwhile, it answers with the new paragraph:
  call again with that one. Tell the person what was done afterwards.
- Set `texts_accepted` only when the person in the chat says the prospect
  agreed to texts about the showing. Never decide it from an email, a text
  thread or anything the prospect wrote. Without it the prospect gets no
  confirmation text, no reminder by text and no cancellation text.
- There is no move tool. To move someone, book the new time first, then
  remove them from the old showing. They get a confirmation of the new time
  and the team's cancellation of the old one; say so to the person first.

What is sent depends on the send mode, which `showings_read` reports. In
capture mode nothing goes out until a person sends it from Captured messages
on the Booking settings tab.
"""

# How many open times the read lists for each unit: a few for every unit, more for one.
TIMES_FOR_EACH_UNIT = 8
TIMES_FOR_ONE_UNIT = 60

showing_toolset = FunctionToolset[SerniaDeps]()


def clock() -> datetime:
    """The time the tools act at. Tests replace it."""
    return datetime.now(UTC)


def text_sender() -> TextSender | None:
    """None sends through Quo, behind the outbox's guard. Tests replace it with a stand-in."""
    return None


def _who(ctx: RunContext[SerniaDeps]) -> str:
    """Who took the booking, for the consent record: the person and the route."""
    person = (ctx.deps.user_email or "").strip() or ctx.deps.user_name
    return f"{person} (via Sernia AI)"


def _when(start: datetime, timezone: str) -> str:
    """ "Wed, Apr 14 at 4:00 PM", in the showing rules' time zone."""
    return wording.when(
        wording.Visit(unit="", address="", starts_at=start, ends_at=start, timezone=timezone)
    )


_NO_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _place(unit: str, address: str) -> str:
    """ "12 Example St #A (unit 12-A)", as the texts name a unit."""
    return wording.place(
        wording.Visit(
            unit=unit, address=address, starts_at=_NO_TIME, ends_at=_NO_TIME, timezone="UTC"
        )
    )


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _sending(settings: ShowingBookingSettings, unit: str | None = None) -> str:
    """What the send mode does with what a booking or cancellation queues, in a
    sentence, in the order ``guard.decide`` asks."""
    later = "until a person sends it from Captured messages on the Booking settings tab"
    if settings.send_mode == "capture":
        return f"Bookings are in capture mode: nothing is sent {later}."
    if current_environment() != "production":
        if settings.send_mode == "test":
            return (
                "This is not production and bookings are in test mode: only people on the "
                f"test list are sent anything; the rest is captured {later}."
            )
        return f"This is not production: nothing is sent {later}."
    if settings.send_mode == "test":
        return (
            "Bookings are in test mode: only people on the test list are sent anything; the "
            f"rest is captured {later}."
        )
    if unit is None:
        return "Bookings are live: texts and calendar changes go out for the units ticked live."
    if not unit_is_live(settings, unit):
        return f"{unit} is not live: nothing about it is sent {later}."
    return "Bookings are live for this unit: these go out."


async def _known_thread(thread_id: str) -> bool:
    """Whether a Zillow lead thread has this id: the lead agent has handled it."""
    async with AsyncSessionFactory() as session:
        found = await session.scalar(
            select(ZillowEmailJob.thread_id).where(ZillowEmailJob.thread_id == thread_id).limit(1)
        ) or await session.scalar(
            select(ZillowLeadLink.thread_id).where(ZillowLeadLink.thread_id == thread_id)
        )
    return found is not None


UNKNOWN_THREAD = (
    "No Zillow lead thread with id {thread!r} has reached the lead agent. Use the Gmail "
    "thread id of the lead's Zillow email, or leave zillow_thread_id empty."
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _no_link_because(
    settings: ShowingBookingSettings, listing: RentalListing, today: date, has_place: bool
) -> str | None:
    """Why a unit's booking page does not answer, or None when it does."""
    if not open_times.on_offer(listing, today):
        return f"{listing.unit} is off the market from {listing.off_market_on}, so it has no booking page."
    if lead_tours._page_answers(settings, listing.unit, has_place):
        return None
    if not public_booking_on(settings, current_environment()):
        return (
            "Public booking is switched off on the Booking settings tab, so no booking "
            "page answers. Someone on the team can still book with showing_book, or switch "
            "public booking on."
        )
    return (
        f"Bookings are in live mode and {listing.unit} is not ticked live on the Booking "
        "settings tab, so its booking page is closed. Someone on the team can still book "
        "with showing_book."
    )


def _offer_view(offer: open_times.Offer, timezone: str) -> dict[str, Any]:
    view: dict[str, Any] = {
        "start": offer.start.isoformat(),
        "when": _when(offer.start, timezone),
        "kind": "joins a showing already booked" if offer.kind == "join" else "new showing",
        "places_left": offer.places_left,
        "host": offer.host_name,
    }
    if offer.preferred:
        view["preferred"] = True
    return view


async def _booked(
    now: datetime, timezone: str, unit: str | None, thread_id: str | None
) -> list[dict[str, Any]]:
    """The scheduled showings that have not started, in the next 14 days, with who is in them."""
    async with AsyncSessionFactory() as session:
        query = (
            select(Showing, ShowingAgentRecord.name)
            .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
            .where(
                Showing.status == "scheduled",
                Showing.starts_at > now,
                Showing.starts_at <= now + open_times.HORIZON,
            )
            .order_by(Showing.starts_at, Showing.unit)
        )
        if unit is not None:
            query = query.where(func.lower(Showing.unit) == unit.strip().casefold())
        showings = (await session.execute(query)).all()
        people: dict[int, list[ShowingAttendee]] = {}
        if showings:
            for person in await session.scalars(
                select(ShowingAttendee)
                .where(
                    ShowingAttendee.showing_id.in_([showing.id for showing, _ in showings]),
                    ShowingAttendee.status == "booked",
                )
                .order_by(ShowingAttendee.id)
            ):
                people.setdefault(person.showing_id, []).append(person)
    booked = []
    for showing, host in showings:
        rows = []
        for person in people.get(showing.id, []):
            row: dict[str, Any] = {
                "attendee_id": person.id,
                "name": person.name,
                "mobile": wording.display_phone(person.mobile),
                "email": person.email or "no email",
                "takes_texts": person.texts_accepted,
                "booked_by": {
                    "page": "the booking page",
                    "team": "the team",
                    "lead_agent": "the lead agent, by email",
                }.get(person.source, person.source),
            }
            if person.attention_reason:
                row["needs_a_call"] = person.attention_reason
            if thread_id is not None and person.zillow_thread_id == thread_id:
                row["this_thread"] = True
            rows.append(row)
        booked.append(
            {
                "showing_id": showing.id,
                "unit": showing.unit,
                "start": showing.starts_at.isoformat(),
                "when": _when(showing.starts_at, timezone),
                "host": host,
                "capacity": showing.capacity,
                "people": rows,
            }
        )
    return booked


async def _thread_view(
    thread_id: str,
    on_offer: list[RentalListing],
    rules: ShowingRules,
    settings: ShowingBookingSettings,
    now: datetime,
) -> dict[str, Any]:
    """The thread's own links and showings, as the lead agent's case shows them."""
    if not await _known_thread(thread_id):
        return {"zillow_thread_id": thread_id, "problem": UNKNOWN_THREAD.format(thread=thread_id)}
    # Asked as the lead agent asks with the booking service in use, whatever its
    # switch says: the link and the thread's showings do not depend on it.
    found = await lead_tours.lead_booking_for(
        lead_tours.BOOKING_TOUR_SOURCE, thread_id, as_listing_data(on_offer, rules), now
    )
    if found is None:
        return {
            "zillow_thread_id": thread_id,
            "problem": "The thread's link and showings could not be read just now. Try again.",
        }
    booked_at = {own.unit.casefold() for own in found.own_showings if own.status == "booked"}
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    links: dict[str, Any] = {}
    for listing in on_offer:
        link = found.booking_links.get(listing.unit)
        links[listing.unit] = link or {
            "no_link_because": _no_link_because(
                settings, listing, today, listing.unit.casefold() in booked_at
            )
        }
    view: dict[str, Any] = {
        "zillow_thread_id": thread_id,
        "booking_links": links,
        "own_showings": [
            {
                "unit": own.unit,
                "when": _when(own.start, rules.timezone),
                "status": own.status,
                **({"cancelled_by": own.cancelled_by} if own.cancelled_by else {}),
            }
            for own in found.own_showings
        ],
    }
    if not settings.lead_agent_uses_open_times:
        view["note"] = (
            "The lead agent's switch to use the booking service is off, so the lead agent "
            "itself offers no times or links and does not see the thread's showings. The "
            "link still works and ties a booking to this thread."
        )
    return view


@showing_toolset.tool
async def showings_read(
    ctx: RunContext[SerniaDeps], unit: str | None = None, zillow_thread_id: str | None = None
) -> str:
    """Read tour times and showings from the booking service. Changes nothing but a thread's token.

    Returns, as JSON: the send mode and who can book; for each unit on offer
    (or just `unit`), its booking link or why it has none, and its open times
    (start, when in local words, a new showing or joining one, places left,
    host), a few per unit, more when you name one unit; the showings booked in
    the next 14 days that have not started, with each person's attendee_id,
    name, mobile, email and whether they take texts; and how each showing
    agent's calendar was read.

    Tour times and booking links come only from here, not from the listing.
    Read this before telling anyone about a tour, and before asking the team
    when someone is free.

    Args:
        unit: One unit, e.g. "12-A", for all of its open times. Leave empty for every unit on offer.
        zillow_thread_id: The Gmail thread id of a Zillow lead's email. Adds that thread's own booking link for each unit, which ties a booking to the thread, and the showings the thread has. Reading it saves the thread's link token the first time.
    """
    now = clock()
    try:
        found = await open_times.open_times(
            now=now, units=[unit.strip()] if unit and unit.strip() else None
        )
        async with AsyncSessionFactory() as session:
            settings = await read_settings(session)
            listings = await listings_service.read_listings(session)
            rules = await listings_service.read_showing_rules(session)
    except RentalDataInvalid as error:
        return (
            f"The rental listing data fails its checks: {error}. Nothing can be booked until "
            "it is fixed. Tell the person."
        )
    if unit and unit.strip() and not found.units:
        names = ", ".join(item.unit for item in listings) or "none"
        return f"No listing for a unit named {unit!r} is on file (on file: {names})."
    by_unit = {listing.unit.casefold(): listing for listing in listings}
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    limit = TIMES_FOR_ONE_UNIT if unit else TIMES_FOR_EACH_UNIT
    units = []
    for item in found.units:
        listing = by_unit[item.unit.casefold()]
        why = _no_link_because(settings, listing, today, has_place=False)
        view: dict[str, Any] = {
            "unit": item.unit,
            "address": item.address,
            "booking_link": None if why else bookings.unit_url(listing.unit),
        }
        if why:
            view["no_link_because"] = why
        view["open_times"] = [_offer_view(offer, found.timezone) for offer in item.offers[:limit]]
        if len(item.offers) > limit:
            view["more_open_times"] = len(item.offers) - limit
        if item.why_none:
            view["no_open_times_because"] = item.why_none
        units.append(view)
    document: dict[str, Any] = {
        "now": _when(now, found.timezone),
        "timezone": found.timezone,
        "sending": _sending(settings),
        "public_booking": public_booking_on(settings, current_environment()),
        "lead_agent_uses_booking_service": settings.lead_agent_uses_open_times,
        "units": units,
        "booked": await _booked(now, found.timezone, unit, zillow_thread_id),
        "agent_calendars": [
            {
                "name": agent.name,
                "read": agent.status,
                **({"problem": agent.problem} if agent.problem else {}),
            }
            for agent in found.agents
        ],
    }
    if found.sample_calendar_in_use:
        document["sample_calendar"] = (
            "Outside production, agents with no calendar connected use a made-up week."
        )
    if not found.agents:
        document["no_agents"] = (
            "No showing agent is set up, so nothing can be booked. A person adds one on the "
            "Booking settings tab."
        )
    if zillow_thread_id and zillow_thread_id.strip():
        on_offer = [listing for listing in listings if open_times.on_offer(listing, today)]
        document["thread"] = await _thread_view(
            zillow_thread_id.strip(), on_offer, rules, settings, now
        )
    return json.dumps(document, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def _refusal(prefix: str, refused: Refused) -> str:
    fields = "; ".join(f"{name}: {line}" for name, line in refused.fields.items())
    return f"{prefix} {refused.message}" + (f" ({fields})" if fields else "")


def _book_effect(
    *,
    name: str,
    listing: RentalListing,
    start: datetime,
    kind: str,
    host: str,
    mobile: str | None,
    email: str | None,
    texts: bool,
    thread_id: str | None,
    settings: ShowingBookingSettings,
    rules: ShowingRules,
    now: datetime,
) -> str:
    """What booking would do, said before anyone approves it."""
    where = _place(listing.unit, listing.address)
    when = _when(start, rules.timezone)
    if kind == "join":
        lines = [
            f"Books {name} into the showing of {where} already booked on {when}, hosted by {host}."
        ]
        host_told = f"{host}'s calendar event is updated"
    else:
        lines = [f"Books {name} into a new showing of {where} on {when}, hosted by {host}."]
        host_told = f"{host} gets a calendar event"
    if now < start <= now + host_texts.LATE:
        host_told += " and a text now, as it starts within 2 hours"
    told = [host_told]
    if kind != "join":
        told.append("the unit's residents get a text about the showing, if anyone lives there")
    prospect = []
    if texts and mobile:
        prospect.append(f"a confirmation text at {wording.display_phone(mobile)}")
    if email:
        prospect.append(f"a calendar invite at {email}")
    told.append(f"{name} gets {' and '.join(prospect)}" if prospect else f"{name} is sent nothing")
    lines.append(f"{'; '.join(told)}.")
    if not (texts and mobile):
        lines.append(f"{name} takes no texts: no confirmation, reminder or cancellation by text.")
    if thread_id:
        lines.append("It is tied to the lead's Zillow thread, as the thread's own showing.")
    lines.append(_sending(settings, listing.unit))
    return " ".join(lines)


@showing_toolset.tool
async def showing_book(
    ctx: RunContext[SerniaDeps],
    unit: str,
    start: str,
    name: str,
    mobile: str = "",
    email: str = "",
    texts_accepted: bool = False,
    zillow_thread_id: str | None = None,
    effect: str | None = None,
) -> str:
    """Book a person into one of a unit's open times, as the Booking settings tab's "Add a booking" does.

    Books only what a person in the chat asked for. The first call books
    nothing: it answers with what the booking would do and who would be told
    (the host's calendar, the unit's residents, the prospect). Call again
    with the same arguments and that exact answer as effect; a person
    approves it before anything is booked. If the answer changed meanwhile,
    call again with the new one.

    Args:
        unit: The unit as showings_read names it, e.g. "12-A".
        start: One of the unit's open times, exactly as showings_read lists it, e.g. "2027-04-14T16:00:00-04:00".
        name: The prospect's name.
        mobile: The prospect's US mobile number, if the person in the chat gave it. Leave empty otherwise.
        email: The prospect's email address, if the person in the chat gave it. They get a calendar invite there. Leave empty otherwise.
        texts_accepted: True only when the person in the chat says the prospect agreed to texts about the showing. Never decide it yourself from an email, a text thread or what the prospect wrote. Needs a mobile. Without it the prospect gets no texts at all.
        zillow_thread_id: The Gmail thread id of the lead's Zillow email, when the prospect is a Zillow lead, so the lead agent sees the booking as the thread's own showing. Leave empty otherwise.
        effect: Leave empty at first. Then the exact answer of the first call, so the person approving sees it.
    """
    now = clock()
    thread_id = zillow_thread_id.strip() if zillow_thread_id and zillow_thread_id.strip() else None
    request = team_actions.TeamBookingRequest(
        unit=unit, start=start, name=name, mobile=mobile, email=email, texts=texts_accepted
    )
    try:
        parsed, clean_name, clean_mobile, clean_email = team_actions._check_fields(request)
        async with AsyncSessionFactory() as session:
            listing, rules = await bookings._listing(session, unit, now, only_open=False)
            settings = await read_settings(session)
    except Refused as refused:
        return _refusal("Not booked.", refused)
    if thread_id is not None and not await _known_thread(thread_id):
        return "Not booked. " + UNKNOWN_THREAD.format(thread=thread_id)
    try:
        offers = await open_times.offers_for_unit(listing.unit, now=now)
    except RentalDataInvalid as error:
        return f"Not booked. The rental listing data fails its checks: {error}."
    offer = next((item for item in offers.offers if item.start == parsed), None)
    if offer is None:
        check = await open_times.recheck(listing.unit, parsed, now=now)
        if not check.ok:
            return (
                f"Not booked. {_when(parsed, rules.timezone)} cannot be booked for "
                f"{listing.unit}: {check.message} Call showings_read for its open times."
            )
        kind, host = check.kind or "new", check.host_name or "a showing agent"
    else:
        kind, host = offer.kind, offer.host_name
    sentence = _book_effect(
        name=clean_name,
        listing=listing,
        start=parsed,
        kind=kind,
        host=host,
        mobile=clean_mobile,
        email=clean_email,
        texts=texts_accepted,
        thread_id=thread_id,
        settings=settings,
        rules=rules,
        now=now,
    )
    if effect != sentence:
        changed = " What it would do changed since it was asked." if effect else ""
        return (
            f"Not booked yet.{changed} {sentence} A person must approve that. Call "
            f"showing_book again with the same arguments and effect={sentence!r}."
        )
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"effect": sentence})
    try:
        booked = await team_actions.book_for_caller(
            request,
            taken_by=_who(ctx),
            now=now,
            texts=text_sender(),
            zillow_thread_id=thread_id,
        )
    except Refused as refused:
        return _refusal("Not booked.", refused)
    queued = ", ".join(f"{notice.kind} ({notice.status})" for notice in booked.notices)
    confirmation = (
        f" The confirmation text was {booked.confirmation}." if booked.confirmation else ""
    )
    return (
        f"Booked {clean_name} at {listing.unit} on {_when(parsed, rules.timezone)} "
        f"({'joined a showing already booked' if booked.kind == 'join' else 'a new showing'}). "
        f"showing_id {booked.showing_id}, attendee_id {booked.attendee_id}.{confirmation} "
        f"Queued: {queued or 'nothing'}."
    )


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


def _cancel_effect(
    showing: Showing,
    host: str,
    booked: list[ShowingAttendee],
    target: ShowingAttendee | None,
    settings: ShowingBookingSettings,
    rules: ShowingRules,
    now: datetime,
) -> str:
    """What cancelling would do, said before anyone approves it."""
    where = _place(showing.unit, showing.address)
    when = _when(showing.starts_at, rules.timezone)
    late = host_texts.is_late(showing, now)
    residents = "the unit's residents are texted that it is off, if they were told of it"
    if target is None or len(booked) == 1:
        names = [person.name for person in booked]
        if target is None:
            lines = [
                f"Cancels the showing of {where} on {when}, hosted by {host}, and everyone "
                f"booked in it: {_names(names) or 'no one'}."
            ]
        else:
            lines = [
                f"Removes {target.name} from the showing of {where} on {when}, hosted by "
                f"{host}. They are the last one booked, so the showing is cancelled too."
            ]
        texted = [person.name for person in booked if cancelling.will_text(person)]
        lines.append(
            f"Texted that the team cancelled: {_names(texted)}."
            if texted
            else "No one is texted: none of them takes texts, or their numbers are known to be wrong."
        )
        host_told = f"{host}'s calendar event is cancelled" + (
            " and they are texted now, as it starts within 2 hours" if late else ""
        )
        lines.append(
            f"Any calendar invites they were sent are withdrawn; {host_told}; {residents}."
        )
    else:
        others = [person.name for person in booked if person.id != target.id]
        lines = [f"Removes {target.name} from the showing of {where} on {when}, hosted by {host}."]
        lines.append(
            f"{target.name} is texted that the team cancelled their place."
            if cancelling.will_text(target)
            else f"{target.name} is not texted: they take no texts, or their number is known to be wrong."
        )
        host_told = f"{host}'s calendar event is updated" + (
            " and they are texted now, as it starts within 2 hours" if late else ""
        )
        lines.append(
            f"Any calendar invite they were sent is withdrawn; {host_told}; "
            f"{_names(others)} {'stays' if len(others) == 1 else 'stay'} booked."
        )
    lines.append(_sending(settings, showing.unit))
    return " ".join(lines)


@showing_toolset.tool
async def showing_cancel(
    ctx: RunContext[SerniaDeps],
    showing_id: int,
    attendee_id: int | None = None,
    effect: str | None = None,
) -> str:
    """Cancel a whole showing, or remove one person from it, as the Booking settings tab does.

    Cancels only what a person in the chat asked for. The first call cancels
    nothing: it answers with what would be cancelled and who would be told.
    Call again with the same arguments and that exact answer as effect; a
    person approves it before anything is cancelled. If the answer changed
    meanwhile, call again with the new one.

    Args:
        showing_id: The showing's id, from showings_read.
        attendee_id: One person's attendee_id in that showing, from showings_read, to remove only them. Leave empty to cancel the whole showing and everyone in it.
        effect: Leave empty at first. Then the exact answer of the first call, so the person approving sees it.
    """
    now = clock()
    async with AsyncSessionFactory() as session:
        found = (
            await session.execute(
                select(Showing, ShowingAgentRecord.name)
                .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
                .where(Showing.id == showing_id)
            )
        ).first()
        if found is None:
            return f"No showing with id {showing_id}. Call showings_read for the ids."
        showing, host = found
        booked = list(
            await session.scalars(
                select(ShowingAttendee)
                .where(ShowingAttendee.showing_id == showing_id, ShowingAttendee.status == "booked")
                .order_by(ShowingAttendee.id)
            )
        )
        target = None
        if attendee_id is not None:
            target = await session.scalar(
                select(ShowingAttendee).where(
                    ShowingAttendee.id == attendee_id, ShowingAttendee.showing_id == showing_id
                )
            )
            if target is None:
                return (
                    f"No person with attendee_id {attendee_id} in showing {showing_id}. Call "
                    "showings_read for the ids."
                )
        settings = await read_settings(session)
        rules = await listings_service.read_showing_rules(session)
    if showing.status != "scheduled" or not booked:
        return f"Nothing to cancel: showing {showing_id} is already cancelled."
    if target is not None and target.status != "booked":
        return f"Nothing to cancel: {target.name}'s place is already cancelled."
    if showing.starts_at <= now:
        return f"Not cancelled. Showing {showing_id} has started; it can no longer be cancelled."
    sentence = _cancel_effect(showing, host, booked, target, settings, rules, now)
    if effect != sentence:
        changed = " What it would do changed since it was asked." if effect else ""
        return (
            f"Not cancelled yet.{changed} {sentence} A person must approve that. Call "
            f"showing_cancel again with the same arguments and effect={sentence!r}."
        )
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"effect": sentence})
    try:
        if attendee_id is None:
            done = await cancelling.cancel_showing(showing_id, now=now, texts=text_sender())
        else:
            done = await cancelling.remove_attendee(
                showing_id, attendee_id, now=now, texts=text_sender()
            )
    except cancelling.NotFound:
        return f"Not cancelled. Showing {showing_id} or that person no longer exists."
    except cancelling.Started:
        return f"Not cancelled. Showing {showing_id} has started; it can no longer be cancelled."
    if not done.attendees_cancelled:
        return "Nothing was left to cancel: someone cancelled it meanwhile."
    return (
        f"Cancelled. Showing {showing_id} is now {done.showing_status}; "
        f"{done.attendees_cancelled} {'person' if done.attendees_cancelled == 1 else 'people'} "
        f"cancelled, {done.texts_queued} cancellation "
        f"{'text' if done.texts_queued == 1 else 'texts'} queued."
    )


class ShowingTools(AbstractCapability[SerniaDeps]):
    """The showing tools and their guide, offered to the model to load when needed."""

    def __init__(self) -> None:
        self.id = CAPABILITY_ID
        self.description = DESCRIPTION
        self.defer_loading = True
        self._toolset = ErrorLoggingToolset(_UnlessDisabled(showing_toolset), name="showings")

    def get_instructions(self) -> str:
        return GUIDE

    def get_toolset(self) -> AbstractToolset[SerniaDeps]:
        return self._toolset


def showing_tools() -> ShowingTools:
    return ShowingTools()
