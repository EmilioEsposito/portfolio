"""The Google calendar adapter: free/busy reads, event writes and the showings calendar. No database.

Google is replaced by ``FakeGoogle`` behind ``httpx.MockTransport``. Its
answers are shaped as the Calendar API reference describes; none has been
checked against Google yet.
"""

from datetime import datetime

import httpx
import pytest

from api.src.showing_bookings import google_oauth
from api.src.showing_bookings.google_calendar import (
    NOT_SHARED,
    SHOWINGS_CALENDAR_NAME,
    GoogleCalendar,
    ShowingsCalendarError,
)
from api.src.showing_bookings.providers import BusyBlock, CalendarUnreadable, EventDraft, NotSent
from api.src.tests.showing_google import TEAM_ACCOUNT, TEAM_SUBJECT, Account, FakeGoogle, configure
from api.src.tests.showing_helpers import TEAM_EMAIL, at

JANE = "jane.doe@example.com"
SAM = "sam.lee@example.com"
WINDOW = (at(9), at(21))


class Tokens:
    """Access tokens from the fake Google, counting each ask."""

    def __init__(self, google: FakeGoogle, *, lost: bool = False):
        self.google = google
        self.lost = lost
        self.asks: list[bool] = []
        self.token = google._issue_access(Account(TEAM_SUBJECT, TEAM_ACCOUNT))

    async def access_token(self, *, fresh: bool = False) -> str:
        self.asks.append(fresh)
        if self.lost:
            raise google_oauth.GrantLost()
        if fresh:
            self.token = self.google._issue_access(Account(TEAM_SUBJECT, TEAM_ACCOUNT))
        return self.token


class Reads:
    def __init__(self):
        self.seen: list[tuple[bool, str | None]] = []

    async def __call__(self, ok: bool, problem: str | None) -> None:
        self.seen.append((ok, problem))


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    configure(monkeypatch)
    return FakeGoogle().install(monkeypatch)


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def calendar(google, reads) -> GoogleCalendar:
    return GoogleCalendar(Tokens(google), on_read=reads)


def showings_calendar(google: FakeGoogle) -> str:
    calendar_id = "c_showings@group.calendar.google.com"
    google.calendars[calendar_id] = {"summary": SHOWINGS_CALENDAR_NAME, "owner": TEAM_SUBJECT}
    return calendar_id


def draft(attendees=(TEAM_EMAIL,), **fields) -> EventDraft:
    values = {
        "summary": "Showing 320-03",
        "description": "Unit 320-03 at 100 Example Ave.",
        "location": "100 Example Ave",
        "starts_at": at(16),
        "ends_at": at(16, 30),
        "attendees": list(attendees),
    }
    return EventDraft(**{**values, **fields})


# Free/busy


@pytest.mark.asyncio
async def test_busy_times_come_from_free_busy_cut_to_the_window(google, calendar, reads):
    google.add_busy(JANE, at(16), at(17))
    google.add_busy(JANE, at(8), at(10))  # starts before the window
    google.add_busy(SAM, at(12), at(12, 30))

    busy = await calendar.busy_blocks([JANE, SAM], *WINDOW)

    assert busy == {
        JANE: [BusyBlock(at(9), at(10)), BusyBlock(at(16), at(17))],
        SAM: [BusyBlock(at(12), at(12, 30))],
    }
    (request,) = google.sent("POST", "/calendar/v3/freeBusy")
    assert google.body(request) == {
        "timeMin": at(9).isoformat(),
        "timeMax": at(21).isoformat(),
        "items": [{"id": JANE}, {"id": SAM}],
    }
    assert reads.seen == [(True, None)]


@pytest.mark.asyncio
async def test_an_event_marked_free_does_not_block(google, calendar):
    google.add_busy(JANE, at(16), at(17), transparency="transparent")
    google.add_busy(JANE, at(18), at(19))

    busy = await calendar.busy_blocks([JANE], *WINDOW)

    assert busy == {JANE: [BusyBlock(at(18), at(19))]}
    # Only free/busy is read, never what the events are.
    assert google.paths() == [("POST", "/calendar/v3/freeBusy")]


@pytest.mark.asyncio
async def test_a_calendar_google_cannot_read_is_unreadable_not_free(google, calendar, reads):
    google.add_busy(SAM, at(12), at(13))
    google.unreadable[JANE] = "notFound"

    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE, SAM], *WINDOW)

    assert unreadable.value.unreadable == {JANE: NOT_SHARED}
    # The calendars that were read still count.
    assert unreadable.value.busy == {SAM: [BusyBlock(at(12), at(13))]}
    assert reads.seen == [(False, f"{JANE}: {NOT_SHARED}")]


@pytest.mark.asyncio
async def test_any_other_calendar_error_is_unreadable_too(google, calendar):
    google.unreadable[JANE] = "internalError"
    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE], *WINDOW)
    assert "internalError" in unreadable.value.unreadable[JANE]


@pytest.mark.asyncio
async def test_a_calendar_left_out_of_the_answer_is_unreadable(google, calendar):
    google.forced[("POST", "/calendar/v3/freeBusy")] = httpx.Response(
        200, json={"kind": "calendar#freeBusy", "calendars": {SAM: {"busy": []}}}
    )
    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE, SAM], *WINDOW)
    assert set(unreadable.value.unreadable) == {JANE}
    assert unreadable.value.busy == {SAM: []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"error": {"code": 500, "message": "Backend Error"}}),
        httpx.Response(200, json={"kind": "calendar#freeBusy"}),
        httpx.Response(
            200, json={"calendars": {JANE: {"busy": [{"start": "soon", "end": "later"}]}}}
        ),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_a_failed_read_leaves_every_calendar_unreadable(google, calendar, reads, answer):
    google.forced[("POST", "/calendar/v3/freeBusy")] = answer
    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE], *WINDOW)
    assert set(unreadable.value.unreadable) == {JANE}
    assert reads.seen[-1][0] is False


@pytest.mark.asyncio
async def test_a_lost_grant_leaves_every_calendar_unreadable(google, reads):
    calendar = GoogleCalendar(Tokens(google, lost=True), on_read=reads)
    with pytest.raises(CalendarUnreadable) as unreadable:
        await calendar.busy_blocks([JANE, SAM], *WINDOW)
    assert set(unreadable.value.unreadable) == {JANE, SAM}
    assert "Reconnect" in unreadable.value.unreadable[JANE]
    assert google.requests == []


@pytest.mark.asyncio
async def test_an_expired_access_token_is_replaced_once(google, reads):
    tokens = Tokens(google)
    google.access_tokens.pop(tokens.token)  # expired at Google
    google.add_busy(JANE, at(16), at(17))

    busy = await GoogleCalendar(tokens, on_read=reads).busy_blocks([JANE], *WINDOW)

    assert busy == {JANE: [BusyBlock(at(16), at(17))]}
    assert tokens.asks == [False, True]


@pytest.mark.asyncio
async def test_no_calendars_means_no_request(google, calendar):
    assert await calendar.busy_blocks([], *WINDOW) == {}
    assert google.requests == []


@pytest.mark.asyncio
async def test_times_must_carry_a_timezone(google, calendar):
    with pytest.raises(ValueError, match="timezone"):
        await calendar.busy_blocks([JANE], at(9).replace(tzinfo=None), at(21))
    with pytest.raises(NotSent) as refused:
        await calendar.create_event(
            showings_calendar(google), draft(starts_at=datetime(2027, 4, 14, 16))
        )
    assert refused.value.reason == "rejected"
    assert google.requests == []


# Events


@pytest.mark.asyncio
async def test_a_new_event_invites_its_guests_by_email(google, calendar):
    calendar_id = showings_calendar(google)

    event_id = await calendar.create_event(calendar_id, draft(attendees=[TEAM_EMAIL, JANE]))

    assert event_id in google.events
    (request,) = google.sent("POST", f"/calendar/v3/calendars/{calendar_id}/events")
    assert request.url.params["sendUpdates"] == "all"
    assert google.body(request) == {
        "summary": "Showing 320-03",
        "description": "Unit 320-03 at 100 Example Ave.",
        "location": "100 Example Ave",
        "start": {"dateTime": "2027-04-14T16:00:00-04:00"},
        "end": {"dateTime": "2027-04-14T16:30:00-04:00"},
        "attendees": [{"email": TEAM_EMAIL}, {"email": JANE}],
        "guestsCanInviteOthers": False,
    }


@pytest.mark.asyncio
async def test_a_change_tells_the_guests(google, calendar):
    calendar_id = showings_calendar(google)
    event_id = await calendar.create_event(calendar_id, draft())

    await calendar.update_event(calendar_id, event_id, draft(starts_at=at(17), ends_at=at(17, 30)))

    (request,) = google.sent("PATCH", f"/calendar/v3/calendars/{calendar_id}/events/{event_id}")
    assert request.url.params["sendUpdates"] == "all"
    body = google.body(request)
    assert body["start"] == {"dateTime": "2027-04-14T17:00:00-04:00"}
    assert body["attendees"] == [{"email": TEAM_EMAIL}]
    assert google.events[event_id].body["start"] == body["start"]


@pytest.mark.asyncio
async def test_a_cancellation_tells_the_guests_and_can_be_repeated(google, calendar):
    calendar_id = showings_calendar(google)
    event_id = await calendar.create_event(calendar_id, draft())

    await calendar.cancel_event(calendar_id, event_id)
    await calendar.cancel_event(calendar_id, event_id)  # Google answers 410: already gone

    requests = google.sent("DELETE", f"/calendar/v3/calendars/{calendar_id}/events/{event_id}")
    assert len(requests) == 2
    assert all(request.url.params["sendUpdates"] == "all" for request in requests)
    assert google.events[event_id].deleted


@pytest.mark.asyncio
async def test_ids_are_sent_as_single_path_segments(google, calendar):
    calendar_id = "team#showings@group.calendar.google.com"
    google.calendars[calendar_id] = {"summary": SHOWINGS_CALENDAR_NAME}

    await calendar.create_event(calendar_id, draft())

    (request,) = google.requests
    assert b"/calendars/team%23showings%40group.calendar.google.com/events" in request.url.raw_path


def google_error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, reason, retryable",
    [
        (google_error(404, "notFound"), "no_such_event", False),
        (google_error(410, "deleted"), "no_such_event", False),
        (google_error(429, "rateLimitExceeded"), "rate_limited", True),
        (google_error(403, "userRateLimitExceeded"), "rate_limited", True),
        (google_error(403, "forbidden"), "rejected", False),
        (google_error(400, "badRequest"), "rejected", False),
        (httpx.ConnectError("refused"), "unavailable", True),
    ],
)
async def test_a_refused_change_says_whether_to_try_again(
    google, calendar, answer, reason, retryable
):
    calendar_id = showings_calendar(google)
    google.forced[("PATCH", f"/calendar/v3/calendars/{calendar_id}/events/testevent")] = answer

    with pytest.raises(NotSent) as refused:
        await calendar.update_event(calendar_id, "testevent", draft())

    assert (refused.value.reason, refused.value.retryable) == (reason, retryable)


@pytest.mark.asyncio
async def test_a_new_event_on_a_missing_calendar_says_so(google, calendar):
    with pytest.raises(NotSent) as refused:
        await calendar.create_event("c_gone@group.calendar.google.com", draft())
    assert refused.value.reason == "calendar_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        google_error(500, "backendError"),
        google_error(503, "backendError"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_a_change_that_may_have_been_made_is_not_reported_as_unsent(google, calendar, answer):
    calendar_id = showings_calendar(google)
    google.forced[("POST", f"/calendar/v3/calendars/{calendar_id}/events")] = answer

    with pytest.raises(Exception) as failure:
        await calendar.create_event(calendar_id, draft())

    # The outbox saves anything but NotSent as unknown and never retries it.
    assert not isinstance(failure.value, NotSent)


@pytest.mark.asyncio
async def test_a_lost_grant_refuses_changes(google):
    calendar = GoogleCalendar(Tokens(google, lost=True))
    with pytest.raises(NotSent) as refused:
        await calendar.create_event(showings_calendar(google), draft())
    assert (refused.value.reason, refused.value.retryable) == ("calendar_lost", False)
    assert google.requests == []


@pytest.mark.asyncio
async def test_google_out_of_reach_for_a_token_is_tried_again_later(google):
    class Unreachable(Tokens):
        async def access_token(self, *, fresh: bool = False) -> str:
            raise google_oauth.OAuthError("unreachable", "Could not reach Google (ConnectError).")

    with pytest.raises(NotSent) as refused:
        await GoogleCalendar(Unreachable(google)).create_event(showings_calendar(google), draft())
    assert (refused.value.reason, refused.value.retryable) == ("unavailable", True)


# The showings calendar


@pytest.mark.asyncio
async def test_the_showings_calendar_is_made_once_and_then_kept(google, calendar):
    calendar_id = await calendar.ensure_showings_calendar(None)

    (made,) = google.sent("POST", "/calendar/v3/calendars")
    assert google.body(made) == {"summary": "Sernia showings", "timeZone": "America/New_York"}
    assert await calendar.ensure_showings_calendar(calendar_id) == calendar_id
    assert len(google.sent("POST", "/calendar/v3/calendars")) == 1
    assert len(google.sent("GET", f"/calendar/v3/calendars/{calendar_id}")) == 1


@pytest.mark.asyncio
async def test_a_showings_calendar_deleted_in_google_is_made_again(google, calendar):
    calendar_id = await calendar.ensure_showings_calendar("c_deleted@group.calendar.google.com")
    assert calendar_id != "c_deleted@group.calendar.google.com"
    assert len(google.sent("POST", "/calendar/v3/calendars")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, answer",
    [
        ("/calendar/v3/calendars", google_error(403, "forbidden")),
        ("/calendar/v3/calendars", httpx.ConnectError("refused")),
        (
            "/calendar/v3/calendars/c_known@group.calendar.google.com",
            google_error(500, "backendError"),
        ),
    ],
)
async def test_a_showings_calendar_that_cannot_be_made_is_an_error(google, calendar, path, answer):
    method = "POST" if path.endswith("/calendars") else "GET"
    google.forced[(method, path)] = answer
    with pytest.raises(ShowingsCalendarError):
        await calendar.ensure_showings_calendar(
            None if method == "POST" else "c_known@group.calendar.google.com"
        )
