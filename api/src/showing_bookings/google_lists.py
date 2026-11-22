"""Google's two lists behind the calendar picker: the account's calendar list and the directory.

Written from Google's reference:

- calendar list: ``GET /users/me/calendarList`` (Calendar API v3), at most 250
  entries a page. Needs ``calendar.calendarlist.readonly``. Entries hidden in
  Google Calendar's own sidebar are included.
  https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list
- directory: ``GET /v1/people:listDirectoryPeople`` (People API), at most 1,000
  people a page. Needs ``directory.readonly``; ``readMask`` and ``sources`` are
  required, and only names and email addresses are asked for.
  https://developers.google.com/people/api/rest/v1/people/listDirectoryPeople
  Google lists a Workspace's people only when its admin has turned on sharing
  them with apps (External Directory Sharing):
  https://developers.google.com/people/v1/directory
- why a list was refused: the reasons in Google's error answers, from
  https://developers.google.com/workspace/calendar/api/guides/errors and
  https://github.com/googleapis/googleapis/blob/master/google/api/error_reason.proto

Both are read to the end, within ``MAX_PAGES`` and ``MAX_ENTRIES``. Google's
answers are never quoted: a list that cannot be read is one of
``providers.ListStatus``. Nothing is written and nothing is kept.

Only ``connections.py`` builds a ``GoogleLists`` (``test_showing_boundaries.py``).
"""

from collections.abc import Callable

import httpx

from api.src.showing_bookings import google_http
from api.src.showing_bookings.google_http import AccessTokens
from api.src.showing_bookings.google_oauth import GrantLost, OAuthError
from api.src.showing_bookings.providers import CalendarList, ListedCalendar, ListStatus

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
DIRECTORY_URL = "https://people.googleapis.com/v1/people:listDirectoryPeople"
CALENDAR_LIST_PAGE = 250
DIRECTORY_PAGE = 1000
# Far more than any team here has; reading stops at either, and says so.
MAX_PAGES = 10
MAX_ENTRIES = 500

NOT_GRANTED = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
API_OFF = {"accessNotConfigured", "SERVICE_DISABLED"}
USAGE_LIMITS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "dailyLimitExceeded",
    "quotaExceeded",
    "RATE_LIMIT_EXCEEDED",
}


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _json(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _refusal(response: httpx.Response) -> ListStatus:
    """What an error answer says about the list, as far as it says."""
    status = response.status_code
    if status in (400, 404):
        return "refused"
    if status != 403:
        return "failed"
    error = _dict((_json(response) or {}).get("error"))
    reasons = {
        _text(item.get("reason"))
        for key in ("errors", "details")
        for item in _dicts(error.get(key))
    }
    if reasons & NOT_GRANTED:
        return "not_granted"
    if reasons & API_OFF:
        return "api_off"
    if reasons & USAGE_LIMITS:
        return "failed"
    # The directory's own refusal carries no reason, only words.
    if "directory sharing" in _text(error.get("message")).casefold():
        return "sharing_off"
    return "refused"


def _calendar(entry: dict) -> ListedCalendar | None:
    calendar_id = _text(entry.get("id"))
    if not calendar_id or entry.get("deleted"):
        return None
    name = _text(entry.get("summaryOverride")) or _text(entry.get("summary")) or calendar_id
    return ListedCalendar(calendar_id, name, own=bool(entry.get("primary")))


def _primary(fields: list[dict]) -> dict:
    """The primary one of a person's names or addresses; aliases are the others."""
    for mark in ("primary", "sourcePrimary"):
        for field in fields:
            if _dict(field.get("metadata")).get(mark):
                return field
    return fields[0] if fields else {}


def _person(entry: dict) -> ListedCalendar | None:
    """A person's main calendar: its id is their primary email address."""
    address = _text(_primary(_dicts(entry.get("emailAddresses"))).get("value"))
    if not address:
        return None
    name = _text(_primary(_dicts(entry.get("names"))).get("displayName"))
    return ListedCalendar(address, name or address)


class GoogleLists:
    """The two lists of one connected Google account, read afresh each time."""

    def __init__(self, tokens: AccessTokens) -> None:
        self._tokens = tokens

    async def calendar_list(self) -> CalendarList:
        """The calendars on the account's own list. Raises ``GrantLost``."""
        return await self._read(
            CALENDAR_LIST_URL,
            {"maxResults": CALENDAR_LIST_PAGE, "showHidden": "true"},
            "items",
            _calendar,
        )

    async def directory(self) -> CalendarList:
        """The people in the Workspace directory, by their main calendar. Raises ``GrantLost``."""
        return await self._read(
            DIRECTORY_URL,
            {
                "readMask": "names,emailAddresses",
                "sources": "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
                "pageSize": DIRECTORY_PAGE,
            },
            "people",
            _person,
        )

    async def _read(
        self,
        url: str,
        params: dict,
        key: str,
        listed: Callable[[dict], ListedCalendar | None],
    ) -> CalendarList:
        entries: list[ListedCalendar] = []
        page: str | None = None
        for _ in range(MAX_PAGES):
            try:
                response = await google_http.call(
                    self._tokens,
                    "GET",
                    url,
                    params=params if page is None else {**params, "pageToken": page},
                    # A page token can hold the last entry's address, and tracing
                    # records the query, so only the first page is traced.
                    traced=page is None,
                )
            except GrantLost:
                raise
            except (OAuthError, httpx.HTTPError):
                return CalendarList("failed", tuple(entries))
            if response.status_code != 200:
                return CalendarList(_refusal(response), tuple(entries))
            body = _json(response)
            if body is None:
                return CalendarList("failed", tuple(entries))
            found = [listed(entry) for entry in _dicts(body.get(key))]
            entries += [entry for entry in found if entry is not None]
            page = _text(body.get("nextPageToken")) or None
            if len(entries) > MAX_ENTRIES or (page and len(entries) == MAX_ENTRIES):
                return CalendarList("read", tuple(entries[:MAX_ENTRIES]), more=True)
            if page is None:
                return CalendarList("read", tuple(entries))
        return CalendarList("read", tuple(entries), more=True)
