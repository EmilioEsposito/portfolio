"""The calendars the showing agent form offers, through the team's route.

Uses the test database. Google is ``FakeGoogle`` behind ``httpx.MockTransport``;
nothing reaches Google. Sign-in is replaced by a stand-in Sernia user, except in
the test of who may call.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import logfire
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from logfire.testing import capfire as capfire
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import (
    calendar_callback,
    calendar_candidates,
    connections,
    google_http,
    google_oauth,
    routes,
    token_crypto,
)
from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.showing_bookings.providers import CalendarList, ListedCalendar
from api.src.tests.showing_google import (
    ALL_SCOPES,
    CALENDAR_LIST,
    DIRECTORY,
    FREE_BUSY,
    ORIGIN,
    OTHER_TOKEN_KEY,
    PICKER_SCOPES,
    TEAM_ACCOUNT,
    TEAM_SUBJECT,
    TOKEN_KEY,
    Account,
    FakeGoogle,
    configure,
    people_error,
    person,
)
from api.src.tests.showing_helpers import clear_showing_data
from api.src.utils import clerk as clerk_module

PERSON = SimpleNamespace(id="user_test")
BASE = "/api/showing-bookings"
CANDIDATES = f"{BASE}/calendar/candidates"
CONNECTIONS = f"{BASE}/calendar/connections"
AGENTS = f"{BASE}/agents"
JANE = "jane.doe@example.com"
SAM = "sam.lee@example.com"
PEPPINO = "peppino@example.com"
SHARED = "c_shared@group.calendar.google.com"
HOLIDAYS = "en.usa#holiday@group.v.calendar.google.com"
BIRTHDAYS = "addressbook#contacts@group.v.calendar.google.com"
GONE = "gone@example.com"


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
    configure(monkeypatch)
    # The app logs at warning and up, so it never logs the addresses httpx calls.
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
    fake = FakeGoogle().install(monkeypatch)
    fake.people = [
        person("Team mailbox", TEAM_ACCOUNT),
        person("Sam Lee", SAM),
        person("Jane Doe", JANE, "jane@example.com"),
    ]
    fake.calendar_list = [
        {"id": TEAM_ACCOUNT, "summary": TEAM_ACCOUNT, "primary": True},
        {"id": SHARED, "summary": "Shared calendar"},
        {"id": PEPPINO, "summary": PEPPINO, "summaryOverride": "Peppino Example"},
        {"id": HOLIDAYS, "summary": "Holidays in United States"},
        {"id": BIRTHDAYS, "summary": "Birthdays"},
        # Jane's, also on the list, in another case.
        {"id": "Jane.Doe@Example.com", "summary": "Jane's calendar"},
    ]
    return fake


@pytest_asyncio.fixture
async def api():
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def connect(api, google: FakeGoogle, scopes: tuple[str, ...] = PICKER_SCOPES) -> str:
    """Connect the team account through Google's callback, and give the callback's result."""
    response = await api.post(f"{BASE}/calendar/connect", json={"origin": ORIGIN})
    consent = {
        key: values[0] for key, values in parse_qs(urlsplit(response.json()["url"]).query).items()
    }
    code = google.approve(
        Account(TEAM_SUBJECT, TEAM_ACCOUNT, scopes=scopes), redirect_uri=consent["redirect_uri"]
    )
    page = await api.get(
        f"{BASE}/calendar/callback", params={"code": code, "state": consent["state"]}
    )
    google.calendar_list.append({"id": (await row()).showings_calendar_id, "summary": "Showings"})
    return page.text.split("calendar=")[1].split("#")[0]


async def row() -> CalendarConnection:
    async with AsyncSessionFactory() as session:
        return await session.scalar(select(CalendarConnection))


async def add_agent(*calendars: str) -> int:
    async with AsyncSessionFactory() as session:
        agent = ShowingAgentRecord(name="Jane Doe", email=JANE, busy_calendar_ids=list(calendars))
        session.add(agent)
        await session.commit()
        return agent.id


async def candidates(api, agent_id: int | None = None) -> dict:
    response = await api.get(CANDIDATES, params={"agent_id": agent_id} if agent_id else None)
    assert response.status_code == 200, response.text
    return response.json()


def shown(body: dict) -> list[tuple[str, str, str, bool | None]]:
    return [
        (item["group"], item["name"], item["calendar_id"], item["readable"])
        for item in body["calendars"]
    ]


TEAM_ROWS = [("team", "Jane Doe", JANE, True), ("team", "Sam Lee", SAM, True)]
SHARED_ROWS = [
    ("shared", "Peppino Example", PEPPINO, True),
    ("shared", "Shared calendar", SHARED, True),
]


# What is offered


@pytest.mark.asyncio
async def test_the_team_and_the_shared_calendars_google_can_read_are_offered(api, google):
    assert await connect(api, google) == "connected"

    body = await candidates(api)

    # Each once, the team's group first; not the account's own calendar, the
    # app's showings calendar, or Google's own calendars.
    assert shown(body) == TEAM_ROWS + SHARED_ROWS
    assert body | {"calendars": None} == {
        "connection": "usable",
        "account": TEAM_ACCOUNT,
        "problem": None,
        "directory": "read",
        "calendar_list": "read",
        "check_failed": False,
        "truncated": False,
        "calendars": None,
    }
    (read,) = google.sent("POST", FREE_BUSY)
    asked = google.body(read)
    assert [item["id"] for item in asked["items"]] == [JANE, SAM, PEPPINO, SHARED]
    window = datetime.fromisoformat(asked["timeMax"]) - datetime.fromisoformat(asked["timeMin"])
    assert window == timedelta(days=1)


@pytest.mark.asyncio
async def test_a_calendar_free_busy_does_not_return_is_not_offered(api, google):
    await connect(api, google)
    google.unreadable[SAM] = google.unreadable[SHARED] = "notFound"

    body = await candidates(api)

    assert shown(body) == [TEAM_ROWS[0], SHARED_ROWS[0]]
    assert body["check_failed"] is False


@pytest.mark.asyncio
async def test_busy_times_are_never_in_the_answer(api, google):
    await connect(api, google)
    now = datetime.now(UTC)
    google.add_busy(JANE, now, now + timedelta(hours=1))

    body = await candidates(api)

    assert set(body["calendars"][0]) == {"calendar_id", "name", "group", "readable"}


@pytest.mark.asyncio
async def test_each_read_is_a_fresh_one(api, google):
    await connect(api, google)
    await candidates(api)
    google.people.append(person("New Person", "new.person@example.com"))
    google.calendar_list = []
    google.unreadable[JANE] = "notFound"

    body = await candidates(api)

    assert shown(body) == [
        ("team", "New Person", "new.person@example.com", True),
        TEAM_ROWS[1],
    ]
    for method, path in (("GET", DIRECTORY), ("GET", CALENDAR_LIST), ("POST", FREE_BUSY)):
        assert len(google.sent(method, path)) == 2


@pytest.mark.asyncio
async def test_many_candidates_are_all_checked(api, google):
    await connect(api, google)
    google.people = [person(f"P {n:03d}", f"p{n:03d}@example.com") for n in range(120)]
    google.unreadable["p077@example.com"] = "notFound"

    body = await candidates(api)

    team = [item for item in body["calendars"] if item["group"] == "team"]
    assert len(team) == 119 and "p077@example.com" not in {item["calendar_id"] for item in team}
    assert len(google.sent("POST", FREE_BUSY)) == 3


def test_each_calendar_is_offered_once_and_the_teams_group_wins():
    directory = CalendarList(
        "read",
        (
            ListedCalendar(JANE, "Jane Doe"),
            ListedCalendar(JANE.upper(), "Jane again"),
            ListedCalendar(TEAM_ACCOUNT, "Team"),
            ListedCalendar("has space@example.com", "Cannot be saved"),
            ListedCalendar(f"{'x' * 200}@example.com", "Too long to save"),
        ),
    )
    listed = CalendarList(
        "read",
        (
            ListedCalendar("Team@Example.com", "Own", own=True),
            ListedCalendar("other.name@example.com", "Own by another name", own=True),
            ListedCalendar("Jane.Doe@example.com", "Jane's"),
            ListedCalendar("B@example.com", "beta"),
            ListedCalendar("a@example.com", "Alpha"),
            ListedCalendar("C_SHOWINGS@group.calendar.google.com", "Showings"),
            ListedCalendar("X#Holiday@Group.V.Calendar.Google.com", "Holidays"),
        ),
    )

    found, more = calendar_candidates.offered(
        directory, listed, left_out=[TEAM_ACCOUNT, "c_showings@group.calendar.google.com"]
    )

    assert [(item.group, item.calendar_id) for item in found] == [
        ("team", JANE),
        ("shared", "a@example.com"),
        ("shared", "B@example.com"),
    ]
    assert more is False


def test_past_the_most_calendars_the_shared_ones_are_kept_and_the_answer_says_so():
    directory = CalendarList(
        "read", tuple(ListedCalendar(f"p{n:03d}@example.com", f"P{n:03d}") for n in range(499))
    )
    listed = CalendarList("read", (ListedCalendar("a@example.com", "A"),))
    found, more = calendar_candidates.offered(directory, listed, left_out=[])
    assert (len(found), more) == (500, False)

    listed = CalendarList("read", (*listed.entries, ListedCalendar("b@example.com", "B")))
    found, more = calendar_candidates.offered(directory, listed, left_out=[])
    assert (len(found), more) == (calendar_candidates.MAX_CANDIDATES, True)
    assert [item.calendar_id for item in found[-3:]] == [
        "p497@example.com",
        "a@example.com",
        "b@example.com",
    ]
    # A list Google had more of says so too.
    cut = CalendarList("read", listed.entries, more=True)
    assert calendar_candidates.offered(CalendarList("read"), cut, left_out=[])[1] is True


@pytest.mark.asyncio
async def test_more_calendars_than_are_read_are_said_to_be_cut(api, google):
    await connect(api, google)
    google.people = [person(f"P {n:03d}", f"p{n:03d}@example.com") for n in range(501)]
    body = await candidates(api)
    assert body["truncated"] is True
    assert len(body["calendars"]) == 500
    assert len(google.sent("POST", FREE_BUSY)) == 10
    assert [item["group"] for item in body["calendars"]].count("shared") == 3


# The calendars saved on the agent


@pytest.mark.asyncio
async def test_a_saved_calendar_that_cannot_be_read_stays_and_says_so(api, google):
    await connect(api, google)
    google.unreadable[SAM] = google.unreadable[GONE] = "notFound"
    agent = await add_agent(SAM, GONE, PEPPINO)

    body = await candidates(api, agent)

    assert shown(body) == [
        TEAM_ROWS[0],
        # Known by name, and in neither list.
        ("team", "Sam Lee", SAM, False),
        *SHARED_ROWS,
        ("saved", GONE, GONE, False),
    ]


@pytest.mark.asyncio
async def test_a_saved_calendar_in_neither_list_stays_by_its_address(api, google):
    await connect(api, google)
    showings = (await row()).showings_calendar_id
    # Left out of the lists on purpose, yet saved earlier.
    agent = await add_agent("elsewhere@example.com", TEAM_ACCOUNT, showings, HOLIDAYS)

    body = await candidates(api, agent)

    assert shown(body) == TEAM_ROWS + SHARED_ROWS + [
        ("saved", "elsewhere@example.com", "elsewhere@example.com", True),
        ("saved", TEAM_ACCOUNT, TEAM_ACCOUNT, True),
        ("saved", showings, showings, True),
        ("saved", HOLIDAYS, HOLIDAYS, True),
    ]


@pytest.mark.asyncio
async def test_a_saved_calendar_is_matched_in_any_case_and_kept_as_saved(api, google):
    await connect(api, google)
    agent = await add_agent("Jane.Doe@Example.com")

    body = await candidates(api, agent)

    assert shown(body)[0] == ("team", "Jane Doe", "Jane.Doe@Example.com", True)
    assert len(body["calendars"]) == 4
    (read,) = google.sent("POST", FREE_BUSY)
    # Asked about first, as availability reads it.
    assert google.body(read)["items"][0] == {"id": "Jane.Doe@Example.com"}


@pytest.mark.asyncio
async def test_saved_calendars_are_checked_even_past_the_most_calendars(api, google):
    await connect(api, google)
    google.people = [person(f"P {n:03d}", f"p{n:03d}@example.com") for n in range(501)]
    agent = await add_agent("p499@example.com", "elsewhere@example.com")
    body = await candidates(api, agent)
    # p499 is past the first 497 of the directory that fit beside the 3 shared ones.
    assert shown(body)[-2:] == [
        ("saved", "p499@example.com", "p499@example.com", True),
        ("saved", "elsewhere@example.com", "elsewhere@example.com", True),
    ]


async def lose(google):
    google.revoke_everything()
    connections.forget_access_tokens()


async def lose_with_a_token_held(google):
    google.revoke_everything()


async def google_down(google):
    for method, path in (("GET", DIRECTORY), ("GET", CALENDAR_LIST), ("POST", FREE_BUSY)):
        google.forced[(method, path)] = httpx.Response(503)


async def token_endpoint_down(google):
    connections.forget_access_tokens()
    google.forced[("POST", "/token")] = httpx.ConnectError("refused")


async def another_key(google):
    async with AsyncSessionFactory() as session, session.begin():
        ciphertext = Fernet(OTHER_TOKEN_KEY).encrypt(b"1//stored-refresh").decode()
        await session.execute(update(CalendarConnection).values(encrypted_refresh_token=ciphertext))


async def revoked(google):
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(CalendarConnection).values(status="revoked", encrypted_refresh_token=None)
        )


async def nothing(google):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scopes, trouble",
    [
        (PICKER_SCOPES, nothing),
        (ALL_SCOPES, nothing),
        (PICKER_SCOPES, lose),
        (PICKER_SCOPES, lose_with_a_token_held),
        (PICKER_SCOPES, google_down),
        (PICKER_SCOPES, token_endpoint_down),
        (PICKER_SCOPES, another_key),
        (PICKER_SCOPES, revoked),
    ],
)
async def test_every_saved_calendar_is_in_the_answer_whatever_happens_and_a_save_keeps_it(
    api, google, scopes, trouble
):
    await connect(api, google, scopes)
    google.unreadable[GONE] = "notFound"
    saved = [SAM, GONE, "Elsewhere@example.com", SHARED, HOLIDAYS]
    agent = await add_agent(*saved)
    await trouble(google)

    body = await candidates(api, agent)
    in_the_answer = [item["calendar_id"] for item in body["calendars"]]
    assert set(saved) <= set(in_the_answer)

    # The form sends back the ticks it was given, and nothing is lost.
    response = await api.put(
        f"{AGENTS}/{agent}",
        json={
            "name": "Jane Doe",
            "email": JANE,
            "busy_calendar_ids": [item for item in in_the_answer if item in saved],
        },
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["busy_calendar_ids"]) == set(saved)


@pytest.mark.asyncio
async def test_reading_the_candidates_changes_nothing_saved(api, google):
    await connect(api, google)
    google.unreadable[GONE] = "notFound"
    agent = await add_agent(GONE)
    before = await row()

    await candidates(api, agent)

    after = await row()
    assert (after.last_read_at, after.last_read_ok, after.last_error) == (None, None, None)
    assert after.updated_at == before.updated_at
    assert {method for method, path in google.paths() if path != "/token"} <= {"GET", "POST"}
    # Connecting made the showings calendar; nothing else is written to Google.
    assert [r for r in google.paths() if r[0] == "POST"] == [
        ("POST", "/token"),
        ("POST", "/calendar/v3/calendars"),
        ("POST", "/token"),
        ("POST", FREE_BUSY),
    ]
    async with AsyncSessionFactory() as session:
        assert (await session.get(ShowingAgentRecord, agent)).busy_calendar_ids == [GONE]


@pytest.mark.asyncio
async def test_an_unknown_agent_is_not_found(api, google):
    await connect(api, google)
    sent = len(google.requests)
    assert (await api.get(CANDIDATES, params={"agent_id": 999999})).status_code == 404
    assert (await api.get(CANDIDATES, params={"agent_id": "nope"})).status_code == 422
    assert len(google.requests) == sent


# Without a usable connection


@pytest.mark.asyncio
async def test_with_no_account_connected_the_saved_calendars_are_unchecked(api, google):
    agent = await add_agent(JANE, GONE)

    body = await candidates(api, agent)

    assert (body["connection"], body["account"], body["problem"]) == ("none", None, None)
    assert (body["directory"], body["calendar_list"]) == (None, None)
    assert shown(body) == [("saved", JANE, JANE, None), ("saved", GONE, GONE, None)]
    assert shown(await candidates(api)) == []
    assert google.requests == []


@pytest.mark.asyncio
async def test_a_connection_this_environment_cannot_use_says_why(api, google, monkeypatch):
    await connect(api, google)
    monkeypatch.delenv("SHOWING_TOKEN_KEY")
    sent = len(google.requests)

    body = await candidates(api, await add_agent(JANE))

    assert (body["connection"], body["account"]) == ("unusable", TEAM_ACCOUNT)
    assert "SHOWING_TOKEN_KEY is not set here" in body["problem"]
    assert shown(body) == [("saved", JANE, JANE, None)]
    assert len(google.requests) == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [False, True])
async def test_a_grant_google_no_longer_accepts_is_lost_and_the_team_is_told(
    api, google, capfire, held
):
    await connect(api, google)
    if held:
        await candidates(api)  # an access token is held in memory
    await lose_with_a_token_held(google)

    body = await candidates(api, await add_agent(JANE))

    assert (body["connection"], body["account"]) == ("lost", TEAM_ACCOUNT)
    assert shown(body) == [("saved", JANE, JANE, None)]
    assert (await row()).status == "lost"
    lost = [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["name"] == "showing bookings: calendar connection lost"
    ]
    assert [span["attributes"]["logfire.level_num"] for span in lost] == [17]  # error: alerted
    # Known to be lost from here on, so Google is not asked again.
    sent = len(google.requests)
    again = await candidates(api)
    assert (again["connection"], again["account"]) == ("lost", TEAM_ACCOUNT)
    assert len(google.requests) == sent


@pytest.mark.asyncio
async def test_the_candidates_are_read_through_the_teams_connection(api, google):
    """The oldest connected account, as for an agent with no connection of their own."""
    await connect(api, google)
    async with AsyncSessionFactory() as session, session.begin():
        session.add(
            CalendarConnection(
                google_email="later@example.com",
                google_subject="later",
                scopes=list(PICKER_SCOPES),
                encrypted_refresh_token=Fernet(TOKEN_KEY).encrypt(b"1//later").decode(),
                showings_calendar_id=SHARED,
                connected_by="pat@serniacapital.com",
                connected_at=datetime.now(UTC) + timedelta(days=1),
            )
        )

    body = await candidates(api)

    assert body["account"] == TEAM_ACCOUNT
    # The other account's showings calendar is left out too.
    assert SHARED not in {item["calendar_id"] for item in body["calendars"]}


# Permissions


@pytest.mark.asyncio
async def test_a_connection_without_the_new_permissions_still_connects_and_reads(api, google):
    """As it was before the picker: connecting, Check now and availability's reads."""
    assert await connect(api, google, ALL_SCOPES) == "connected"
    connection = await row()
    assert set(connection.scopes) == set(ALL_SCOPES)

    checked = await api.post(f"{CONNECTIONS}/{connection.id}/check")
    assert checked.json()["last_read_ok"] is True
    now = datetime.now(UTC)
    google.add_busy(JANE, now, now + timedelta(hours=1))
    busy = await connections.calendar_for_connection(await row()).busy_blocks(
        [JANE], now, now + timedelta(days=1)
    )
    assert len(busy[JANE]) == 1
    assert len(google.sent("POST", "/calendar/v3/calendars")) == 1

    agent = await add_agent(JANE, GONE)
    google.unreadable[GONE] = "notFound"
    body = await candidates(api, agent)

    assert (body["connection"], body["directory"], body["calendar_list"]) == (
        "usable",
        "not_granted",
        "not_granted",
    )
    # What can still be checked is.
    assert shown(body) == [("saved", JANE, JANE, True), ("saved", GONE, GONE, False)]
    # Decided from the saved permissions: neither list is asked for.
    assert google.sent("GET", DIRECTORY) == google.sent("GET", CALENDAR_LIST) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "granted, rows",
    [
        (google_oauth.DIRECTORY_SCOPE, TEAM_ROWS),
        (
            google_oauth.CALENDAR_LIST_SCOPE,
            [("shared", "Jane's calendar", "Jane.Doe@Example.com", True), *SHARED_ROWS],
        ),
    ],
)
async def test_with_one_permission_its_list_is_still_shown(api, google, granted, rows):
    assert await connect(api, google, (*ALL_SCOPES, granted)) == "connected"

    body = await candidates(api)

    assert shown(body) == rows
    assert {"directory": body["directory"], "calendar_list": body["calendar_list"]} == {
        "directory": "read" if granted == google_oauth.DIRECTORY_SCOPE else "not_granted",
        "calendar_list": "read" if granted == google_oauth.CALENDAR_LIST_SCOPE else "not_granted",
    }


@pytest.mark.asyncio
async def test_a_permission_google_says_is_missing_is_believed_over_the_saved_ones(api, google):
    await connect(api, google, ALL_SCOPES)
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(update(CalendarConnection).values(scopes=list(PICKER_SCOPES)))

    body = await candidates(api, await add_agent(JANE))

    assert (body["directory"], body["calendar_list"]) == ("not_granted", "not_granted")
    assert len(google.sent("GET", DIRECTORY)) == len(google.sent("GET", CALENDAR_LIST)) == 1
    assert shown(body) == [("saved", JANE, JANE, True)]


@pytest.mark.asyncio
async def test_the_connection_says_when_a_reconnect_would_add_permissions(api, google):
    await connect(api, google, ALL_SCOPES)

    async def said() -> list[bool]:
        listed = (await api.get(CONNECTIONS)).json()["connections"]
        return [item["reconnect_adds_permissions"] for item in listed]

    assert await said() == [True]
    connection = await row()
    checked = (await api.post(f"{CONNECTIONS}/{connection.id}/check")).json()
    assert checked["reconnect_adds_permissions"] is True

    # Reconnected with one of the two left unticked, then with both.
    await connect(api, google, (*ALL_SCOPES, google_oauth.DIRECTORY_SCOPE))
    assert await said() == [True]
    await connect(api, google, PICKER_SCOPES)
    assert await said() == [False]
    assert (await row()).id == connection.id

    disconnected = (await api.post(f"{CONNECTIONS}/{connection.id}/disconnect")).json()
    assert disconnected["reconnect_adds_permissions"] is False


# Google's trouble


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, status",
    [
        (people_error(403, "SERVICE_DISABLED", "People API has not been used"), "api_off"),
        (people_error(403, None, "Admin has disabled external directory sharing"), "sharing_off"),
        (people_error(403, None, "The caller does not have permission"), "refused"),
        (people_error(429, "RATE_LIMIT_EXCEEDED", "Quota"), "failed"),
        (httpx.Response(503), "failed"),
        (httpx.ReadTimeout("slow"), "failed"),
    ],
)
async def test_without_the_directory_the_shared_calendars_are_still_shown(
    api, google, answer, status
):
    await connect(api, google)
    google.forced[("GET", DIRECTORY)] = answer
    agent = await add_agent(SAM)

    body = await candidates(api, agent)

    assert (body["directory"], body["calendar_list"], body["check_failed"]) == (
        status,
        "read",
        False,
    )
    assert shown(body) == [
        # On the account's list too, so it is found there.
        ("shared", "Jane's calendar", "Jane.Doe@Example.com", True),
        *SHARED_ROWS,
        # Still readable, and known by its address alone.
        ("saved", SAM, SAM, True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, status",
    [
        (FakeGoogle._error(403, "accessNotConfigured", "Calendar API off"), "api_off"),
        (FakeGoogle._error(403, "forbidden", "Forbidden"), "refused"),
        (FakeGoogle._error(500, "backendError", "Backend Error"), "failed"),
    ],
)
async def test_without_the_calendar_list_the_team_is_still_shown(api, google, answer, status):
    await connect(api, google)
    google.forced[("GET", CALENDAR_LIST)] = answer

    body = await candidates(api)

    assert (body["directory"], body["calendar_list"]) == ("read", status)
    assert shown(body) == TEAM_ROWS


@pytest.mark.asyncio
async def test_a_failed_check_is_said_and_trying_again_reads_afresh(api, google):
    await connect(api, google)
    agent = await add_agent(SAM)
    google.forced[("POST", FREE_BUSY)] = [httpx.Response(503)]

    body = await candidates(api, agent)

    assert body["check_failed"] is True
    # Nothing unchecked is offered; the saved one stays.
    assert shown(body) == [("team", "Sam Lee", SAM, None)]

    body = await candidates(api, agent)
    assert body["check_failed"] is False and shown(body) == TEAM_ROWS + SHARED_ROWS


@pytest.mark.asyncio
async def test_no_access_token_to_be_had_is_googles_trouble_not_a_lost_grant(api, google):
    await connect(api, google)
    await token_endpoint_down(google)

    body = await candidates(api, await add_agent(SAM))

    assert (body["connection"], body["directory"], body["calendar_list"]) == (
        "usable",
        "failed",
        "failed",
    )
    assert body["check_failed"] is True
    assert shown(body) == [("saved", SAM, SAM, None)]
    assert (await row()).status == "connected"


@pytest.mark.asyncio
async def test_an_expired_access_token_is_replaced_and_the_read_goes_on(api, google):
    await connect(api, google)
    await candidates(api)
    google.access_tokens.clear()  # expired at Google, still held here

    body = await candidates(api)

    assert shown(body) == TEAM_ROWS + SHARED_ROWS
    assert len(google.sent("GET", DIRECTORY)) == 3


# What reaches the logs, and who may call


@pytest.mark.asyncio
async def test_no_address_name_or_token_reaches_the_logs(google, capfire, monkeypatch):
    """With the HTTP client traced as the app traces it, and lists long enough for
    Google's page tokens, which can hold an address."""
    from api.index import app as real_app

    def traced_client() -> httpx.AsyncClient:
        made = FakeGoogle.client(google)
        logfire.instrument_httpx(made)
        return made

    real_app.dependency_overrides[routes.verify_serniacapital_user] = lambda: PERSON
    monkeypatch.setattr(google_http, "client", traced_client)
    google.page_limit = 2
    google.unreadable[GONE] = "notFound"
    try:
        transport = httpx.ASGITransport(app=real_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await connect(client, google)
            capfire.exporter.clear()
            agent = await add_agent(SAM, GONE)
            body = await candidates(client, agent)
            google.forced[("GET", DIRECTORY)] = people_error(403, None, f"Not for {JANE}")
            google.forced[("POST", FREE_BUSY)] = httpx.Response(500, text=f"Trouble with {JANE}")
            refused = await candidates(client, agent)
    finally:
        real_app.dependency_overrides.pop(routes.verify_serniacapital_user, None)

    assert len(body["calendars"]) == 5
    assert (refused["directory"], refused["check_failed"]) == ("refused", True)
    assert len(google.sent("GET", CALENDAR_LIST)) > 4 and len(google.sent("GET", DIRECTORY)) == 3
    spans = [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if not str(
            span["attributes"].get("http.url") or span["attributes"].get("url.full") or ""
        ).startswith("http://test/")
    ]
    exported = json.dumps(spans + capfire.log_exporter.exported_logs_as_dicts(), default=str)
    # The reads are traced, and the read's outcome is logged in counts.
    assert DIRECTORY in exported and "calendarList" in exported and "freeBusy" in exported
    assert "showing bookings: calendar candidates read" in exported
    private = [JANE, SAM, GONE, PEPPINO, SHARED, TEAM_ACCOUNT, "Sam Lee", "Peppino", "pageToken"]
    assert not [text for text in private if text.casefold() in exported.casefold()]
    assert not [secret for secret in google.secrets if secret in exported]
    # Nor does Google's own wording reach the team.
    assert JANE not in json.dumps({k: v for k, v in refused.items() if k != "calendars"})


@pytest.mark.asyncio
async def test_without_a_sign_in_the_candidates_are_refused(google, monkeypatch):
    monkeypatch.setattr(
        clerk_module,
        "clerk_client",
        SimpleNamespace(
            authenticate_request=lambda request, options: SimpleNamespace(is_signed_in=False)
        ),
    )
    read = AsyncMock()
    monkeypatch.setattr(calendar_candidates, "candidates", read)
    transport = httpx.ASGITransport(app=app(signed_in=False))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(CANDIDATES)).status_code == 401
        assert (await client.get(CANDIDATES, params={"agent_id": 1})).status_code == 401
    read.assert_not_called()
    assert google.requests == []


def test_the_address_of_the_route_takes_an_agent_id_and_nothing_else():
    route = next(
        route
        for route in routes.router.routes
        if route.path == "/showing-bookings/calendar/candidates"
    )
    assert route.methods == {"GET"}
    assert [param.name for param in route.dependant.query_params] == ["agent_id"]
    assert route.dependant.path_params == []


def test_the_token_key_still_reads_what_it_wrote():
    """A guard on the fixtures: the stored token above is one this environment reads."""
    assert token_crypto.decrypt(Fernet(TOKEN_KEY).encrypt(b"1//x").decode()) == "1//x"
