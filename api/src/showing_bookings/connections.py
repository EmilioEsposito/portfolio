"""Calendar connections: one row per Google account that granted calendar access.

- Connecting: ``start_connect`` gives the browser Google's consent screen, and
  ``complete_connect`` takes Google's answer at the callback. It saves the
  account with its refresh token encrypted (``token_crypto.py``) and makes
  sure the account has the "Sernia showings" calendar.
- Using one: ``calendar_for_connection`` is the ``CalendarSource`` for a
  connection. ``calendar_for_showings_calendar`` finds the connection whose
  showings calendar a calendar notice names; the outbox sends through it.
- Looking after one: ``check_connection`` reads the account's own free/busy
  once and saves the result; ``disconnect`` revokes the grant at Google, then
  marks the row revoked.

A connection is usable here when it is connected, this environment has the
Google client and the token key, and its token decrypts with that key. Any
other connection reads as unreadable and refuses writes, so its agents count
as busy and nothing is sent through it. Each read through Google is saved on
the row (``last_read_at``, ``last_read_ok``, ``last_error``), and a refresh
token Google rejects marks the row ``lost``.
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Literal, get_args

import logfire
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import google_oauth, token_crypto
from api.src.showing_bookings.google_calendar import GoogleCalendar, ShowingsCalendarError
from api.src.showing_bookings.models import CalendarConnection
from api.src.showing_bookings.providers import (
    BusyBlock,
    CalendarSource,
    CalendarUnreadable,
    EventDraft,
    NotSent,
)

# What the Booking settings tab is told after Google's callback.
ConnectResult = Literal[
    "connected",
    "denied",
    "missing_permissions",
    "expired",
    "invalid",
    "not_configured",
    "failed",
]
CONNECT_RESULTS: tuple[str, ...] = get_args(ConnectResult)

# "Check now" reads the account's own calendar over this window.
CHECK_WINDOW = timedelta(days=1)
# An access token is used until this long before Google says it expires.
EXPIRY_MARGIN_SECONDS = 60
_GOOGLE_ERROR = re.compile(r"[a-z_]{1,40}")


class NotConfigured(Exception):
    """Calendars cannot be connected in this environment; the message says why."""


class ConnectionUnusable(Exception):
    """The connection cannot be used here; the message says why."""


class CannotDisconnect(Exception):
    """Google was not told, so the connection is left as it was; the message says why."""


def setup_problem() -> str | None:
    """Why calendars cannot be connected or used in this environment, or None."""
    if google_oauth.oauth_client() is None:
        return (
            "The Google client for calendar connections is not set up here "
            f"({google_oauth.CLIENT_ID_ENV}, {google_oauth.CLIENT_SECRET_ENV})."
        )
    problem = token_crypto.key_problem()
    if problem is not None:
        return f"{problem.detail} Tokens cannot be saved or read without it."
    return None


@dataclass(frozen=True)
class _Problem:
    code: str  # the ``NotSent`` reason a write gets
    detail: str  # what the team reads


def _problem(connection: CalendarConnection) -> _Problem | None:
    if connection.status == "lost":
        return _Problem("calendar_lost", connection.last_error or google_oauth.GrantLost().detail)
    if connection.status != "connected":
        return _Problem("calendar_not_connected", "This connection was disconnected.")
    setup = setup_problem()
    if setup is not None:
        return _Problem("calendar_unusable", setup)
    if not connection.encrypted_refresh_token:
        return _Problem(
            "calendar_unusable", "No token is stored for this connection. Reconnect it."
        )
    try:
        token_crypto.decrypt(connection.encrypted_refresh_token)
    except token_crypto.TokenUnreadable as unreadable:
        return _Problem("calendar_unusable", unreadable.detail)
    return None


def unusable_reason(connection: CalendarConnection) -> str | None:
    """Why ``connection`` cannot be used in this environment, or None when it can."""
    problem = _problem(connection)
    return problem.detail if problem is not None else None


class UnusableCalendar:
    """A ``CalendarSource`` for a connection that cannot be used here.

    Every read raises ``CalendarUnreadable`` and every write ``NotSent``, so the
    connection's agents count as busy and nothing is sent through it.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail

    async def busy_blocks(
        self, calendar_ids: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[BusyBlock]]:
        ids = list(dict.fromkeys(calendar_ids))
        if not ids:
            return {}
        raise CalendarUnreadable({calendar_id: self.detail for calendar_id in ids}, {})

    async def create_event(self, calendar_id: str, event: EventDraft) -> str:
        raise NotSent(self.code, self.detail)

    async def update_event(self, calendar_id: str, event_id: str, event: EventDraft) -> None:
        raise NotSent(self.code, self.detail)

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        raise NotSent(self.code, self.detail)


# ---------------------------------------------------------------------------
# Using a connection
# ---------------------------------------------------------------------------

# Access tokens by connection id, in this process's memory only and never
# saved: (the encrypted refresh token they came from, token, good until).
_ACCESS_TOKENS: dict[int, tuple[str, str, float]] = {}


def forget_access_tokens() -> None:
    """Drop every access token held in memory. For tests."""
    _ACCESS_TOKENS.clear()


class _ConnectionTokens:
    """Access tokens for one connection, refreshed from its refresh token as needed."""

    def __init__(self, connection_id: int, ciphertext: str) -> None:
        self._id = connection_id
        self._ciphertext = ciphertext

    async def access_token(self, *, fresh: bool = False) -> str:
        held = _ACCESS_TOKENS.get(self._id)
        if not fresh and held and held[0] == self._ciphertext and held[2] > time.monotonic():
            return held[1]
        try:
            refresh_token = token_crypto.decrypt(self._ciphertext)
        except token_crypto.TokenUnreadable as unreadable:
            raise google_oauth.OAuthError("not_configured", unreadable.detail) from None
        try:
            token = await google_oauth.refresh_access_token(refresh_token)
        except google_oauth.GrantLost:
            _ACCESS_TOKENS.pop(self._id, None)
            await _mark_lost(self._id, self._ciphertext)
            raise
        good_until = time.monotonic() + token.expires_in - EXPIRY_MARGIN_SECONDS
        _ACCESS_TOKENS[self._id] = (self._ciphertext, token.value, good_until)
        return token.value


async def _mark_lost(connection_id: int, ciphertext: str) -> None:
    """Google rejected the refresh token: the connection is lost until someone reconnects it."""
    async with AsyncSessionFactory() as session, session.begin():
        changed = await session.execute(
            update(CalendarConnection)
            .where(
                CalendarConnection.id == connection_id,
                CalendarConnection.status == "connected",
                # A reconnect since this token was read is left alone.
                CalendarConnection.encrypted_refresh_token == ciphertext,
            )
            .values(status="lost", last_error=google_oauth.GrantLost().detail)
        )
    if changed.rowcount:
        # Error level, so the alert on error records reaches the team: until
        # someone reconnects it, this connection's agents count as busy.
        logfire.error("showing bookings: calendar connection lost", connection_id=connection_id)


async def _record_read(connection_id: int, ok: bool, problem: str | None) -> None:
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(CalendarConnection)
            .where(CalendarConnection.id == connection_id)
            .values(last_read_at=func.now(), last_read_ok=ok, last_error=None if ok else problem)
        )


def calendar_for_connection(connection: CalendarConnection) -> CalendarSource:
    """The calendar to read and write through ``connection``, a ``calendar_connections`` row.

    A connection that cannot be used here (``unusable_reason``) gives a
    calendar that reads as unreadable and refuses writes. Tests replace this
    function with one that returns an ``InMemoryCalendar``.
    """
    problem = _problem(connection)
    if problem is not None:
        return UnusableCalendar(problem.code, problem.detail)
    tokens = _ConnectionTokens(connection.id, connection.encrypted_refresh_token)
    return GoogleCalendar(tokens, on_read=partial(_record_read, connection.id))


async def calendar_for_showings_calendar(calendar_id: str) -> CalendarSource:
    """The calendar of the connection whose "Sernia showings" calendar is ``calendar_id``.

    The outbox sends calendar notices through it. Raises ``NotSent`` when no
    connection holds that calendar.
    """
    async with AsyncSessionFactory() as session:
        connection = await session.scalar(
            select(CalendarConnection)
            .where(CalendarConnection.showings_calendar_id == calendar_id)
            .order_by(
                (CalendarConnection.status == "connected").desc(), CalendarConnection.id.desc()
            )
            .limit(1)
        )
    if connection is None:
        raise NotSent("calendar_not_connected", "No connected Google account holds this calendar.")
    return calendar_for_connection(connection)


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


def start_connect(*, user_id: str, by: str, origin: str, account: str | None = None) -> str:
    """Google's consent URL, for a signed-in team member starting a connection.

    ``account`` preselects a Google account, for a reconnect. Raises
    ``NotConfigured`` when this environment cannot connect calendars and
    ``ValueError`` for an origin a connection may not start from.
    """
    problem = setup_problem()
    client = google_oauth.oauth_client()
    if problem is not None or client is None:
        raise NotConfigured(problem or "The Google client is not set up here.")
    redirect_to = google_oauth.redirect_uri(origin)
    try:
        state = google_oauth.sign_state(user_id=user_id, by=by, origin=origin)
    except google_oauth.OAuthError as failure:
        raise NotConfigured(failure.detail) from None
    return google_oauth.consent_url(
        client, redirect_to=redirect_to, state=state, login_hint=account
    )


class _IssuedToken:
    """The access token Google issued with the grant, for the calls made while connecting."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def access_token(self, *, fresh: bool = False) -> str:
        if fresh:
            raise google_oauth.OAuthError(
                "rejected", "Google did not accept the access token it had just issued."
            )
        return self._token


async def complete_connect(
    *, state: str | None, code: str | None, error: str | None, now: datetime
) -> ConnectResult:
    """Finish a connection from Google's callback, and say how it went.

    Nothing is saved unless the state is valid, Google granted both calendar
    permissions, and the account has the showings calendar. Connecting an
    account again updates its row and keeps its showings calendar.
    """
    try:
        started = google_oauth.read_state(state)
    except google_oauth.StateRejected as rejected:
        logfire.warn("showing bookings: calendar callback refused", reason=rejected.reason)
        return "expired" if rejected.reason == "expired" else "invalid"
    except google_oauth.OAuthError:
        return "not_configured"
    user_id = started.user_id
    if error:
        reason = error if _GOOGLE_ERROR.fullmatch(error) else "other"
        logfire.info("showing bookings: calendar consent not given", user_id=user_id, error=reason)
        return "denied" if error == "access_denied" else "failed"
    if not code:
        return "invalid"
    if setup_problem() is not None:
        return "not_configured"

    try:
        grant = await google_oauth.exchange_code(
            code, redirect_to=google_oauth.redirect_uri(started.origin)
        )
    except google_oauth.OAuthError as failure:
        logfire.warn(
            "showing bookings: calendar code not exchanged", user_id=user_id, reason=failure.reason
        )
        return (
            "not_configured" if failure.reason in ("not_configured", "invalid_client") else "failed"
        )
    if grant.missing_scopes:
        logfire.warn(
            "showing bookings: calendar permissions left out",
            user_id=user_id,
            missing=len(grant.missing_scopes),
        )
        return "missing_permissions"

    async with AsyncSessionFactory() as session:
        existing = await session.scalar(
            select(CalendarConnection).where(CalendarConnection.google_subject == grant.subject)
        )
    previous_calendar = existing.showings_calendar_id if existing is not None else None
    try:
        calendar_id = await GoogleCalendar(
            _IssuedToken(grant.access_token)
        ).ensure_showings_calendar(previous_calendar)
    except ShowingsCalendarError as failure:
        logfire.warn(
            "showing bookings: showings calendar not made", user_id=user_id, problem=str(failure)
        )
        return "failed"
    try:
        ciphertext = token_crypto.encrypt(grant.refresh_token)
    except token_crypto.TokenUnreadable:
        return "not_configured"

    values = {
        "google_email": grant.email,
        "scopes": list(grant.scopes),
        "encrypted_refresh_token": ciphertext,
        "showings_calendar_id": calendar_id,
        "status": "connected",
        "connected_by": started.by,
        "connected_at": now,
        "last_read_at": None,
        "last_read_ok": None,
        "last_error": None,
    }
    async with AsyncSessionFactory() as session, session.begin():
        connection_id = await session.scalar(
            insert(CalendarConnection)
            .values(google_subject=grant.subject, **values)
            .on_conflict_do_update(
                index_elements=["google_subject"], set_={**values, "updated_at": func.now()}
            )
            .returning(CalendarConnection.id)
        )
    _ACCESS_TOKENS.pop(connection_id, None)
    logfire.info(
        "showing bookings: calendar connected",
        connection_id=connection_id,
        user_id=user_id,
        reconnected=existing is not None,
    )
    return "connected"


# ---------------------------------------------------------------------------
# Looking after a connection
# ---------------------------------------------------------------------------


async def _load(connection_id: int) -> CalendarConnection:
    async with AsyncSessionFactory() as session:
        connection = await session.get(CalendarConnection, connection_id)
    if connection is None:
        raise LookupError(f"No calendar connection {connection_id}.")
    return connection


async def check_connection(connection_id: int, *, now: datetime) -> bool:
    """Read the account's own free/busy over the next day once, and save how it went.

    Returns whether the read worked. Raises ``LookupError`` for no such
    connection and ``ConnectionUnusable`` for one that cannot be used here.
    """
    connection = await _load(connection_id)
    problem = _problem(connection)
    if problem is not None:
        raise ConnectionUnusable(problem.detail)
    calendar = calendar_for_connection(connection)
    try:
        await calendar.busy_blocks([connection.google_email], now, now + CHECK_WINDOW)
    except CalendarUnreadable:
        return False
    return True


async def disconnect(connection_id: int, *, user_id: str) -> None:
    """Revoke the grant at Google, then mark the connection revoked and drop its token.

    Raises ``LookupError`` for no such connection, and ``CannotDisconnect``
    when Google cannot be told, which leaves the connection as it was.
    """
    connection = await _load(connection_id)
    ciphertext = connection.encrypted_refresh_token
    if ciphertext:
        try:
            refresh_token = token_crypto.decrypt(ciphertext)
        except token_crypto.TokenUnreadable as unreadable:
            raise CannotDisconnect(
                f"{unreadable.detail} Google cannot be told from here: disconnect it where it was "
                "connected, or remove the app's access in that Google account's settings."
            ) from None
        try:
            await google_oauth.revoke(refresh_token)
        except google_oauth.OAuthError as failure:
            raise CannotDisconnect(
                f"Google did not confirm the disconnection. {failure.detail} Try again."
            ) from None
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(CalendarConnection)
            .where(
                CalendarConnection.id == connection_id,
                CalendarConnection.encrypted_refresh_token.is_not_distinct_from(ciphertext),
            )
            .values(status="revoked", encrypted_refresh_token=None, last_error=None)
        )
    _ACCESS_TOKENS.pop(connection_id, None)
    logfire.info(
        "showing bookings: calendar disconnected", connection_id=connection_id, user_id=user_id
    )
