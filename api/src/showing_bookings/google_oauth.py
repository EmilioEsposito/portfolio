"""Google's OAuth endpoints for calendar connections, on a Google client of their own.

The client is ``GOOGLE_SHOWINGS_OAUTH_CLIENT_ID`` and
``GOOGLE_SHOWINGS_OAUTH_CLIENT_SECRET``, from the Google Cloud project
``sernia-showings``. Both are read at call time and never logged. It is not the
client Clerk's Google sign-in uses (README.md says why).

- ``consent_url``: where the browser goes to approve the permissions.
- ``exchange_code``: the callback's one-time code, for the tokens and the
  account that granted them.
- ``refresh_access_token``: a short-lived access token from a stored refresh
  token. Google's ``invalid_grant`` raises ``GrantLost``.
- ``revoke``: end a grant at Google.

The callback arrives as a plain browser redirect without the person's sign-in,
so who started the connection travels in ``state``: signed with
``SESSION_SECRET_KEY``, good for ten minutes, with a random nonce
(``sign_state`` and ``read_state``).

Errors say what went wrong and never quote a token, a code or the secret.
Written from https://developers.google.com/identity/protocols/oauth2/web-server
"""

import base64
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode

import httpx
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from api.src.showing_bookings import google_http

CLIENT_ID_ENV = "GOOGLE_SHOWINGS_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_SHOWINGS_OAUTH_CLIENT_SECRET"
STATE_SECRET_ENV = "SESSION_SECRET_KEY"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# Free/busy of every calendar the account can see, and the calendars the app itself creates.
FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
APP_CALENDARS_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
# A grant without either of these is refused.
CALENDAR_SCOPES = (FREEBUSY_SCOPE, APP_CALENDARS_SCOPE)
# What the calendar picker lists, both read-only: the account's own calendar
# list, and the people in the Workspace directory. Asked for on every connect
# and never required: a grant without them still connects, and only the picker
# goes without.
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
DIRECTORY_SCOPE = "https://www.googleapis.com/auth/directory.readonly"
PICKER_SCOPES = (CALENDAR_LIST_SCOPE, DIRECTORY_SCOPE)
# openid and email say which account granted access.
SCOPES = ("openid", "email", *CALENDAR_SCOPES, *PICKER_SCOPES)

CALLBACK_PATH = "/api/showing-bookings/calendar/callback"
# The sites a connection may start from. The redirect URI is built from one of
# these, never from what a request says about itself. Google accepts only the
# redirect URIs registered on the client, so a localhost port must be listed there too.
ALLOWED_ORIGINS = frozenset({"https://eesposito.com", "https://dev.eesposito.com"})
_LOCALHOST = re.compile(r"http://localhost(?::\d{1,5})?")
STATE_MAX_AGE_SECONDS = 600
_STATE_SALT = "showing-bookings.calendar-connect"


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = field(repr=False)


def oauth_client() -> OAuthClient | None:
    """The Google client from the environment, or None when it is not set up here."""
    client_id = os.getenv(CLIENT_ID_ENV, "").strip()
    client_secret = os.getenv(CLIENT_SECRET_ENV, "").strip()
    if not (client_id and client_secret):
        return None
    return OAuthClient(client_id, client_secret)


class OAuthError(Exception):
    """Google's OAuth endpoints did not do what was asked.

    ``reason`` is a short key: "not_configured", "unreachable" (Google was not
    reached, or failed with a 5xx), "invalid_grant", "invalid_client",
    "rejected" or "incomplete" (Google's answer lacked something needed).
    """

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class GrantLost(OAuthError):
    """Google no longer accepts a refresh token: the grant was removed or has expired."""

    def __init__(self) -> None:
        super().__init__(
            "invalid_grant",
            "Google no longer accepts this connection: access was removed or has expired. "
            "Reconnect it.",
        )


def _require_client() -> OAuthClient:
    client = oauth_client()
    if client is None:
        raise OAuthError(
            "not_configured", f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} are not set here."
        )
    return client


# ---------------------------------------------------------------------------
# Where a connection starts, and the signed state that carries it
# ---------------------------------------------------------------------------


def allowed_origin(origin: str | None) -> str | None:
    """``origin`` when a connection may start from it, else None."""
    candidate = (origin or "").strip().rstrip("/")
    if candidate in ALLOWED_ORIGINS or _LOCALHOST.fullmatch(candidate):
        return candidate
    return None


def redirect_uri(origin: str) -> str:
    """The callback URL on an allowed origin. Raises ``ValueError`` for any other origin."""
    allowed = allowed_origin(origin)
    if allowed is None:
        raise ValueError(
            "Calendars can be connected only from eesposito.com, dev.eesposito.com or localhost."
        )
    return f"{allowed}{CALLBACK_PATH}"


@dataclass(frozen=True)
class ConnectState:
    """Who started a connection, and where, as the signed ``state`` carries it."""

    user_id: str  # the Clerk user id
    by: str  # their Sernia email, saved as ``connected_by``
    origin: str  # the allowed origin the browser started from
    nonce: str


class StateRejected(Exception):
    """The callback's ``state`` is missing, altered or expired; ``reason`` says which."""

    def __init__(self, reason: Literal["missing", "invalid", "expired"]):
        super().__init__(reason)
        self.reason = reason


def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv(STATE_SECRET_ENV, "")
    if not secret:
        raise OAuthError("not_configured", f"{STATE_SECRET_ENV} is not set here.")
    return URLSafeTimedSerializer(secret, salt=_STATE_SALT)


def sign_state(*, user_id: str, by: str, origin: str) -> str:
    allowed = allowed_origin(origin)
    if allowed is None:
        raise ValueError("Not an origin a connection may start from.")
    return _serializer().dumps(
        {"u": user_id, "b": by, "o": allowed, "n": secrets.token_urlsafe(16)}
    )


def read_state(value: str | None, *, max_age: int = STATE_MAX_AGE_SECONDS) -> ConnectState:
    """The state ``sign_state`` made. Raises ``StateRejected`` when it is missing,
    altered, expired or names an origin no longer allowed."""
    if not value:
        raise StateRejected("missing")
    try:
        payload = _serializer().loads(value, max_age=max_age)
    except SignatureExpired:
        raise StateRejected("expired") from None
    except BadData:
        raise StateRejected("invalid") from None
    if not isinstance(payload, dict):
        raise StateRejected("invalid")
    values = [payload.get(key) for key in ("u", "b", "o", "n")]
    if not all(isinstance(item, str) and item for item in values):
        raise StateRejected("invalid")
    user_id, by, origin, nonce = values
    if allowed_origin(origin) != origin:
        raise StateRejected("invalid")
    return ConnectState(user_id=user_id, by=by, origin=origin, nonce=nonce)


def consent_url(
    client: OAuthClient, *, redirect_to: str, state: str, login_hint: str | None = None
) -> str:
    """Google's consent screen for the calendar and picker permissions."""
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_to,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # A refresh token on every connect: without prompt=consent, Google sends
        # one only the first time an account approves the app.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """What Google returned for a callback's code."""

    subject: str  # Google's stable id for the account
    email: str
    scopes: tuple[str, ...]
    refresh_token: str = field(repr=False)
    access_token: str = field(repr=False)

    @property
    def missing_scopes(self) -> list[str]:
        """The required calendar permissions the person left unticked on Google's screen."""
        return [scope for scope in CALENDAR_SCOPES if scope not in self.scopes]


@dataclass(frozen=True)
class AccessToken:
    value: str = field(repr=False)
    expires_in: int  # seconds, as Google says


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _token_request(form: dict[str, str]) -> dict:
    """POST to Google's token endpoint. The form holds the secret and a code or
    token, so it never appears in an error."""
    try:
        async with google_http.client() as client:
            response = await client.post(TOKEN_URL, data=form)
    except httpx.HTTPError as exc:
        raise OAuthError("unreachable", f"Could not reach Google ({type(exc).__name__}).") from None
    body = _json(response)
    if response.status_code == 200:
        return body
    status = response.status_code
    if status >= 500:
        raise OAuthError("unreachable", f"Google answered {status}.")
    error = body.get("error") if isinstance(body.get("error"), str) else ""
    if error == "invalid_grant":
        raise OAuthError("invalid_grant", "Google did not accept the grant.")
    if error in ("invalid_client", "unauthorized_client"):
        raise OAuthError("invalid_client", "Google did not accept the app's client ID and secret.")
    raise OAuthError("rejected", f"Google answered {status} {error}".rstrip() + ".")


def _id_claims(id_token: object, client_id: str) -> tuple[str, str]:
    """The granting account's subject and email, from the ID token in the token answer.

    The token came straight from Google's token endpoint over TLS, in exchange
    for the client secret, so its signature is not checked again (OpenID Connect
    Core 3.1.3.7). Its issuer and audience still are.
    """
    unreadable = OAuthError(
        "incomplete", "Google's answer did not say which account granted access."
    )
    if not isinstance(id_token, str):
        raise unreadable
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise unreadable from None
    if not isinstance(claims, dict):
        raise unreadable
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    subject, email = claims.get("sub"), claims.get("email")
    if (
        client_id not in audiences
        or claims.get("iss") not in _ISSUERS
        or not (isinstance(subject, str) and subject)
        or not (isinstance(email, str) and email)
    ):
        raise unreadable
    return subject, email.strip().lower()


async def exchange_code(code: str, *, redirect_to: str) -> Grant:
    """Trade the callback's one-time code for tokens. Raises ``OAuthError``."""
    client = _require_client()
    try:
        body = await _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_to,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            }
        )
    except OAuthError as failure:
        if failure.reason == "invalid_grant":
            raise OAuthError(
                "invalid_grant",
                "Google did not accept the code. It works once and expires within minutes.",
            ) from None
        raise
    refresh_token, access_token = body.get("refresh_token"), body.get("access_token")
    if not (isinstance(refresh_token, str) and refresh_token):
        raise OAuthError("incomplete", "Google's answer had no refresh token.")
    if not (isinstance(access_token, str) and access_token):
        raise OAuthError("incomplete", "Google's answer had no access token.")
    subject, email = _id_claims(body.get("id_token"), client.client_id)
    scope = body.get("scope")
    return Grant(
        subject=subject,
        email=email,
        scopes=tuple(scope.split()) if isinstance(scope, str) else (),
        refresh_token=refresh_token,
        access_token=access_token,
    )


async def refresh_access_token(refresh_token: str) -> AccessToken:
    """A new access token. Raises ``GrantLost`` when Google no longer accepts the
    refresh token, and ``OAuthError`` for anything else."""
    client = _require_client()
    try:
        body = await _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            }
        )
    except OAuthError as failure:
        if failure.reason == "invalid_grant":
            raise GrantLost() from None
        raise
    access_token = body.get("access_token")
    if not (isinstance(access_token, str) and access_token):
        raise OAuthError("incomplete", "Google's answer had no access token.")
    expires_in = body.get("expires_in")
    return AccessToken(
        value=access_token,
        expires_in=expires_in if isinstance(expires_in, int) and expires_in > 0 else 0,
    )


async def revoke(token: str) -> None:
    """End the grant at Google. A token Google already rejects counts as revoked.

    The token goes in the form body, never the URL, so it stays out of request logs.
    """
    try:
        async with google_http.client() as client:
            response = await client.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError as exc:
        raise OAuthError("unreachable", f"Could not reach Google ({type(exc).__name__}).") from None
    status = response.status_code
    if status == 200:
        return
    if status == 400 and _json(response).get("error") in ("invalid_token", "invalid_grant"):
        return
    if status >= 500:
        raise OAuthError("unreachable", f"Google answered {status}.")
    raise OAuthError("rejected", f"Google answered {status} to the revocation.")
