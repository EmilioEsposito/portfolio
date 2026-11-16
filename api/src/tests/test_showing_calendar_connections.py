"""Calendar connections end to end: connecting through Google's callback, checking,
losing and disconnecting a connection, and the outbox sending through it.

Uses the test database. Google is ``FakeGoogle`` behind ``httpx.MockTransport``;
nothing reaches Google. Sign-in is replaced by a stand-in Sernia user, except
in the tests of who may call.
"""

import html
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from logfire.testing import capfire as capfire
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import (
    calendar_callback,
    connections,
    google_http,
    google_oauth,
    outbox,
    routes,
    token_crypto,
)
from api.src.showing_bookings.fakes import InMemoryCalendar
from api.src.showing_bookings.models import CalendarConnection, Showing, ShowingNotice
from api.src.showing_bookings.providers import CalendarUnreadable, EventDraft, NotSent
from api.src.showing_bookings.schema import CalendarPayload
from api.src.tests.showing_google import (
    CLIENT_SECRET,
    ORIGIN,
    OTHER_TOKEN_KEY,
    TEAM_ACCOUNT,
    TEAM_SUBJECT,
    TOKEN_KEY,
    Account,
    FakeGoogle,
    configure,
)
from api.src.tests.showing_helpers import (
    TEAM_EMAIL,
    UNIT,
    add_agent,
    add_showing,
    at,
    clear_showing_data,
    store_settings,
)
from api.src.utils import clerk as clerk_module

TEAM_MEMBER = "pat@serniacapital.com"
PERSON = SimpleNamespace(
    id="user_test",
    email_addresses=[
        SimpleNamespace(email_address=TEAM_MEMBER, verification=SimpleNamespace(status="verified"))
    ],
)
NOW = at(9)
CONNECTIONS = "/api/showing-bookings/calendar/connections"
REAL_GOOGLE_CLIENT = google_http.client


def app(signed_in: bool = True) -> FastAPI:
    application = FastAPI()
    application.include_router(routes.router, prefix="/api")
    application.include_router(calendar_callback.callback_router, prefix="/api")
    if signed_in:
        application.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    return application


@pytest_asyncio.fixture(autouse=True)
async def clean(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", "412-555-0150")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", TEAM_EMAIL)
    configure(monkeypatch)
    # The test client logs each address it calls, and a callback's address holds
    # its code; keep that out of the test output.
    httpx_logger = logging.getLogger("httpx")
    level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    connections.forget_access_tokens()
    await clear_showing_data()
    yield
    connections.forget_access_tokens()
    await clear_showing_data()
    httpx_logger.setLevel(level)


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    return FakeGoogle().install(monkeypatch)


@pytest_asyncio.fixture
async def api():
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def start(api, **body) -> dict[str, str]:
    """Press Connect: the consent URL's query, as Google receives it."""
    response = await api.post(
        "/api/showing-bookings/calendar/connect", json={"origin": ORIGIN, **body}
    )
    assert response.status_code == 200, response.text
    return {
        key: values[0] for key, values in parse_qs(urlsplit(response.json()["url"]).query).items()
    }


async def connect(api, google: FakeGoogle, account: Account | None = None) -> httpx.Response:
    """Connect, approve on the fake Google's screen, and land on the callback."""
    consent = await start(api)
    code = google.approve(account, redirect_uri=consent["redirect_uri"])
    return await api.get(
        "/api/showing-bookings/calendar/callback",
        params={"code": code, "state": consent["state"]},
    )


def result_of(page: httpx.Response) -> str:
    """The result the callback's page sends the Booking settings tab."""
    assert page.status_code == 200
    target = re.search(r'http-equiv="refresh" content="0; url=([^"]+)"', page.text).group(1)
    address = urlsplit(html.unescape(target))
    assert (address.scheme, address.netloc, address.path, address.fragment) == (
        "",
        "",
        "/systems/leasing-leads/bookings",
        "calendar-connections",
    )
    return parse_qs(address.query)["calendar"][0]


async def rows() -> list[CalendarConnection]:
    async with AsyncSessionFactory() as session:
        return list(
            await session.scalars(select(CalendarConnection).order_by(CalendarConnection.id))
        )


async def add_connection(
    email: str,
    subject: str,
    *,
    status: str = "connected",
    calendar_id: str | None = None,
    key: str | None = TOKEN_KEY,
) -> int:
    ciphertext = Fernet(key).encrypt(b"1//stored-refresh").decode() if key else None
    async with AsyncSessionFactory() as session:
        row = CalendarConnection(
            google_email=email,
            google_subject=subject,
            scopes=list(google_oauth.CALENDAR_SCOPES),
            encrypted_refresh_token=ciphertext,
            showings_calendar_id=calendar_id,
            status=status,
            connected_by=TEAM_MEMBER,
            connected_at=NOW,
        )
        session.add(row)
        await session.commit()
        return row.id


# Connecting


@pytest.mark.asyncio
async def test_connecting_saves_the_account_with_its_token_encrypted(api, google):
    page = await connect(api, google)

    assert result_of(page) == "connected"
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    (row,) = await rows()
    assert (row.google_email, row.google_subject, row.status, row.connected_by) == (
        TEAM_ACCOUNT,
        TEAM_SUBJECT,
        "connected",
        TEAM_MEMBER,
    )
    assert set(google_oauth.CALENDAR_SCOPES) <= set(row.scopes)
    (refresh_token,) = google.refresh_tokens
    assert refresh_token not in row.encrypted_refresh_token
    assert token_crypto.decrypt(row.encrypted_refresh_token) == refresh_token
    assert google.calendars[row.showings_calendar_id]["summary"] == "Sernia showings"
    assert not any(secret in page.text for secret in google.secrets)


@pytest.mark.asyncio
async def test_connecting_the_same_account_again_keeps_its_row_and_its_calendar(api, google):
    await connect(api, google)
    (first,) = await rows()
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(CalendarConnection).values(status="lost", last_read_ok=False, last_error="lost")
        )

    assert result_of(await connect(api, google)) == "connected"

    (again,) = await rows()
    assert (again.id, again.showings_calendar_id) == (first.id, first.showings_calendar_id)
    assert (again.status, again.last_read_ok, again.last_error) == ("connected", None, None)
    assert token_crypto.decrypt(again.encrypted_refresh_token) != token_crypto.decrypt(
        first.encrypted_refresh_token
    )
    assert len(google.sent("POST", "/calendar/v3/calendars")) == 1
    assert len(google.sent("GET", f"/calendar/v3/calendars/{first.showings_calendar_id}")) == 1


@pytest.mark.asyncio
async def test_a_reconnect_google_rate_limits_keeps_the_saved_calendar(api, google):
    await connect(api, google)
    (first,) = await rows()
    google.forced[("GET", f"/calendar/v3/calendars/{first.showings_calendar_id}")] = httpx.Response(
        403,
        json={
            "error": {
                "code": 403,
                "message": "Rate Limit Exceeded",
                "errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded"}],
            }
        },
    )

    assert result_of(await connect(api, google)) == "failed"

    (kept,) = await rows()
    assert kept.showings_calendar_id == first.showings_calendar_id
    assert kept.encrypted_refresh_token == first.encrypted_refresh_token
    assert len(google.sent("POST", "/calendar/v3/calendars")) == 1


@pytest.mark.asyncio
async def test_two_accounts_get_a_row_and_a_calendar_each(api, google):
    await connect(api, google)
    await connect(api, google, Account("100000000000000000002", "jane.doe@example.com"))

    first, second = await rows()
    assert (first.google_email, second.google_email) == (TEAM_ACCOUNT, "jane.doe@example.com")
    assert first.showings_calendar_id != second.showings_calendar_id


def expired_state(monkeypatch) -> str:
    import itsdangerous.timed

    real_time = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real_time() - 601)
    state = google_oauth.sign_state(user_id="user_test", by=TEAM_MEMBER, origin=ORIGIN)
    monkeypatch.setattr(itsdangerous.timed.time, "time", real_time)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["missing", "altered", "expired"])
async def test_a_callback_without_a_valid_state_saves_nothing(api, google, monkeypatch, problem):
    consent = await start(api)
    code = google.approve(redirect_uri=consent["redirect_uri"])
    state = {
        "missing": None,
        "altered": consent["state"][:-2] + ("AA" if consent["state"][-2:] != "AA" else "BB"),
        "expired": expired_state(monkeypatch),
    }[problem]
    params = {"code": code, **({"state": state} if state else {})}

    page = await api.get("/api/showing-bookings/calendar/callback", params=params)

    assert result_of(page) == ("expired" if problem == "expired" else "invalid")
    assert google.sent("POST", "/token") == []
    assert await rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, result", [("access_denied", "denied"), ("admin_policy_enforced", "failed")]
)
async def test_a_consent_refused_on_googles_screen_saves_nothing(api, google, error, result):
    consent = await start(api)

    page = await api.get(
        "/api/showing-bookings/calendar/callback",
        params={"error": error, "state": consent["state"]},
    )

    assert result_of(page) == result
    assert google.requests == [] and await rows() == []


@pytest.mark.asyncio
async def test_a_permission_left_unticked_saves_nothing(api, google):
    narrow = Account(TEAM_SUBJECT, TEAM_ACCOUNT, scopes=("openid", google_oauth.FREEBUSY_SCOPE))

    assert result_of(await connect(api, google, narrow)) == "missing_permissions"
    assert await rows() == []
    assert google.sent("POST", "/calendar/v3/calendars") == []


@pytest.mark.asyncio
async def test_a_code_google_refuses_saves_nothing(api, google):
    consent = await start(api)

    page = await api.get(
        "/api/showing-bookings/calendar/callback",
        params={"code": "4/never-issued", "state": consent["state"]},
    )

    assert result_of(page) == "failed"
    assert await rows() == []


@pytest.mark.asyncio
async def test_a_showings_calendar_google_will_not_make_saves_nothing(api, google):
    google.forced[("POST", "/calendar/v3/calendars")] = httpx.Response(
        403,
        json={"error": {"code": 403, "message": "Forbidden", "errors": [{"reason": "forbidden"}]}},
    )
    assert result_of(await connect(api, google)) == "failed"
    assert await rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["SHOWING_TOKEN_KEY", google_oauth.CLIENT_ID_ENV])
async def test_without_the_key_or_the_client_nothing_can_be_connected(
    api, google, monkeypatch, missing
):
    consent = await start(api)  # started while everything was set up
    code = google.approve(redirect_uri=consent["redirect_uri"])
    monkeypatch.delenv(missing)

    listed = (await api.get(CONNECTIONS)).json()
    refused = await api.post("/api/showing-bookings/calendar/connect", json={"origin": ORIGIN})
    page = await api.get(
        "/api/showing-bookings/calendar/callback",
        params={"code": code, "state": consent["state"]},
    )

    assert listed["can_connect"] is False and missing in listed["setup_problem"]
    assert refused.status_code == 409 and missing in refused.json()["detail"]
    assert result_of(page) == "not_configured"
    assert google.sent("POST", "/token") == [] and await rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    ["https://react-router-portfolio-pr-12.up.railway.app", "https://elsewhere.example.com", ""],
)
async def test_a_connection_starts_only_from_an_allowed_site(api, google, origin):
    response = await api.post("/api/showing-bookings/calendar/connect", json={"origin": origin})
    assert response.status_code == 400
    assert "eesposito.com" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_reconnect_preselects_the_account(api, google):
    consent = await start(api, account=TEAM_ACCOUNT)
    assert consent["login_hint"] == TEAM_ACCOUNT
    assert consent["redirect_uri"] == f"{ORIGIN}/api/showing-bookings/calendar/callback"
    assert consent["prompt"] == "consent" and consent["access_type"] == "offline"


# The list


@pytest.mark.asyncio
async def test_the_list_says_which_connections_can_be_used_here(api, google):
    usable = await add_connection(
        "team@example.com", "s1", calendar_id="c_1@group.calendar.google.com"
    )
    other_key = await add_connection("jane.doe@example.com", "s2", key=OTHER_TOKEN_KEY)
    lost = await add_connection("sam.lee@example.com", "s3", status="lost")
    revoked = await add_connection("old@example.com", "s4", status="revoked", key=None)

    listed = (await api.get(CONNECTIONS)).json()

    assert (listed["can_connect"], listed["setup_problem"]) == (True, None)
    assert [connection["google_email"] for connection in listed["connections"]] == [
        "jane.doe@example.com",
        "team@example.com",
        "sam.lee@example.com",
        "old@example.com",
    ]
    by_id = {connection["id"]: connection for connection in listed["connections"]}
    assert by_id[usable] | {"connected_at": None} == {
        "id": usable,
        "google_email": "team@example.com",
        "status": "connected",
        "has_showings_calendar": True,
        "connected_by": TEAM_MEMBER,
        "connected_at": None,
        "last_read_at": None,
        "last_read_ok": None,
        "last_error": None,
        "usable_here": True,
        "unusable_reason": None,
    }
    assert by_id[other_key]["usable_here"] is False
    assert "another key" in by_id[other_key]["unusable_reason"]
    assert (by_id[lost]["status"], by_id[lost]["usable_here"]) == ("lost", False)
    assert (by_id[revoked]["status"], by_id[revoked]["usable_here"]) == ("revoked", False)
    assert google.requests == []


@pytest.mark.asyncio
async def test_in_a_preview_without_the_key_nothing_is_usable(api, google, monkeypatch):
    await add_connection("team@example.com", "s1", calendar_id="c_1@group.calendar.google.com")
    monkeypatch.delenv("SHOWING_TOKEN_KEY")

    listed = (await api.get(CONNECTIONS)).json()

    (connection,) = listed["connections"]
    assert connection["usable_here"] is False
    assert "SHOWING_TOKEN_KEY is not set here" in connection["unusable_reason"]


# Check now


@pytest.mark.asyncio
async def test_check_now_reads_the_accounts_own_calendar_and_saves_the_result(api, google):
    await connect(api, google)
    (row,) = await rows()

    checked = await api.post(f"{CONNECTIONS}/{row.id}/check")

    assert checked.status_code == 200
    body = checked.json()
    assert (body["last_read_ok"], body["last_error"]) == (True, None) and body["last_read_at"]
    (read,) = google.sent("POST", "/calendar/v3/freeBusy")
    assert google.body(read)["items"] == [{"id": TEAM_ACCOUNT}]

    google.unreadable[TEAM_ACCOUNT] = "notFound"
    body = (await api.post(f"{CONNECTIONS}/{row.id}/check")).json()
    assert body["last_read_ok"] is False
    assert "Share its free/busy" in body["last_error"]
    assert body["status"] == "connected" and body["usable_here"] is True


@pytest.mark.asyncio
async def test_check_now_on_a_connection_unusable_here_reads_nothing(api, google, monkeypatch):
    connection_id = await add_connection("team@example.com", "s1", key=OTHER_TOKEN_KEY)

    response = await api.post(f"{CONNECTIONS}/{connection_id}/check")

    assert response.status_code == 409 and "another key" in response.json()["detail"]
    assert google.requests == []
    (row,) = await rows()
    assert row.last_read_at is None


@pytest.mark.asyncio
async def test_unknown_connections_are_not_found(api, google):
    assert (await api.post(f"{CONNECTIONS}/999999/check")).status_code == 404
    assert (await api.post(f"{CONNECTIONS}/999999/disconnect")).status_code == 404


@pytest.mark.asyncio
async def test_a_grant_google_no_longer_accepts_marks_the_connection_lost(api, google, capfire):
    await connect(api, google)
    (row,) = await rows()
    google.revoke_everything()  # access removed in the Google account's settings

    body = (await api.post(f"{CONNECTIONS}/{row.id}/check")).json()

    assert (body["status"], body["last_read_ok"], body["usable_here"]) == ("lost", False, False)
    assert "Reconnect" in body["last_error"]
    lost = [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["name"] == "showing bookings: calendar connection lost"
    ]
    assert [span["attributes"]["logfire.level_num"] for span in lost] == [17]  # error: alerted
    # Its calendars now read as unreadable, never free, and nothing more is asked of Google.
    (row,) = await rows()
    requests = len(google.requests)
    with pytest.raises(CalendarUnreadable):
        await connections.calendar_for_connection(row).busy_blocks([TEAM_ACCOUNT], at(9), at(21))
    assert len(google.requests) == requests
    assert (await api.post(f"{CONNECTIONS}/{row.id}/check")).status_code == 409


@pytest.mark.asyncio
async def test_access_tokens_are_refreshed_as_needed_and_never_saved(api, google):
    await connect(api, google)
    (row,) = await rows()

    def refreshes() -> int:
        return [google.form(r)["grant_type"] for r in google.sent("POST", "/token")].count(
            "refresh_token"
        )

    await api.post(f"{CONNECTIONS}/{row.id}/check")
    await api.post(f"{CONNECTIONS}/{row.id}/check")
    assert refreshes() == 1  # the second read reuses the first token

    google.access_lifetime = 30  # shorter than the minute kept in hand
    connections.forget_access_tokens()
    await api.post(f"{CONNECTIONS}/{row.id}/check")
    await api.post(f"{CONNECTIONS}/{row.id}/check")
    assert refreshes() == 3

    (row,) = await rows()
    stored = json.dumps(
        {column.key: getattr(row, column.key) for column in CalendarConnection.__mapper__.columns},
        default=str,
    )
    assert not [token for token in google.secrets if token.startswith("ya29.") and token in stored]


# Disconnecting


@pytest.mark.asyncio
async def test_disconnect_revokes_the_grant_then_marks_it_revoked(api, google):
    await connect(api, google)
    (row,) = await rows()
    refresh_token = token_crypto.decrypt(row.encrypted_refresh_token)

    response = await api.post(f"{CONNECTIONS}/{row.id}/disconnect")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["usable_here"]) == ("revoked", False)
    (revocation,) = google.sent("POST", "/revoke")
    assert google.form(revocation) == {"token": refresh_token}
    assert revocation.url.query == b""
    assert refresh_token not in google.refresh_tokens
    (revoked,) = await rows()
    assert (revoked.status, revoked.encrypted_refresh_token) == ("revoked", None)
    assert revoked.showings_calendar_id == row.showings_calendar_id

    # Again: Google has nothing left to revoke, so it is not asked.
    assert (await api.post(f"{CONNECTIONS}/{row.id}/disconnect")).status_code == 200
    assert len(google.sent("POST", "/revoke")) == 1
    # Connecting again brings the same row and calendar back.
    assert result_of(await connect(api, google)) == "connected"
    (back,) = await rows()
    assert (back.id, back.status, back.showings_calendar_id) == (
        row.id,
        "connected",
        row.showings_calendar_id,
    )


@pytest.mark.asyncio
async def test_a_lost_connection_can_still_be_disconnected(api, google):
    await connect(api, google)
    (row,) = await rows()
    google.revoke_everything()
    await api.post(f"{CONNECTIONS}/{row.id}/check")  # finds it lost

    response = await api.post(f"{CONNECTIONS}/{row.id}/disconnect")

    assert response.status_code == 200 and response.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_a_token_this_environment_cannot_read_is_not_disconnected_here(api, google):
    connection_id = await add_connection("team@example.com", "s1", key=OTHER_TOKEN_KEY)

    response = await api.post(f"{CONNECTIONS}/{connection_id}/disconnect")

    assert response.status_code == 409
    assert "Google cannot be told from here" in response.json()["detail"]
    assert google.requests == []
    (row,) = await rows()
    assert row.status == "connected" and row.encrypted_refresh_token


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [httpx.Response(503), httpx.ConnectError("refused")])
async def test_a_disconnect_google_does_not_confirm_changes_nothing(api, google, answer):
    await connect(api, google)
    (row,) = await rows()
    google.forced[("POST", "/revoke")] = answer

    response = await api.post(f"{CONNECTIONS}/{row.id}/disconnect")

    assert response.status_code == 409 and "Try again" in response.json()["detail"]
    (unchanged,) = await rows()
    assert unchanged.status == "connected"
    assert unchanged.encrypted_refresh_token == row.encrypted_refresh_token


# A connection that cannot be used


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, key, env_key, reason",
    [
        ("connected", OTHER_TOKEN_KEY, TOKEN_KEY, "calendar_unusable"),
        ("connected", TOKEN_KEY, None, "calendar_unusable"),
        ("lost", TOKEN_KEY, TOKEN_KEY, "calendar_lost"),
        ("revoked", None, TOKEN_KEY, "calendar_not_connected"),
    ],
)
async def test_an_unusable_connection_reads_as_unreadable_and_refuses_changes(
    google, monkeypatch, status, key, env_key, reason
):
    await add_connection(TEAM_ACCOUNT, TEAM_SUBJECT, status=status, key=key)
    configure(monkeypatch, key=env_key)
    (row,) = await rows()
    calendar = connections.calendar_for_connection(row)
    event = EventDraft(
        summary="Showing", starts_at=at(16), ends_at=at(16, 30), attendees=[TEAM_EMAIL]
    )

    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks(["jane.doe@example.com", TEAM_ACCOUNT], at(9), at(21))
    with pytest.raises(NotSent) as refused:
        await calendar.create_event("c_1@group.calendar.google.com", event)

    assert set(unreadable.value.unreadable) == {"jane.doe@example.com", TEAM_ACCOUNT}
    assert unreadable.value.busy == {}
    assert refused.value.reason == reason
    assert google.requests == []


# The outbox


async def queue_event(showing_id: int, kind: str, operation: str, calendar_id: str) -> int:
    payload = CalendarPayload(
        operation=operation,
        calendar_id=calendar_id,
        summary=f"Showing {UNIT}",
        location="100 Example Ave",
        starts_at=at(16),
        ends_at=at(16, 30),
        attendees=[TEAM_EMAIL],
    )
    async with AsyncSessionFactory() as session:
        notice = await outbox.enqueue(
            session,
            kind=kind,
            channel="calendar_event",
            showing_id=showing_id,
            recipient=TEAM_EMAIL,
            recipient_key="agent:1",
            payload=payload,
            now=NOW,
        )
        await session.commit()
        return notice.id


async def notice(notice_id: int) -> ShowingNotice:
    async with AsyncSessionFactory() as session:
        return await session.get(ShowingNotice, notice_id)


@pytest.mark.asyncio
async def test_a_calendar_notice_goes_to_the_connection_that_holds_its_calendar(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await store_settings(send_mode="live", live_units=[UNIT])
    first = await add_connection("a@example.com", "sa", calendar_id="c_a@group.calendar.google.com")
    second = await add_connection(
        "b@example.com", "sb", calendar_id="c_b@group.calendar.google.com"
    )
    calendars = {first: InMemoryCalendar(), second: InMemoryCalendar()}
    monkeypatch.setattr(connections, "calendar_for_connection", lambda row: calendars[row.id])
    showing = await add_showing(await add_agent())
    await queue_event(showing, "host_event_created", "create", "c_b@group.calendar.google.com")

    assert (await outbox.drain(now=NOW)).sent == 1

    assert calendars[first].log == []
    assert calendars[second].log == [("create", "c_b@group.calendar.google.com", "evt0001")]


@pytest.mark.asyncio
async def test_the_hosts_event_goes_through_google_and_tells_the_guests(api, google):
    await connect(api, google)
    (row,) = await rows()
    await store_settings(send_mode="live", test_sends_enabled=True)
    showing = await add_showing(await add_agent())
    created = await queue_event(showing, "host_event_created", "create", row.showings_calendar_id)

    assert (await outbox.drain(now=NOW)).sent == 1

    (event_id,) = google.events
    assert ((await notice(created)).status, (await notice(created)).provider_id) == (
        "sent",
        event_id,
    )
    async with AsyncSessionFactory() as session:
        assert (await session.get(Showing, showing)).host_event_id == event_id
    assert google.events[event_id].body["attendees"] == [{"email": TEAM_EMAIL}]

    await queue_event(showing, "host_event_updated", "update", row.showings_calendar_id)
    await queue_event(showing, "host_event_cancelled", "cancel", row.showings_calendar_id)
    assert (await outbox.drain(now=at(9, 5))).sent == 2

    writes = [request for request in google.requests if "/events" in google._path(request)]
    assert [request.method for request in writes] == ["POST", "PATCH", "DELETE"]
    assert all(request.url.params["sendUpdates"] == "all" for request in writes)
    assert google.events[event_id].deleted


@pytest.mark.asyncio
async def test_a_lost_connection_fails_its_calendar_notices_without_writing(api, google):
    await connect(api, google)
    (row,) = await rows()
    google.revoke_everything()
    await store_settings(send_mode="live", test_sends_enabled=True)
    showing = await add_showing(await add_agent())
    created = await queue_event(showing, "host_event_created", "create", row.showings_calendar_id)

    assert (await outbox.drain(now=NOW)).failed == 1

    assert (await notice(created)).provider_status == "calendar_lost"
    assert google.events == {}
    assert (await rows())[0].status == "lost"


@pytest.mark.asyncio
async def test_in_a_preview_without_the_key_calendar_notices_fail(api, google, monkeypatch):
    await connect(api, google)
    (row,) = await rows()
    monkeypatch.delenv("SHOWING_TOKEN_KEY")
    await store_settings(send_mode="live", test_sends_enabled=True)
    showing = await add_showing(await add_agent())
    created = await queue_event(showing, "host_event_created", "create", row.showings_calendar_id)
    requests = len(google.requests)

    assert (await outbox.drain(now=NOW)).failed == 1

    assert (await notice(created)).provider_status == "calendar_unusable"
    assert len(google.requests) == requests


@pytest.mark.asyncio
async def test_a_test_without_its_mock_fails_loudly_instead_of_reaching_google(
    api, google, monkeypatch
):
    await connect(api, google)
    (row,) = await rows()
    monkeypatch.setattr(google_http, "client", REAL_GOOGLE_CLIENT)  # refuses under pytest
    await store_settings(send_mode="live", test_sends_enabled=True)
    showing = await add_showing(await add_agent())
    created = await queue_event(showing, "host_event_created", "create", row.showings_calendar_id)

    with pytest.raises(RuntimeError, match="MockTransport"):
        await outbox.drain(now=NOW)
    assert (await notice(created)).status == "pending"


# What reaches the logs and the browser


@pytest.mark.asyncio
async def test_no_token_code_or_secret_reaches_the_logs_or_the_browser(google, capfire):
    from api.index import app as real_app

    real_app.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    try:
        transport = httpx.ASGITransport(app=real_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            consent = await start(client)
            page = await connect(client, google)
            (row,) = await rows()
            answers = [
                page.text,
                (await client.get(CONNECTIONS)).text,
                (await client.post(f"{CONNECTIONS}/{row.id}/check")).text,
                (await client.post(f"{CONNECTIONS}/{row.id}/disconnect")).text,
            ]
    finally:
        real_app.dependency_overrides.pop(routes.verify_serniacapital_user, None)

    assert result_of(page) == "connected"
    secrets = [*google.secrets, CLIENT_SECRET, TOKEN_KEY]
    for answer in answers:
        assert not [secret for secret in secrets if secret in answer]
    # The test client's own requests are left out: in production the browser makes them.
    exported = json.dumps(
        [
            span
            for span in capfire.exporter.exported_spans_as_dict()
            if not str(
                span["attributes"].get("http.url") or span["attributes"].get("url.full") or ""
            ).startswith("http://test/")
        ]
        + capfire.log_exporter.exported_logs_as_dicts(),
        default=str,
    )
    assert "showing bookings: calendar connected" in exported
    assert not [secret for secret in [*secrets, consent["state"]] if secret in exported]


# Who may call


def depends_on(dependant: Dependant, call) -> bool:
    return any(d.call is call or depends_on(d, call) for d in dependant.dependencies)


def test_every_showing_bookings_route_needs_a_sernia_user_but_the_public_ones():
    from api.index import app as real_app

    found = {
        route.path: depends_on(route.dependant, routes.verify_serniacapital_user)
        for route in real_app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/showing-bookings")
    }
    assert found.pop("/api/showing-bookings/calendar/callback") is False
    public = {path for path in found if path.startswith("/api/showing-bookings/public/")}
    assert public == {
        "/api/showing-bookings/public/units",
        "/api/showing-bookings/public/call-line",
        "/api/showing-bookings/public/units/{unit}",
        "/api/showing-bookings/public/units/{unit}/bookings",
        "/api/showing-bookings/public/bookings/lookup",
        "/api/showing-bookings/public/bookings/mobile",
        "/api/showing-bookings/public/bookings/cancel",
        "/api/showing-bookings/public/team-phone",
    }
    assert not any(found.pop(path) for path in public)
    assert {
        "/api/showing-bookings/calendar/connections",
        "/api/showing-bookings/calendar/connect",
        "/api/showing-bookings/calendar/connections/{connection_id}/check",
        "/api/showing-bookings/calendar/connections/{connection_id}/disconnect",
    } <= set(found)
    assert all(found.values()), found


@pytest.mark.asyncio
async def test_without_a_sign_in_the_calendar_routes_are_refused(google, monkeypatch):
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=lambda request, options: SimpleNamespace(is_signed_in=False)
        ),
    )
    connection_id = await add_connection(TEAM_ACCOUNT, TEAM_SUBJECT)
    check = AsyncMock()
    monkeypatch.setattr(connections, "check_connection", check)
    transport = httpx.ASGITransport(app=app(signed_in=False))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        calls = [
            client.get(CONNECTIONS),
            client.post("/api/showing-bookings/calendar/connect", json={"origin": ORIGIN}),
            client.post(f"{CONNECTIONS}/{connection_id}/check"),
            client.post(f"{CONNECTIONS}/{connection_id}/disconnect"),
        ]
        for call in calls:
            assert (await call).status_code == 401
        # Google's callback needs no sign-in, and without a valid state it saves nothing.
        page = await client.get("/api/showing-bookings/calendar/callback?code=4/x")
    assert result_of(page) == "invalid"
    check.assert_not_called()
    assert google.requests == []
    (row,) = await rows()
    assert row.status == "connected"
