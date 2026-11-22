"""What the calendar picker reads from Google: the two lists, and which calendars free/busy returns.

No database. Google is replaced by ``FakeGoogle`` behind ``httpx.MockTransport``.
Its answers are shaped as Google's reference describes; none has been checked
against Google yet.
"""

import httpx
import pytest

from api.src.showing_bookings import google_http, google_lists, google_oauth
from api.src.showing_bookings.google_calendar import GoogleCalendar
from api.src.showing_bookings.google_lists import GoogleLists
from api.src.showing_bookings.providers import CalendarList, CalendarUnreadable, ListedCalendar
from api.src.tests.showing_google import (
    CALENDAR_LIST,
    DIRECTORY,
    FREE_BUSY,
    PICKER_SCOPES,
    TEAM_ACCOUNT,
    TEAM_SUBJECT,
    Account,
    FakeGoogle,
    configure,
    people_error,
    person,
)
from api.src.tests.showing_helpers import at

JANE = "jane.doe@example.com"
WINDOW = (at(9), at(21))
TEAM = Account(TEAM_SUBJECT, TEAM_ACCOUNT, scopes=PICKER_SCOPES)


class Tokens:
    """Access tokens from the fake Google, counting each ask."""

    def __init__(self, google: FakeGoogle, account: Account = TEAM, *, lost: bool = False):
        self.google = google
        self.account = account
        self.lost = lost
        self.asks: list[bool] = []
        self.token = google._issue_access(account)

    async def access_token(self, *, fresh: bool = False) -> str:
        self.asks.append(fresh)
        if self.lost:
            raise google_oauth.GrantLost()
        if fresh:
            self.token = self.google._issue_access(self.account)
        return self.token


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    configure(monkeypatch)
    return FakeGoogle().install(monkeypatch)


@pytest.fixture
def lists(google) -> GoogleLists:
    return GoogleLists(Tokens(google))


def entry(number: int) -> dict:
    return {
        "id": f"c{number:04d}@group.calendar.google.com",
        "summary": f"Calendar {number:04d}",
        "accessRole": "freeBusyReader",
    }


def somebody(number: int) -> dict:
    return person(f"Person {number:04d}", f"person{number:04d}@example.com")


# The calendar list


@pytest.mark.asyncio
async def test_the_whole_calendar_list_is_read_page_by_page(google, lists):
    google.calendar_list = [entry(number) for number in range(430)]

    found = await lists.calendar_list()

    assert found == CalendarList(
        "read",
        tuple(ListedCalendar(item["id"], item["summary"]) for item in google.calendar_list),
    )
    pages = google.sent("GET", CALENDAR_LIST)
    assert [dict(page.url.params) for page in pages] == [
        {"maxResults": "250", "showHidden": "true"},
        {
            "maxResults": "250",
            "showHidden": "true",
            "pageToken": f"after-{google.calendar_list[249]['id']}",
        },
    ]
    assert all(page.content == b"" for page in pages)


@pytest.mark.asyncio
async def test_a_calendar_is_named_as_the_account_named_it(google, lists):
    google.calendar_list = [
        {"id": TEAM_ACCOUNT, "summary": TEAM_ACCOUNT, "primary": True},
        {"id": JANE, "summary": JANE, "summaryOverride": "Jane Doe (personal)"},
        {"id": "peppino@example.com", "summary": "Peppino's shifts", "hidden": True},
        {"id": "nameless@example.com"},
        {"id": "gone@example.com", "summary": "Gone", "deleted": True},
        {"summary": "No id"},
    ]

    assert (await lists.calendar_list()).entries == (
        ListedCalendar(TEAM_ACCOUNT, TEAM_ACCOUNT, own=True),
        ListedCalendar(JANE, "Jane Doe (personal)"),
        ListedCalendar("peppino@example.com", "Peppino's shifts"),
        ListedCalendar("nameless@example.com", "nameless@example.com"),
    )


# The directory


@pytest.mark.asyncio
async def test_the_whole_directory_is_read_page_by_page(google, lists):
    google.people = [somebody(number) for number in range(7)]
    google.page_limit = 3

    found = await lists.directory()

    assert found == CalendarList(
        "read",
        tuple(
            ListedCalendar(f"person{number:04d}@example.com", f"Person {number:04d}")
            for number in range(7)
        ),
    )
    asked = {
        "readMask": "names,emailAddresses",
        "sources": "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
        "pageSize": "1000",
    }
    assert [dict(page.url.params) for page in google.sent("GET", DIRECTORY)] == [
        asked,
        {**asked, "pageToken": "after-person0002@example.com"},
        {**asked, "pageToken": "after-person0005@example.com"},
    ]


@pytest.mark.asyncio
async def test_a_person_is_listed_by_their_primary_address_only(google, lists):
    alias_first = person("Sam Lee", "sam@example.com", "sam.lee@example.com", primary=False)
    alias_first["emailAddresses"][1]["metadata"]["primary"] = True
    google.people = [
        person("Jane Doe", JANE, "jane@example.com", "j.doe@example.com"),
        alias_first,
        person("Peppino Example", "peppino@example.com", sourcePrimary=True),
        person("Unmarked", "first@example.com", "second@example.com", verified=True),
        person("No Email"),
        person(None, "nameless@example.com"),
        {"resourceName": "people/empty", "emailAddresses": [{"metadata": {"primary": True}}]},
    ]

    assert (await lists.directory()).entries == (
        ListedCalendar(JANE, "Jane Doe"),
        ListedCalendar("sam.lee@example.com", "Sam Lee"),
        ListedCalendar("peppino@example.com", "Peppino Example"),
        ListedCalendar("first@example.com", "Unmarked"),
        ListedCalendar("nameless@example.com", "nameless@example.com"),
    )


# Trouble


@pytest.mark.asyncio
async def test_an_expired_access_token_is_replaced_once_for_each_list(google):
    google.calendar_list, google.people = [entry(1)], [somebody(1)]
    tokens = Tokens(google)
    lists = GoogleLists(tokens)

    google.access_tokens.pop(tokens.token)
    assert (await lists.calendar_list()).status == "read"
    google.access_tokens.pop(tokens.token)
    assert (await lists.directory()).status == "read"

    assert tokens.asks == [False, True, False, True]


@pytest.mark.asyncio
async def test_a_permission_not_granted_is_said(google):
    lists = GoogleLists(Tokens(google, Account(TEAM_SUBJECT, TEAM_ACCOUNT)))
    assert await lists.calendar_list() == CalendarList("not_granted")
    assert await lists.directory() == CalendarList("not_granted")


def calendar_error(status: int, reason: str) -> httpx.Response:
    return FakeGoogle._error(status, reason, "Not told to anyone")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, status",
    [
        (calendar_error(403, "insufficientPermissions"), "not_granted"),
        (calendar_error(403, "accessNotConfigured"), "api_off"),
        (calendar_error(403, "rateLimitExceeded"), "failed"),
        (calendar_error(403, "userRateLimitExceeded"), "failed"),
        (calendar_error(403, "quotaExceeded"), "failed"),
        (calendar_error(403, "forbidden"), "refused"),
        (calendar_error(400, "invalid"), "refused"),
        (calendar_error(404, "notFound"), "refused"),
        (calendar_error(401, "authError"), "failed"),
        (calendar_error(429, "rateLimitExceeded"), "failed"),
        (calendar_error(500, "backendError"), "failed"),
        (httpx.Response(503, text="Service Unavailable"), "failed"),
        (httpx.Response(403, text="<html>Forbidden</html>"), "refused"),
        (httpx.Response(200, text="<html>not JSON</html>"), "failed"),
        (httpx.Response(200, json=["not", "an", "object"]), "failed"),
        (httpx.ConnectError("refused"), "failed"),
        (httpx.ReadTimeout("slow"), "failed"),
    ],
)
async def test_why_the_calendar_list_was_not_read(google, lists, answer, status):
    google.calendar_list = [entry(1)]
    google.forced[("GET", CALENDAR_LIST)] = answer
    assert await lists.calendar_list() == CalendarList(status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, status",
    [
        (people_error(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "Insufficient"), "not_granted"),
        (people_error(403, "SERVICE_DISABLED", "People API has not been used"), "api_off"),
        (
            people_error(403, None, "The domain admin has disabled external directory sharing."),
            "sharing_off",
        ),
        (people_error(403, None, "The caller does not have permission"), "refused"),
        (people_error(403, "USER_PROJECT_DENIED", "Denied"), "refused"),
        (people_error(400, None, "Must be a Workspace domain user"), "refused"),
        (people_error(403, "RATE_LIMIT_EXCEEDED", "Quota"), "failed"),
        (people_error(429, "RATE_LIMIT_EXCEEDED", "Quota"), "failed"),
        (people_error(401, None, "Request had invalid authentication credentials."), "failed"),
        (people_error(500, None, "Internal"), "failed"),
        (people_error(503, None, "Unavailable"), "failed"),
        (httpx.ConnectTimeout("slow"), "failed"),
    ],
)
async def test_why_the_directory_was_not_read(google, lists, answer, status):
    google.people = [somebody(1)]
    google.forced[("GET", DIRECTORY)] = answer
    assert await lists.directory() == CalendarList(status)


@pytest.mark.asyncio
async def test_what_was_read_before_a_failed_page_is_kept_and_said_to_be_part(google, lists):
    google.calendar_list = [entry(number) for number in range(4)]
    google.page_limit = 2
    google.forced[("GET", CALENDAR_LIST)] = [None, httpx.Response(503)]

    found = await lists.calendar_list()

    assert found.status == "failed"
    assert [item.calendar_id for item in found.entries] == [entry(0)["id"], entry(1)["id"]]


@pytest.mark.asyncio
async def test_a_lost_grant_is_raised_not_reported_as_a_failed_list(google):
    lists = GoogleLists(Tokens(google, lost=True))
    for read in (lists.calendar_list, lists.directory):
        with pytest.raises(google_oauth.GrantLost):
            await read()
    assert google.requests == []


@pytest.mark.asyncio
async def test_no_token_to_be_had_is_a_failed_list(google):
    class NoToken:
        async def access_token(self, *, fresh: bool = False) -> str:
            raise google_oauth.OAuthError("unreachable", "Could not reach Google.")

    assert await GoogleLists(NoToken()).directory() == CalendarList("failed")


# Limits


@pytest.mark.asyncio
async def test_reading_stops_at_the_most_entries_and_says_there_were_more(google, lists):
    google.people = [somebody(number) for number in range(google_lists.MAX_ENTRIES + 1)]
    found = await lists.directory()
    assert (len(found.entries), found.more, found.status) == (500, True, "read")
    assert len(google.sent("GET", DIRECTORY)) == 1

    google.people.pop()
    found = await lists.directory()
    assert (len(found.entries), found.more) == (500, False)

    # Two full pages and a third left unread.
    google.calendar_list = [entry(number) for number in range(google_lists.MAX_ENTRIES + 250)]
    found = await lists.calendar_list()
    assert (len(found.entries), found.more) == (500, True)
    assert len(google.sent("GET", CALENDAR_LIST)) == 2


@pytest.mark.asyncio
async def test_reading_stops_at_the_most_pages_and_says_there_were_more(google, lists):
    google.calendar_list = [entry(number) for number in range(30)]
    google.page_limit = 1
    found = await lists.calendar_list()
    assert (len(found.entries), found.more) == (google_lists.MAX_PAGES, True)
    assert len(google.sent("GET", CALENDAR_LIST)) == google_lists.MAX_PAGES


# Which calendars free/busy returns


class Reads:
    def __init__(self):
        self.seen: list[tuple[bool, str | None]] = []

    async def __call__(self, ok: bool, problem: str | None) -> None:
        self.seen.append((ok, problem))


@pytest.mark.asyncio
async def test_more_than_fifty_calendars_are_asked_about_fifty_at_a_time(google):
    ids = [f"c{number:04d}@group.calendar.google.com" for number in range(120)]
    google.unreadable[ids[3]] = google.unreadable[ids[110]] = "notFound"
    reads = Reads()

    found = await GoogleCalendar(Tokens(google), on_read=reads).readable(ids, *WINDOW)

    assert found == {calendar_id: calendar_id not in (ids[3], ids[110]) for calendar_id in ids}
    asked = [google.body(request) for request in google.sent("POST", FREE_BUSY)]
    assert [[item["id"] for item in body["items"]] for body in asked] == [
        ids[:50],
        ids[50:100],
        ids[100:],
    ]
    assert {body["calendarExpansionMax"] for body in asked} == {50}
    # A calendar nobody shared says nothing about the connection.
    assert reads.seen == []


@pytest.mark.asyncio
async def test_a_calendar_reads_as_readable_exactly_when_availability_reads_it(google):
    busy, no_access, left_out, garbled = (
        JANE,
        "no.access@example.com",
        "left.out@example.com",
        "garbled@example.com",
    )
    google.forced[("POST", FREE_BUSY)] = httpx.Response(
        200,
        json={
            "calendars": {
                "Jane.Doe@Example.com": {
                    "busy": [{"start": "2026-11-16T14:00:00Z", "end": "2026-11-16T15:00:00Z"}]
                },
                no_access: {"errors": [{"domain": "global", "reason": "notFound"}]},
                garbled: {"busy": [{"start": "soon", "end": "later"}]},
            }
        },
    )
    calendar = GoogleCalendar(Tokens(google))
    ids = [busy, no_access, left_out, garbled]

    found = await calendar.readable(ids, *WINDOW)

    assert found == {busy: True, no_access: False, left_out: False, garbled: False}
    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks(ids, *WINDOW)
    assert set(unreadable.value.unreadable) == {key for key, ok in found.items() if not ok}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503),
        httpx.Response(200, json={"kind": "calendar#freeBusy"}),
        FakeGoogle._error(429, "rateLimitExceeded", "Rate Limit Exceeded"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_a_failed_read_leaves_its_calendars_and_the_rest_unanswered(google, answer):
    ids = [f"c{number:04d}@group.calendar.google.com" for number in range(120)]
    google.forced[("POST", FREE_BUSY)] = [None, answer, None]

    found = await GoogleCalendar(Tokens(google)).readable(ids, *WINDOW)

    assert found == dict.fromkeys(ids[:50], True)
    assert len(google.sent("POST", FREE_BUSY)) == 2


@pytest.mark.asyncio
async def test_asking_about_calendars_replaces_an_expired_token_and_raises_a_lost_grant(google):
    tokens = Tokens(google)
    google.access_tokens.pop(tokens.token)
    assert await GoogleCalendar(tokens).readable([JANE], *WINDOW) == {JANE: True}
    assert tokens.asks == [False, True]

    with pytest.raises(google_oauth.GrantLost):
        await GoogleCalendar(Tokens(google, lost=True)).readable([JANE], *WINDOW)
    assert await GoogleCalendar(Tokens(google)).readable([], *WINDOW) == {}


def test_the_real_client_is_still_the_only_way_out(monkeypatch):
    """The lists go through ``google_http.call``, which opens ``google_http.client``."""
    with pytest.raises(google_http.LiveGoogleInTests):
        google_http.client()
