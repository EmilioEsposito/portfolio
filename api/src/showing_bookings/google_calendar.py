"""Google Calendar as a ``CalendarSource``: free/busy reads and the showings calendar's events.

Written from the Calendar API v3 reference
(https://developers.google.com/workspace/calendar/api/v3/reference):

- free/busy: ``POST /freeBusy`` with ``timeMin``, ``timeMax`` and ``items``.
  Each calendar in the answer has ``busy`` periods, or ``errors`` when Google
  could not read it. Events marked free are not busy. A calendar with errors,
  or left out of the answer, is unreadable: never counted as free.
- events: ``POST``, ``PATCH`` and ``DELETE`` under ``/calendars/{id}/events``,
  always with ``sendUpdates=all``, so each guest gets the invitation, the
  change or the cancellation by email.
- the showings calendar: ``POST /calendars`` creates "Sernia showings" once, at
  connect time, and ``GET /calendars/{id}`` checks it still exists.

The two scopes allow free/busy reads and calendars this app created, so the
app never reads what anyone's events are and never writes to their own calendars.

Only ``connections.py`` builds a ``GoogleCalendar``, and only ``outbox.py``
calls its event methods (``test_showing_boundaries.py``).
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol
from urllib.parse import quote

import httpx

from api.src.showing_bookings import google_http
from api.src.showing_bookings.google_oauth import GrantLost, OAuthError
from api.src.showing_bookings.providers import BusyBlock, CalendarUnreadable, EventDraft, NotSent

API = "https://www.googleapis.com/calendar/v3"
SHOWINGS_CALENDAR_NAME = "Sernia showings"
SHOWINGS_TIME_ZONE = "America/New_York"
# Errors raised before the request left this process: nothing reached Google.
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Google's 403 reasons that mean "slow down", not "never".
RATE_LIMITS = {"rateLimitExceeded", "userRateLimitExceeded"}
# 403 reasons about the account's usage, which say nothing about a calendar.
USAGE_LIMITS = RATE_LIMITS | {"dailyLimitExceeded", "quotaExceeded"}
NOT_SHARED = (
    "Google cannot find this calendar, or the connected account cannot see it. "
    "Share its free/busy times with that account."
)


class AccessTokens(Protocol):
    async def access_token(self, *, fresh: bool = False) -> str:
        """A current access token; ``fresh`` asks Google for a new one.

        Raises ``google_oauth.GrantLost`` when Google no longer accepts the
        grant, and ``google_oauth.OAuthError`` when no token can be had now.
        """
        ...


# Told the outcome of each free/busy read: (every calendar read, the problem).
ReadRecorder = Callable[[bool, str | None], Awaitable[None]]


class ShowingsCalendarError(Exception):
    """The "Sernia showings" calendar could not be found or made."""


class _BadAnswer(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _q(calendar_or_event_id: str) -> str:
    """An id as one path segment: calendar ids hold "@" and sometimes "#"."""
    return quote(calendar_or_event_id, safe="")


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _api_error(response: httpx.Response) -> tuple[str, str]:
    """Google's error reason and a short description, from an error answer."""
    error = _json(response).get("error")
    error = error if isinstance(error, dict) else {}
    details = error.get("errors") if isinstance(error.get("errors"), list) else []
    reason = next(
        (item["reason"] for item in details if isinstance(item, dict) and item.get("reason")), ""
    )
    message = error.get("message") if isinstance(error.get("message"), str) else ""
    parts = [f"Google answered {response.status_code}", str(reason), message[:200]]
    return str(reason), " ".join(part for part in parts if part).strip() + "."


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Google sent a time that is not text.")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("Google sent a time without a timezone.")
    return moment


def _blocks(busy: object, start: datetime, end: datetime) -> list[BusyBlock]:
    """Google's busy periods, cut to the window asked about, in order."""
    if busy is None:
        return []
    if not isinstance(busy, list) or not all(isinstance(period, dict) for period in busy):
        raise ValueError("Google's busy periods are not a list of periods.")
    blocks = []
    for period in busy:
        begins, ends = max(_time(period.get("start")), start), min(_time(period.get("end")), end)
        if begins < ends:
            blocks.append(BusyBlock(start=begins, end=ends))
    return sorted(blocks, key=lambda block: block.start)


def _calendar_error(errors: object) -> str:
    items = errors if isinstance(errors, list) else []
    reasons = [
        str(item["reason"]) for item in items if isinstance(item, dict) and item.get("reason")
    ]
    if "notFound" in reasons:
        return NOT_SHARED
    return f"Google could not read this calendar ({', '.join(reasons) or 'no reason given'})."


def _event_body(event: EventDraft) -> dict:
    if event.starts_at.tzinfo is None or event.ends_at.tzinfo is None:
        raise NotSent("rejected", "Event times must carry a timezone.")
    return {
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
        "start": {"dateTime": event.starts_at.isoformat()},
        "end": {"dateTime": event.ends_at.isoformat()},
        "attendees": [{"email": address} for address in event.attendees],
        # Guests receive the event; they cannot add other people to it.
        "guestsCanInviteOthers": False,
    }


def _refusal(response: httpx.Response, *, missing: str) -> Exception:
    """What a write's error answer means for the outbox."""
    status = response.status_code
    reason, detail = _api_error(response)
    if status >= 500 or status == 408:
        # Google may have made the change before failing.
        return RuntimeError(f"{detail} The change may have been made.")
    if status == 429 or (status == 403 and reason in RATE_LIMITS):
        return NotSent("rate_limited", detail, retryable=True)
    if status in (404, 410):
        return NotSent(missing, detail)
    return NotSent("rejected", detail)


class GoogleCalendar:
    """A ``CalendarSource`` over one connected Google account."""

    def __init__(self, tokens: AccessTokens, *, on_read: ReadRecorder | None = None) -> None:
        self._tokens = tokens
        self._on_read = on_read

    async def _call(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> httpx.Response:
        """One request with a current access token. A 401 gets a new token and one
        more try: Google refuses an expired token before doing anything, so a
        second try cannot repeat a change."""
        for fresh in (False, True):
            token = await self._tokens.access_token(fresh=fresh)
            async with google_http.client() as client:
                response = await client.request(
                    method,
                    f"{API}{path}",
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status_code != 401:
                break
        return response

    async def _record(self, ok: bool, problem: str | None) -> None:
        if self._on_read is not None:
            await self._on_read(ok, problem)

    async def busy_blocks(
        self, calendar_ids: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[BusyBlock]]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Times must be timezone aware.")
        ids = list(dict.fromkeys(calendar_ids))
        if not ids:
            return {}
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": calendar_id} for calendar_id in ids],
        }
        try:
            response = await self._call("POST", "/freeBusy", json=body)
            if response.status_code != 200:
                raise _BadAnswer(_api_error(response)[1])
            calendars = _json(response).get("calendars")
            if not isinstance(calendars, dict):
                raise _BadAnswer("Google's free/busy answer had no calendars.")
        except OAuthError as failure:
            problem = failure.detail
        except httpx.HTTPError as exc:
            problem = f"Could not reach Google ({type(exc).__name__})."
        except _BadAnswer as bad:
            problem = bad.detail
        else:
            problem = None
        if problem is not None:
            await self._record(False, problem)
            raise CalendarUnreadable({calendar_id: problem for calendar_id in ids}, {})

        folded = {str(key).casefold(): value for key, value in calendars.items()}
        read: dict[str, list[BusyBlock]] = {}
        unreadable: dict[str, str] = {}
        for calendar_id in ids:
            entry = calendars.get(calendar_id, folded.get(calendar_id.casefold()))
            if not isinstance(entry, dict):
                unreadable[calendar_id] = "Google's answer left this calendar out."
            elif entry.get("errors"):
                unreadable[calendar_id] = _calendar_error(entry["errors"])
            else:
                try:
                    read[calendar_id] = _blocks(entry.get("busy"), start, end)
                except (KeyError, TypeError, ValueError):
                    unreadable[calendar_id] = "Google's answer for this calendar could not be read."
        summary = "; ".join(f"{key}: {reason}" for key, reason in unreadable.items())
        await self._record(not unreadable, summary[:500] or None)
        if unreadable:
            raise CalendarUnreadable(unreadable, read)
        return read

    async def _write(self, method: str, path: str, *, json: dict | None = None) -> httpx.Response:
        """A change that tells the guests. Raises ``NotSent`` when nothing reached
        Google; any other error propagates, since the change may have been made."""
        try:
            return await self._call(method, path, params={"sendUpdates": "all"}, json=json)
        except GrantLost as lost:
            raise NotSent("calendar_lost", lost.detail) from None
        except OAuthError as failure:
            if failure.reason == "unreachable":
                raise NotSent("unavailable", failure.detail, retryable=True) from None
            raise NotSent("calendar_unusable", failure.detail) from None
        except CONNECT_ERRORS:
            raise NotSent("unavailable", "Could not reach Google.", retryable=True) from None

    async def create_event(self, calendar_id: str, event: EventDraft) -> str:
        response = await self._write(
            "POST", f"/calendars/{_q(calendar_id)}/events", json=_event_body(event)
        )
        if response.status_code in (200, 201):
            event_id = _json(response).get("id")
            if not (isinstance(event_id, str) and event_id):
                raise RuntimeError("Google created the event, but its answer has no event id.")
            return event_id
        raise _refusal(response, missing="calendar_missing")

    async def update_event(self, calendar_id: str, event_id: str, event: EventDraft) -> None:
        response = await self._write(
            "PATCH",
            f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}",
            json=_event_body(event),
        )
        if response.status_code != 200:
            raise _refusal(response, missing="no_such_event")

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        response = await self._write(
            "DELETE", f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}"
        )
        # 410: already cancelled, which is what was asked.
        if response.status_code not in (200, 204, 410):
            raise _refusal(response, missing="no_such_event")

    async def ensure_showings_calendar(self, existing_id: str | None) -> str:
        """The id of the account's "Sernia showings" calendar, made the first time.

        ``existing_id`` is the calendar saved from an earlier connection of the
        same account. It is replaced only when Google says it is gone, or
        refuses it for a reason about the calendar; a rate or quota limit, or
        any other failure, raises ``ShowingsCalendarError`` and keeps it, so a
        reconnect never leaves the saved calendar and its events behind.
        """
        try:
            if existing_id:
                found = await self._call("GET", f"/calendars/{_q(existing_id)}")
                if found.status_code == 200:
                    return existing_id
                reason, detail = _api_error(found)
                refused = found.status_code == 403 and bool(reason) and reason not in USAGE_LIMITS
                if found.status_code not in (404, 410) and not refused:
                    raise ShowingsCalendarError(detail)
            made = await self._call(
                "POST",
                "/calendars",
                json={"summary": SHOWINGS_CALENDAR_NAME, "timeZone": SHOWINGS_TIME_ZONE},
            )
        except OAuthError as failure:
            raise ShowingsCalendarError(failure.detail) from None
        except httpx.HTTPError as exc:
            raise ShowingsCalendarError(f"Could not reach Google ({type(exc).__name__}).") from None
        calendar_id = _json(made).get("id") if made.status_code == 200 else None
        if not (isinstance(calendar_id, str) and calendar_id):
            raise ShowingsCalendarError(_api_error(made)[1])
        return calendar_id
