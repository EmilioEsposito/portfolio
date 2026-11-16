"""Shared set-up for the public booking tests. Not a test module.

A ``PublicWorld`` is the outside world as the public routes see it: one made-up
listing and the default showing rules (passed in, not read from the
database), one agent with a connection whose calendar is an
``InMemoryCalendar``, a ``RecordingTextSender`` in place of Quo, a stand-in
for Cloudflare Turnstile behind ``httpx.MockTransport``, and a clock the test
moves. Requests go through the real public router with ``httpx.ASGITransport``.
Names, numbers and addresses are made up.
"""

from datetime import date, datetime, time, timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings.schema import RentalListing, ShowingRules
from api.src.showing_bookings import connections, holds, public_routes, routes, turnstile
from api.src.showing_bookings.fakes import InMemoryCalendar, RecordingTextSender
from api.src.showing_bookings.models import (
    CalendarConnection,
    Showing,
    ShowingAgentRecord,
    ShowingAttendee,
    ShowingNotice,
)
from api.src.tests.showing_helpers import LOCAL

TUESDAY = date(2027, 4, 13)
WEDNESDAY = date(2027, 4, 14)
NOW = datetime.combine(TUESDAY, time(14, 0), LOCAL)
UNIT = "320-03"
ADDRESS = "320 Example St"
JANE = "jane.doe@example.com"
SHOWINGS_CALENDAR = "sernia-showings-test@example.com"
ALEX_MOBILE = "+14125550117"
ROBIN_MOBILE = "+14125550118"
CASEY_MOBILE = "+14125550119"
ALEX_EMAIL = "alex@example.com"
ROBIN_EMAIL = "robin@example.com"
CASEY_EMAIL = "casey@example.com"
FAILING_TOKEN = "turnstile-fails"
PASSING_TOKEN = "turnstile-passes"


def at(hour: int, minute: int = 0, day: date = WEDNESDAY) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL)


class PublicWorld:
    def __init__(self) -> None:
        self.now = NOW
        self.listing = RentalListing(
            unit=UNIT,
            address=ADDRESS,
            asking_rent=1450,
            next_lease_start_earliest=date(2027, 6, 1),
            next_lease_start_latest=date(2027, 7, 1),
        )
        self.rules = ShowingRules()
        self.calendar = InMemoryCalendar()
        self.texts = RecordingTextSender()
        # Each check Turnstile was asked for: (secret, token, remote ip).
        self.turnstile_checks: list[tuple[str, str, str | None]] = []
        self.agent_id: int | None = None

    def app(self) -> FastAPI:
        application = FastAPI()
        application.include_router(public_routes.public_router, prefix="/api")
        application.dependency_overrides[public_routes.clock] = lambda: self.now
        application.dependency_overrides[public_routes.text_sender] = lambda: self.texts
        return application

    def team_app(self) -> FastAPI:
        application = FastAPI()
        application.include_router(routes.router, prefix="/api")
        application.dependency_overrides[routes.verify_serniacapital_user] = lambda: _Person()
        return application

    def client(self, app: FastAPI | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app or self.app())
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    def turnstile_transport(self) -> httpx.MockTransport:
        def answer(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == turnstile.VERIFY_URL
            form = dict(httpx.QueryParams(request.content.decode()))
            self.turnstile_checks.append(
                (form.get("secret", ""), form.get("response", ""), form.get("remoteip"))
            )
            # Cloudflare's test secret starting "2x" always fails, as does our failing token.
            ok = not form.get("secret", "").startswith("2x") and form["response"] != FAILING_TOKEN
            body = {"success": ok}
            if not ok:
                body["error-codes"] = ["invalid-input-response"]
            return httpx.Response(200, json=body)

        return httpx.MockTransport(answer)

    async def send_pending(self, now: datetime | None = None) -> holds.SendPendingReport:
        return await holds.send_pending(
            now=now or self.now, texts=self.texts, calendar=self.calendar
        )


class _Person:
    id = "user_test"


def install(world: PublicWorld, monkeypatch) -> None:
    """Point the module at the world's listing, rules, calendar and Turnstile."""
    from api.src.rental_listings import service as listings_service

    async def read_listings(session):
        return [world.listing]

    async def read_rules(session, lock=False):
        return world.rules

    monkeypatch.setattr(listings_service, "read_listings", read_listings)
    monkeypatch.setattr(listings_service, "read_showing_rules", read_rules)
    monkeypatch.setattr(connections, "calendar_for_connection", lambda row: world.calendar)
    monkeypatch.setattr(connections, "unusable_reason", lambda row: None)
    monkeypatch.setattr(
        turnstile,
        "client",
        lambda: httpx.AsyncClient(transport=world.turnstile_transport()),
    )


async def add_world_rows(world: PublicWorld) -> None:
    async with AsyncSessionFactory() as session:
        connection = CalendarConnection(
            google_email="team@example.com",
            google_subject="100000000000000000001",
            connected_by="pat@serniacapital.com",
            connected_at=NOW - timedelta(days=30),
            showings_calendar_id=SHOWINGS_CALENDAR,
        )
        session.add(connection)
        await session.flush()
        agent = ShowingAgentRecord(
            name="Jane Doe",
            email=JANE,
            mobile="+14125550101",
            busy_calendar_ids=[JANE],
        )
        session.add(agent)
        await session.commit()
        world.agent_id = agent.id


def booking(
    start: datetime | str | None = None,
    *,
    name: str = "Alex Parker",
    mobile: str = "412-555-0117",
    email: str = ALEX_EMAIL,
    token: str = PASSING_TOKEN,
    **changes,
) -> dict:
    body = {
        "start": start if isinstance(start, str) else (start or at(16)).isoformat(),
        "name": name,
        "mobile": mobile,
        "email": email,
        "confirmations": {"lease_start": True, "pets": True, "texts": True},
        "turnstile_token": token,
    }
    body.update(changes)
    return body


async def rows() -> tuple[list[Showing], list[ShowingAttendee], list[ShowingNotice]]:
    async with AsyncSessionFactory() as session:
        showings = list(await session.scalars(select(Showing).order_by(Showing.id)))
        attendees = list(
            await session.scalars(select(ShowingAttendee).order_by(ShowingAttendee.id))
        )
        notices = list(await session.scalars(select(ShowingNotice).order_by(ShowingNotice.id)))
    return showings, attendees, notices


def by_kind(notices: list[ShowingNotice]) -> dict[str, list[ShowingNotice]]:
    found: dict[str, list[ShowingNotice]] = {}
    for notice in notices:
        found.setdefault(notice.kind, []).append(notice)
    return found


def statuses(notices: list[ShowingNotice]) -> list[tuple[str, str]]:
    return [(notice.kind, notice.status) for notice in notices]
