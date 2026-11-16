"""The team's API for showing bookings, under ``/api/showing-bookings``.

Signed-in Sernia users only, on every route.

- ``GET /settings`` and ``PATCH /settings``: the send mode, live units and the
  test-send switch. A save sends ``based_on``, the settings as the caller last
  read them; if they have changed since, nothing is saved and the answer is 409.
- ``GET /notices``: notices newest first, with their wording, status and mode.
- ``POST /notices/send-pending``: release or stop the held notices, then run
  the sending step once: the same two steps the job every minute runs
  (``holds.send_pending``). Answers what it did.
- ``GET /calendar/connections``: each connected Google account, whether it can
  be used here, and whether this environment can connect calendars at all.
- ``POST /calendar/connect``: Google's consent URL for a new connection, or to
  reconnect an account. The browser comes back through
  ``calendar_callback.py``, the module's one route without sign-in.
- ``POST /calendar/connections/{id}/check``: read the account's own free/busy
  once and save the result.
- ``POST /calendar/connections/{id}/disconnect``: revoke the grant at Google,
  then mark the connection revoked.
- ``PUT /settings/lead-agent``: switch the lead agent's use of open times on or
  off, changing nothing else in the settings.
- ``PUT /settings/public-booking``: open booking to the public, close it, or
  go back to the environment's default, changing nothing else.
- ``GET /agents``, ``POST /agents``, ``PUT /agents/{id}``,
  ``POST /agents/{id}/deactivate`` and ``.../activate``: the showing agents.
- ``POST /agents/load-samples``: add the made-up agents from ``seed.py``.
  Refused in production.
- ``GET /showings``: each unit on offer with the offers it makes now, how each
  agent's calendar was read, and the showings booked in the next 14 days with
  the people booked in each and any booking that needs the team.
- ``POST /showings/{id}/cancel``: cancel a showing and everyone in it.
- ``POST /showings/{id}/attendees/{attendee_id}/remove``: remove one person.
  Both send what ``cancelling.py`` describes; a second call changes nothing.
"""

from datetime import UTC, datetime, timedelta

import logfire
from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.app_settings import AppSetting
from api.src.database.database import DBSession
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.models import RentalListingRecord
from api.src.showing_bookings import agents, cancelling, connections, holds, open_times
from api.src.showing_bookings.guard import PRODUCTION
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.schema import (
    DAYS_AHEAD,
    AttendeeSource,
    ConnectionStatus,
    Email,
    Notice,
    SendMode,
    ShowingAgent,
)
from api.src.showing_bookings.settings import (
    SETTINGS_KEY,
    ShowingBookingSettings,
    current_environment,
    public_booking_default,
    public_booking_on,
    read_settings,
    read_test_recipients,
)
from api.src.showing_bookings.wording import display_phone
from api.src.utils.clerk import verify_serniacapital_user


def text_sender() -> TextSender | None:
    """None sends through Quo, behind the outbox's guard. Tests replace it with a stand-in."""
    return None


def clock() -> datetime:
    """The time a team action is handled at. Tests replace it to move the clock."""
    return datetime.now(UTC)


router = APIRouter(
    prefix="/showing-bookings",
    tags=["showing-bookings"],
    dependencies=[Depends(verify_serniacapital_user)],
)


class SettingsView(BaseModel):
    settings: ShowingBookingSettings
    # Railway's environment name, or "local".
    environment: str
    is_production: bool
    # Who test sends can reach here, shortened: "•••-0142", "p…@example.com".
    test_recipients: list[str]
    # Whether the public booking routes answer here, and what unset means here.
    public_booking_on: bool
    public_booking_default: bool


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: ShowingBookingSettings
    based_on: ShowingBookingSettings


def _shortened(address: str) -> str:
    if "@" in address:
        name, domain = address.split("@", 1)
        return f"{name[:1]}…@{domain}"
    return f"•••-{address[-4:]}"


def _wording(notice: ShowingNotice) -> str:
    payload = notice.payload
    if notice.channel == "sms":
        return payload.get("body", "")
    if notice.channel == "email":
        return f"Subject: {payload.get('subject', '')}\n\n{payload.get('body', '')}"
    action = {"create": "New event", "update": "Changed event", "cancel": "Cancelled event"}
    lines = [f"{action.get(payload.get('operation'), 'Event')}: {payload.get('summary', '')}"]
    if payload.get("location"):
        lines.append(payload["location"])
    if payload.get("description"):
        lines.append(payload["description"])
    if payload.get("attendees"):
        lines.append("Guests: " + ", ".join(payload["attendees"]))
    return "\n".join(lines)


@router.get("/settings", response_model=SettingsView)
async def get_settings(session: DBSession):
    try:
        settings = await read_settings(session)
    except Exception:
        logfire.exception("showing bookings: settings could not be loaded")
        raise HTTPException(503, "Settings could not be loaded. Try again.") from None
    environment = current_environment()
    return SettingsView(
        settings=settings,
        environment=environment or "local",
        is_production=environment == PRODUCTION,
        test_recipients=sorted(_shortened(address) for address in read_test_recipients()),
        public_booking_on=public_booking_on(settings, environment),
        public_booking_default=public_booking_default(environment),
    )


@router.patch("/settings", response_model=ShowingBookingSettings)
async def update_settings(
    body: SettingsUpdate, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    new = body.settings
    if new.live_units:
        # Save each live unit as its listing spells it, and refuse a unit with no listing.
        listed = {
            unit.casefold(): unit
            for unit in await session.scalars(
                select(RentalListingRecord.unit).where(
                    func.lower(RentalListingRecord.unit).in_(
                        [unit.casefold() for unit in new.live_units]
                    )
                )
            )
        }
        unknown = [unit for unit in new.live_units if unit.casefold() not in listed]
        if unknown:
            raise HTTPException(422, f"No rental listing for: {', '.join(unknown)}.")
        new = new.model_copy(
            update={"live_units": [listed[unit.casefold()] for unit in new.live_units]}
        )

    # Create the row if absent, then lock it, so two first saves cannot both win.
    current = await read_settings(session)
    await session.execute(
        insert(AppSetting)
        .values(key=SETTINGS_KEY, value=current.model_dump())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    row = await session.scalar(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY).with_for_update()
    )
    stored = ShowingBookingSettings.model_validate(row.value)
    # The lead agent's switch and the public booking switch have their own
    # routes, and the public limits are changed in the row itself; all three are
    # kept as stored here, so a send-mode screen loaded before one of them moved
    # neither undoes it nor is refused.
    switch = {
        "lead_agent_uses_open_times": stored.lead_agent_uses_open_times,
        "public_booking_enabled": stored.public_booking_enabled,
        "public_limits": stored.public_limits,
    }
    if stored != body.based_on.model_copy(update=switch):
        raise HTTPException(409, "Settings changed elsewhere. Reload before saving.")
    new = new.model_copy(update=switch)
    row.value = new.model_dump()
    await session.commit()
    logfire.info("showing bookings: settings updated", user_id=user.id, **new.model_dump())
    return new


@router.get("/notices", response_model=list[Notice])
async def list_notices(session: DBSession, limit: int = Query(100, ge=1, le=500)):
    rows = await session.execute(
        select(ShowingNotice, Showing.unit, Showing.starts_at)
        .join(Showing, Showing.id == ShowingNotice.showing_id)
        .order_by(ShowingNotice.id.desc())
        .limit(limit)
    )
    return [
        Notice(
            id=notice.id,
            kind=notice.kind,
            channel=notice.channel,
            showing_id=notice.showing_id,
            attendee_id=notice.attendee_id,
            unit=unit,
            showing_starts_at=starts_at,
            recipient=notice.recipient,
            recipient_key=notice.recipient_key,
            wording=_wording(notice),
            payload=notice.payload,
            status=notice.status,
            mode=notice.mode,
            guard_reason=notice.guard_reason,
            not_before=notice.not_before,
            attempts=notice.attempts,
            provider_status=notice.provider_status,
            last_error=notice.last_error,
            created_at=notice.created_at,
            delivered_at=notice.delivered_at,
        )
        for notice, unit, starts_at in rows
    ]


@router.post("/notices/send-pending", response_model=holds.SendPendingReport)
async def send_pending(user: User = Depends(verify_serniacapital_user)):
    report = await holds.send_pending(now=datetime.now(UTC))
    logfire.info(
        "showing bookings: send pending run by hand", user_id=user.id, **report.model_dump()
    )
    return report


# ---------------------------------------------------------------------------
# Calendar connections
# ---------------------------------------------------------------------------


class ConnectionView(BaseModel):
    """One connected Google account, as the Connections section shows it."""

    id: int
    google_email: str
    status: ConnectionStatus
    has_showings_calendar: bool
    # The Sernia email of the team member who connected it.
    connected_by: str
    connected_at: datetime
    last_read_at: datetime | None
    # Empty until the first read.
    last_read_ok: bool | None
    last_error: str | None
    # False when this environment cannot use it: lost or revoked, no Google
    # client here, or no key to read its token (as in a preview).
    usable_here: bool
    unusable_reason: str | None


class ConnectionsView(BaseModel):
    # Whether a calendar can be connected here, and why not.
    can_connect: bool
    setup_problem: str | None
    connections: list[ConnectionView]


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The page's own origin; Google sends the browser back there.
    origin: str = Field(max_length=200)
    # The account to preselect on Google's screen, for a reconnect.
    account: Email | None = None


class ConnectStart(BaseModel):
    url: str


def _connection_view(connection: CalendarConnection) -> ConnectionView:
    reason = connections.unusable_reason(connection)
    return ConnectionView(
        id=connection.id,
        google_email=connection.google_email,
        status=connection.status,
        has_showings_calendar=bool(connection.showings_calendar_id),
        connected_by=connection.connected_by,
        connected_at=connection.connected_at,
        last_read_at=connection.last_read_at,
        last_read_ok=connection.last_read_ok,
        last_error=connection.last_error,
        usable_here=reason is None,
        unusable_reason=reason,
    )


def _sernia_email(user: User) -> str:
    """The person's verified Sernia email, or their user id when there is none."""
    for address in getattr(user, "email_addresses", None) or []:
        verification = getattr(address, "verification", None)
        if address.email_address.endswith("@serniacapital.com") and (
            verification is not None and verification.status == "verified"
        ):
            return address.email_address
    return user.id


async def _connection_view_by_id(session: AsyncSession, connection_id: int) -> ConnectionView:
    connection = await session.get(CalendarConnection, connection_id)
    if connection is None:
        raise HTTPException(404, "No such calendar connection.")
    return _connection_view(connection)


@router.get("/calendar/connections", response_model=ConnectionsView)
async def list_connections(session: DBSession):
    rows = await session.scalars(
        # Working ones first, then lost ones, which need a reconnect, then disconnected ones.
        select(CalendarConnection).order_by(
            case(
                (CalendarConnection.status == "connected", 0),
                (CalendarConnection.status == "lost", 1),
                else_=2,
            ),
            CalendarConnection.google_email,
        )
    )
    problem = connections.setup_problem()
    return ConnectionsView(
        can_connect=problem is None,
        setup_problem=problem,
        connections=[_connection_view(row) for row in rows],
    )


@router.post("/calendar/connect", response_model=ConnectStart)
async def start_connection(body: ConnectRequest, user: User = Depends(verify_serniacapital_user)):
    try:
        url = connections.start_connect(
            user_id=user.id, by=_sernia_email(user), origin=body.origin, account=body.account
        )
    except connections.NotConfigured as problem:
        raise HTTPException(409, str(problem)) from None
    except ValueError as refused:
        raise HTTPException(400, str(refused)) from None
    logfire.info("showing bookings: calendar connection started", user_id=user.id)
    return ConnectStart(url=url)


@router.post("/calendar/connections/{connection_id}/check", response_model=ConnectionView)
async def check_connection(connection_id: int, session: DBSession):
    try:
        await connections.check_connection(connection_id, now=datetime.now(UTC))
    except LookupError:
        raise HTTPException(404, "No such calendar connection.") from None
    except connections.ConnectionUnusable as unusable:
        raise HTTPException(409, str(unusable)) from None
    return await _connection_view_by_id(session, connection_id)


@router.post("/calendar/connections/{connection_id}/disconnect", response_model=ConnectionView)
async def disconnect_connection(
    connection_id: int, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    try:
        await connections.disconnect(connection_id, user_id=user.id)
    except LookupError:
        raise HTTPException(404, "No such calendar connection.") from None
    except connections.CannotDisconnect as refused:
        raise HTTPException(409, str(refused)) from None
    return await _connection_view_by_id(session, connection_id)


# ---------------------------------------------------------------------------
# The lead agent's switch
# ---------------------------------------------------------------------------


class LeadAgentSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_agent_uses_open_times: bool


class PublicBookingSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None goes back to the environment's default: off in production, on elsewhere.
    public_booking_enabled: bool | None


@router.put("/settings/public-booking", response_model=SettingsView)
async def set_public_booking_switch(
    body: PublicBookingSwitch, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    """Open or close booking to the public, changing nothing else in the settings."""
    try:
        current = await read_settings(session)
    except Exception:
        logfire.exception("showing bookings: settings could not be loaded")
        raise HTTPException(503, "Settings could not be loaded. Try again.") from None
    await session.execute(
        insert(AppSetting)
        .values(key=SETTINGS_KEY, value=current.model_dump())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    row = await session.scalar(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY).with_for_update()
    )
    new = ShowingBookingSettings.model_validate(row.value).model_copy(
        update={"public_booking_enabled": body.public_booking_enabled}
    )
    row.value = new.model_dump()
    await session.commit()
    logfire.info(
        "showing bookings: public booking switched",
        user_id=user.id,
        public_booking_enabled=body.public_booking_enabled,
        on=public_booking_on(new, current_environment()),
    )
    return await get_settings(session)


@router.put("/settings/lead-agent", response_model=ShowingBookingSettings)
async def set_lead_agent_switch(
    body: LeadAgentSwitch, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    """Change only the lead agent's switch, so it cannot undo another change to the settings."""
    try:
        current = await read_settings(session)
    except Exception:
        logfire.exception("showing bookings: settings could not be loaded")
        raise HTTPException(503, "Settings could not be loaded. Try again.") from None
    await session.execute(
        insert(AppSetting)
        .values(key=SETTINGS_KEY, value=current.model_dump())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    row = await session.scalar(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY).with_for_update()
    )
    new = ShowingBookingSettings.model_validate(row.value).model_copy(
        update={"lead_agent_uses_open_times": body.lead_agent_uses_open_times}
    )
    row.value = new.model_dump()
    await session.commit()
    logfire.info(
        "showing bookings: lead agent open times switched",
        user_id=user.id,
        on=body.lead_agent_uses_open_times,
    )
    return new


# ---------------------------------------------------------------------------
# Showing agents
# ---------------------------------------------------------------------------


class AgentView(ShowingAgent):
    """An agent as the Agents section shows it."""

    id: int
    # The account that reads this agent's calendars: their own connection, or
    # the team's when none is chosen. Empty when there is none.
    reads_through: str | None


def _agent_view(record: ShowingAgentRecord, rows: list[CalendarConnection]) -> AgentView:
    connection = agents.connection_for(record, rows)
    return AgentView(
        id=record.id,
        reads_through=connection.google_email if connection is not None else None,
        **agents.as_agent(record).model_dump(),
    )


async def _connection_rows(session: AsyncSession) -> list[CalendarConnection]:
    return list(await session.scalars(select(CalendarConnection)))


def _refused(invalid: agents.AgentInvalid) -> HTTPException:
    return HTTPException(422, {"message": "Not saved.", "problems": invalid.problems})


@router.get("/agents", response_model=list[AgentView])
async def list_agents(session: DBSession):
    rows = await _connection_rows(session)
    return [_agent_view(record, rows) for record in await agents.list_agents(session)]


@router.post("/agents", response_model=AgentView, status_code=201)
async def add_agent(
    body: ShowingAgent, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    try:
        record = await agents.add_agent(session, body)
    except agents.AgentInvalid as invalid:
        raise _refused(invalid) from None
    logfire.info("showing bookings: agent added", agent_id=record.id, user_id=user.id)
    return _agent_view(record, await _connection_rows(session))


@router.put("/agents/{agent_id}", response_model=AgentView)
async def change_agent(
    agent_id: int,
    body: ShowingAgent,
    session: DBSession,
    user: User = Depends(verify_serniacapital_user),
):
    try:
        record = await agents.change_agent(session, agent_id, body)
    except LookupError:
        raise HTTPException(404, "No such showing agent.") from None
    except agents.AgentInvalid as invalid:
        raise _refused(invalid) from None
    logfire.info("showing bookings: agent changed", agent_id=agent_id, user_id=user.id)
    return _agent_view(record, await _connection_rows(session))


async def _set_active(session: AsyncSession, agent_id: int, active: bool, user: User) -> AgentView:
    try:
        record = await agents.set_active(session, agent_id, active)
    except LookupError:
        raise HTTPException(404, "No such showing agent.") from None
    logfire.info(
        "showing bookings: agent active changed", agent_id=agent_id, active=active, user_id=user.id
    )
    return _agent_view(record, await _connection_rows(session))


@router.post("/agents/{agent_id}/deactivate", response_model=AgentView)
async def deactivate_agent(
    agent_id: int, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    return await _set_active(session, agent_id, False, user)


@router.post("/agents/{agent_id}/activate", response_model=AgentView)
async def activate_agent(
    agent_id: int, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    return await _set_active(session, agent_id, True, user)


class SamplesAdded(BaseModel):
    added: list[str]


@router.post("/agents/load-samples", response_model=SamplesAdded)
async def load_sample_agents(session: DBSession, user: User = Depends(verify_serniacapital_user)):
    """Add the made-up agents, so a preview can show open times. Never in production."""
    if current_environment() == PRODUCTION:
        raise HTTPException(403, "Sample agents are never added in production.")
    added = await agents.add_sample_agents(session)
    logfire.info("showing bookings: sample agents loaded", added=len(added), user_id=user.id)
    return SamplesAdded(added=added)


# ---------------------------------------------------------------------------
# Showings and open times
# ---------------------------------------------------------------------------


class BookedPersonView(BaseModel):
    """One person booked into a showing, as the team sees them."""

    id: int
    name: str
    # "412-555-0117", or "no mobile".
    mobile: str
    email: str | None
    source: AttendeeSource
    # False for someone booked without texts: a cancellation sends them none.
    texts_accepted: bool
    # Set when the booking needs a call, such as "confirmation_undelivered".
    attention_reason: str | None


class BookedShowingView(BaseModel):
    id: int
    unit: str
    starts_at: datetime
    host_name: str
    attendees: int
    capacity: int
    # The people booked, in the order they booked.
    people: list[BookedPersonView]
    # Started showings can no longer be cancelled.
    started: bool
    # Why the unit's residents were not texted about it, such as "lookup_failed".
    resident_attention: str | None = None


class ShowingsView(BaseModel):
    open_times: open_times.OpenTimes
    # Scheduled showings from now to 14 days out, earliest first.
    booked: list[BookedShowingView]
    # Whether the Zillow lead agent offers these times (the switch in settings).
    lead_agent_uses_open_times: bool
    # The send mode, so a cancellation's confirmation can say what goes out.
    send_mode: SendMode


@router.get("/showings", response_model=ShowingsView)
async def list_showings(session: DBSession, now: datetime = Depends(clock)):
    try:
        found = await open_times.open_times(now=now, session=session)
    except RentalDataInvalid as invalid:
        raise HTTPException(
            409,
            {"message": "Rental listing data fails its checks.", "problems": invalid.problems},
        ) from None
    attendees = (
        select(ShowingAttendee.showing_id, func.count().label("count"))
        .where(ShowingAttendee.status == "booked")
        .group_by(ShowingAttendee.showing_id)
        .subquery()
    )
    rows = await session.execute(
        select(Showing, ShowingAgentRecord.name, func.coalesce(attendees.c.count, 0))
        .join(ShowingAgentRecord, ShowingAgentRecord.id == Showing.agent_id)
        .outerjoin(attendees, attendees.c.showing_id == Showing.id)
        .where(
            Showing.status == "scheduled",
            Showing.ends_at > now,
            Showing.starts_at <= now + timedelta(days=DAYS_AHEAD),
        )
        .order_by(Showing.starts_at, Showing.unit)
    )
    showings = [(showing, name, count) for showing, name, count in rows]
    people: dict[int, list[BookedPersonView]] = {}
    if showings:
        for person in await session.scalars(
            select(ShowingAttendee)
            .where(
                ShowingAttendee.showing_id.in_([showing.id for showing, *_ in showings]),
                ShowingAttendee.status == "booked",
            )
            .order_by(ShowingAttendee.id)
        ):
            people.setdefault(person.showing_id, []).append(
                BookedPersonView(
                    id=person.id,
                    name=person.name,
                    mobile=display_phone(person.mobile),
                    email=person.email,
                    source=person.source,
                    texts_accepted=person.texts_accepted,
                    attention_reason=person.attention_reason,
                )
            )
    try:
        settings = await read_settings(session)
        switch, send_mode = settings.lead_agent_uses_open_times, settings.send_mode
    except Exception:
        switch, send_mode = False, "capture"
    return ShowingsView(
        open_times=found,
        booked=[
            BookedShowingView(
                id=showing.id,
                unit=showing.unit,
                starts_at=showing.starts_at,
                host_name=name,
                attendees=count,
                capacity=showing.capacity,
                people=people.get(showing.id, []),
                started=showing.starts_at <= now,
                resident_attention=showing.resident_attention,
            )
            for showing, name, count in showings
        ],
        lead_agent_uses_open_times=switch,
        send_mode=send_mode,
    )


async def _team_cancel(action, user: User, **fields) -> cancelling.TeamCancel:
    try:
        result = await action
    except cancelling.NotFound:
        raise HTTPException(404, "No such showing or person.") from None
    except cancelling.Started:
        raise HTTPException(
            409, "This showing has started; it can no longer be cancelled."
        ) from None
    logfire.info("showing bookings: team cancellation", user_id=user.id, **fields)
    return result


@router.post("/showings/{showing_id}/cancel", response_model=cancelling.TeamCancel)
async def cancel_showing(
    showing_id: int,
    user: User = Depends(verify_serniacapital_user),
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    """Cancel the showing and everyone in it: their texts, invites and the host's event."""
    return await _team_cancel(
        cancelling.cancel_showing(showing_id, now=now, texts=texts), user, showing_id=showing_id
    )


@router.post(
    "/showings/{showing_id}/attendees/{attendee_id}/remove", response_model=cancelling.TeamCancel
)
async def remove_attendee(
    showing_id: int,
    attendee_id: int,
    user: User = Depends(verify_serniacapital_user),
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    """Remove one person, with the team's cancellation text; the last one cancels the showing."""
    return await _team_cancel(
        cancelling.remove_attendee(showing_id, attendee_id, now=now, texts=texts),
        user,
        showing_id=showing_id,
        attendee_id=attendee_id,
    )
