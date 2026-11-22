"""A stand-in for Google's OAuth, Calendar and People endpoints, behind ``httpx.MockTransport``.
Not a test module.

``FakeGoogle`` answers the requests ``google_oauth.py``, ``google_calendar.py``
and ``google_lists.py`` make, shaped as Google's documentation describes, and
keeps every request so a test can read what was sent. Install it with
``fake.install(monkeypatch)``; nothing leaves the process.

Everything is made up: example.com accounts, a client id and secret that
exist nowhere, and tokens numbered in the order they are issued.
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote

import httpx
from cryptography.fernet import Fernet

from api.src.showing_bookings import google_http, google_oauth

CLIENT_ID = "test-showings-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-showings-client-secret-0001"
TOKEN_KEY = Fernet.generate_key().decode()
OTHER_TOKEN_KEY = Fernet.generate_key().decode()
TEAM_ACCOUNT = "team@example.com"
TEAM_SUBJECT = "100000000000000000001"
ORIGIN = "http://localhost:5173"
REDIRECT_URI = f"{ORIGIN}{google_oauth.CALLBACK_PATH}"
# A grant as it was before the calendar picker, which still has to work everywhere.
ALL_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    *google_oauth.CALENDAR_SCOPES,
)
PICKER_SCOPES = (*ALL_SCOPES, *google_oauth.PICKER_SCOPES)
CALENDAR_LIST = "/calendar/v3/users/me/calendarList"
DIRECTORY = "/v1/people:listDirectoryPeople"
FREE_BUSY = "/calendar/v3/freeBusy"


def configure(monkeypatch, *, key: str | None = TOKEN_KEY) -> None:
    """Set this environment's Google client and token key, as production would have them."""
    monkeypatch.setenv(google_oauth.CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(google_oauth.CLIENT_SECRET_ENV, CLIENT_SECRET)
    if key is None:
        monkeypatch.delenv("SHOWING_TOKEN_KEY", raising=False)
    else:
        monkeypatch.setenv("SHOWING_TOKEN_KEY", key)


def _utc(moment: datetime) -> str:
    """A time as free/busy answers give it: UTC, with a Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def id_token(claims: dict) -> str:
    """An ID token as Google's token endpoint returns it; the signature is not checked."""

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256', 'kid': 'test'})}.{part(claims)}.c2lnbmF0dXJl"


def person(name: str | None, *addresses: str, **metadata) -> dict:
    """A directory person as the People API gives one: the first address is the
    primary one unless ``metadata`` says otherwise, and the rest are aliases."""
    mark = metadata or {"primary": True, "sourcePrimary": True}
    emails = [{"value": address, "metadata": {"verified": True}} for address in addresses]
    if emails:
        emails[0]["metadata"] |= mark
    return {
        "resourceName": f"people/{name}",
        **({"names": [{"displayName": name, "metadata": {"primary": True}}]} if name else {}),
        **({"emailAddresses": emails} if emails else {}),
    }


def people_error(status: int, reason: str | None, message: str) -> httpx.Response:
    """An error as the People API answers it, with Google's ``ErrorInfo`` when it gives a reason."""
    info = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": reason,
        "domain": "googleapis.com",
        "metadata": {"service": "people.googleapis.com"},
    }
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": message,
                "status": "PERMISSION_DENIED",
                **({"details": [info]} if reason else {}),
            }
        },
    )


@dataclass
class Account:
    subject: str
    email: str
    scopes: tuple[str, ...] = ALL_SCOPES


@dataclass
class Event:
    calendar_id: str
    body: dict
    deleted: bool = False


@dataclass
class BusyEvent:
    start: datetime
    end: datetime
    # "transparent" is an event marked free: free/busy leaves it out.
    transparency: str = "opaque"


@dataclass
class FakeGoogle:
    requests: list[httpx.Request] = field(default_factory=list)
    codes: dict[str, tuple[Account, str]] = field(default_factory=dict)
    # Refresh tokens Google still accepts, by token: the account they belong to.
    refresh_tokens: dict[str, Account] = field(default_factory=dict)
    access_tokens: dict[str, Account] = field(default_factory=dict)
    calendars: dict[str, dict] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)
    busy: dict[str, list[BusyEvent]] = field(default_factory=dict)
    # Calendars free/busy reports an error for, by id: Google's reason.
    unreadable: dict[str, str] = field(default_factory=dict)
    # The connected account's calendar list, and the Workspace directory, as Google's entries.
    calendar_list: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    # The most entries a page of either holds, below what the request asks for.
    page_limit: int = 1000
    # Answers forced for a request, by (method, path): a response, an exception, or a
    # list of them for the requests in turn, where None is answered as usual.
    forced: dict[
        tuple[str, str],
        httpx.Response | Exception | list[httpx.Response | Exception | None],
    ] = field(default_factory=dict)
    access_lifetime: int = 3599
    issued: int = 0
    # Every code and token handed out, so a test can check none of them leaks.
    secrets: list[str] = field(default_factory=list)

    # -- Set-up ---------------------------------------------------------------

    def install(self, monkeypatch) -> "FakeGoogle":
        monkeypatch.setattr(google_http, "client", self.client)
        return self

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def approve(
        self,
        account: Account | None = None,
        *,
        redirect_uri: str = REDIRECT_URI,
    ) -> str:
        """The code Google would put in the callback once ``account`` approves."""
        account = account or Account(TEAM_SUBJECT, TEAM_ACCOUNT)
        self.issued += 1
        code = f"4/test-code-{self.issued:04d}"
        self.secrets.append(code)
        self.codes[code] = (account, redirect_uri)
        return code

    def refresh_token_for(self, account: Account | None = None) -> str:
        """A refresh token Google accepts, as if granted earlier."""
        account = account or Account(TEAM_SUBJECT, TEAM_ACCOUNT)
        self.issued += 1
        token = f"1//test-refresh-{self.issued:04d}"
        self.secrets.append(token)
        self.refresh_tokens[token] = account
        return token

    def revoke_everything(self) -> None:
        """The account removed the app's access in its Google settings."""
        self.refresh_tokens.clear()
        self.access_tokens.clear()

    def add_busy(self, calendar_id: str, start: datetime, end: datetime, transparency="opaque"):
        self.busy.setdefault(calendar_id, []).append(BusyEvent(start, end, transparency))

    # -- Reading what was sent -----------------------------------------------

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and self._path(r) == path]

    def paths(self) -> list[tuple[str, str]]:
        return [(r.method, self._path(r)) for r in self.requests]

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}

    @staticmethod
    def body(request: httpx.Request) -> dict:
        return json.loads(request.content)

    @staticmethod
    def _path(request: httpx.Request) -> str:
        raw = request.url.raw_path.decode().split("?", 1)[0]
        return "/".join(unquote(segment) for segment in raw.split("/"))

    # -- Answering ------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = self._path(request)
        forced = self.forced.get((request.method, path))
        if isinstance(forced, list):
            forced = forced.pop(0) if forced else None
        if isinstance(forced, Exception):
            raise forced
        if forced is not None:
            return forced
        host = request.url.host
        if host == "oauth2.googleapis.com" and path == "/token":
            return self._token(request)
        if host == "oauth2.googleapis.com" and path == "/revoke":
            return self._revoke(request)
        if host == "www.googleapis.com" and path.startswith("/calendar/v3/"):
            return self._calendar_api(request, path.removeprefix("/calendar/v3"))
        if host == "people.googleapis.com" and path == DIRECTORY:
            return self._directory(request)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    def _issue_access(self, account: Account) -> str:
        self.issued += 1
        token = f"ya29.test-access-{self.issued:04d}"
        self.secrets.append(token)
        self.access_tokens[token] = account
        return token

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = self.form(request)
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") == "authorization_code":
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None or entry[1] != form.get("redirect_uri"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            account = entry[0]
            refresh = self.refresh_token_for(account)
            return httpx.Response(
                200,
                json={
                    "access_token": self._issue_access(account),
                    "expires_in": self.access_lifetime,
                    "refresh_token": refresh,
                    "scope": " ".join(account.scopes),
                    "token_type": "Bearer",
                    "id_token": id_token(
                        {
                            "iss": "https://accounts.google.com",
                            "aud": CLIENT_ID,
                            "sub": account.subject,
                            "email": account.email,
                            "email_verified": True,
                        }
                    ),
                },
            )
        if form.get("grant_type") == "refresh_token":
            account = self.refresh_tokens.get(form.get("refresh_token", ""))
            if account is None:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Token has been expired or revoked.",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": self._issue_access(account),
                    "expires_in": self.access_lifetime,
                    "scope": " ".join(account.scopes),
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        token = self.form(request).get("token", "")
        account = self.refresh_tokens.get(token) or self.access_tokens.get(token)
        if account is None:
            return httpx.Response(
                400,
                json={"error": "invalid_token", "error_description": "Token expired or revoked"},
            )
        # Revoking any token ends the whole grant for that account.
        for tokens in (self.refresh_tokens, self.access_tokens):
            for key in [key for key, owner in tokens.items() if owner.subject == account.subject]:
                del tokens[key]
        return httpx.Response(200)

    @staticmethod
    def _error(status: int, reason: str, message: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": status,
                    "message": message,
                    "errors": [{"domain": "global", "reason": reason, "message": message}],
                }
            },
        )

    def _account(self, request: httpx.Request) -> Account | None:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        return self.access_tokens.get(bearer)

    def _page(self, request: httpx.Request, entries: list[dict], size: int, key: str) -> dict:
        """One page of ``entries``. The token names the last entry given, address and all,
        as Google's can, so a test can check it is not logged."""
        token = request.url.params.get("pageToken")

        def address(entry: dict, at: int) -> str:
            emails = entry.get("emailAddresses") or [{}]
            return entry.get("id") or emails[0].get("value") or str(at)

        addresses = [address(entry, at) for at, entry in enumerate(entries)]
        first = addresses.index(token.removeprefix("after-")) + 1 if token else 0
        last = first + min(size, self.page_limit)
        page = {key: entries[first:last]}
        if last < len(entries):
            page["nextPageToken"] = f"after-{addresses[last - 1]}"
        return page

    def _directory(self, request: httpx.Request) -> httpx.Response:
        account = self._account(request)
        if account is None:
            return people_error(401, None, "Request had invalid authentication credentials.")
        if google_oauth.DIRECTORY_SCOPE not in account.scopes:
            return people_error(
                403,
                "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                "Request had insufficient authentication scopes.",
            )
        params = request.url.params
        if not params.get("readMask") or not params.get("sources"):
            return people_error(400, None, "readMask and sources are required.")
        size = int(params.get("pageSize", 100))
        if not 1 <= size <= 1000:
            return people_error(400, None, "pageSize must be from 1 to 1000.")
        return httpx.Response(200, json=self._page(request, self.people, size, "people"))

    def _calendar_api(self, request: httpx.Request, path: str) -> httpx.Response:
        account = self._account(request)
        if account is None:
            return self._error(401, "authError", "Invalid Credentials")
        parts = path.strip("/").split("/")
        if request.method == "GET" and parts == ["users", "me", "calendarList"]:
            if google_oauth.CALENDAR_LIST_SCOPE not in account.scopes:
                return self._error(403, "insufficientPermissions", "Insufficient Permission")
            size = min(int(request.url.params.get("maxResults", 100)), 250)
            return httpx.Response(
                200,
                json={
                    "kind": "calendar#calendarList",
                    **self._page(request, self.calendar_list, size, "items"),
                },
            )
        if request.method == "POST" and parts == ["freeBusy"]:
            return self._freebusy(self.body(request))
        if parts == ["calendars"] and request.method == "POST":
            self.issued += 1
            calendar_id = f"c_test{self.issued:04d}@group.calendar.google.com"
            self.calendars[calendar_id] = {**self.body(request), "owner": account.subject}
            return httpx.Response(
                200, json={"kind": "calendar#calendar", "id": calendar_id, **self.body(request)}
            )
        if len(parts) == 2 and parts[0] == "calendars" and request.method == "GET":
            calendar = self.calendars.get(parts[1])
            if calendar is None:
                return self._error(404, "notFound", "Not Found")
            return httpx.Response(200, json={"id": parts[1], "summary": calendar["summary"]})
        if len(parts) >= 3 and parts[0] == "calendars" and parts[2] == "events":
            calendar_id = parts[1]
            if calendar_id not in self.calendars:
                return self._error(404, "notFound", "Not Found")
            if len(parts) == 3 and request.method == "POST":
                self.issued += 1
                event_id = f"testevent{self.issued:04d}"
                self.events[event_id] = Event(calendar_id, self.body(request))
                return httpx.Response(200, json={"id": event_id, "status": "confirmed"})
            event = self.events.get(parts[3]) if len(parts) == 4 else None
            if event is None or event.calendar_id != calendar_id:
                return self._error(404, "notFound", "Not Found")
            if event.deleted:
                return self._error(410, "deleted", "Resource has been deleted")
            if request.method == "PATCH":
                event.body = {**event.body, **self.body(request)}
                return httpx.Response(200, json={"id": parts[3], "status": "confirmed"})
            if request.method == "DELETE":
                event.deleted = True
                return httpx.Response(204)
        return self._error(404, "notFound", "Not Found")

    def _freebusy(self, query: dict) -> httpx.Response:
        start = datetime.fromisoformat(query["timeMin"])
        end = datetime.fromisoformat(query["timeMax"])
        calendars = {}
        for index, item in enumerate(query["items"]):
            calendar_id = item["id"]
            if index >= 50:
                calendars[calendar_id] = {
                    "errors": [{"domain": "global", "reason": "tooManyCalendarsRequested"}],
                    "busy": [],
                }
                continue
            if calendar_id in self.unreadable:
                calendars[calendar_id] = {
                    "errors": [{"domain": "global", "reason": self.unreadable[calendar_id]}],
                    "busy": [],
                }
                continue
            busy = [
                {"start": _utc(event.start), "end": _utc(event.end)}
                for event in self.busy.get(calendar_id, [])
                if event.transparency != "transparent" and event.start < end and event.end > start
            ]
            calendars[calendar_id] = {"busy": busy}
        return httpx.Response(
            200,
            json={
                "kind": "calendar#freeBusy",
                "timeMin": query["timeMin"],
                "timeMax": query["timeMax"],
                "calendars": calendars,
            },
        )
