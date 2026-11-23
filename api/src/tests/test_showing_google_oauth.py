"""Google's OAuth endpoints, the signed state and the token key. No database.

Google is replaced by ``FakeGoogle`` behind ``httpx.MockTransport``. Its
answers are shaped as Google's documentation describes; none has been checked
against Google yet.
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from api.src.showing_bookings import google_http, google_oauth, token_crypto
from api.src.tests.showing_google import (
    CLIENT_ID,
    CLIENT_SECRET,
    ORIGIN,
    OTHER_TOKEN_KEY,
    REDIRECT_URI,
    TEAM_ACCOUNT,
    TEAM_SUBJECT,
    Account,
    FakeGoogle,
    configure,
    id_token,
)

TEAM_MEMBER = "pat@serniacapital.com"


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    configure(monkeypatch)
    return FakeGoogle().install(monkeypatch)


def sign(origin: str = ORIGIN) -> str:
    return google_oauth.sign_state(user_id="user_test", by=TEAM_MEMBER, origin=origin)


# The signed state


def test_a_state_carries_who_started_and_where():
    started = google_oauth.read_state(sign())
    assert (started.user_id, started.by, started.origin) == ("user_test", TEAM_MEMBER, ORIGIN)
    assert len(started.nonce) >= 16
    # Each state is different, even for the same person and place.
    assert google_oauth.read_state(sign()).nonce != started.nonce


@pytest.mark.parametrize("state", [None, ""])
def test_a_missing_state_is_refused(state):
    with pytest.raises(google_oauth.StateRejected) as refused:
        google_oauth.read_state(state)
    assert refused.value.reason == "missing"


def test_an_altered_state_is_refused():
    state = sign()
    payload, signature = state.rsplit(".", 1)
    tampered = [
        f"{payload[:-1]}{'A' if payload[-1] != 'A' else 'B'}.{signature}",
        f"{payload}.{signature[::-1]}",
        "not-a-state",
    ]
    for value in tampered:
        with pytest.raises(google_oauth.StateRejected) as refused:
            google_oauth.read_state(value)
        assert refused.value.reason == "invalid", value


def test_a_state_signed_by_another_environment_is_refused(monkeypatch):
    state = sign()
    monkeypatch.setenv("SESSION_SECRET_KEY", "another-environments-secret")
    with pytest.raises(google_oauth.StateRejected) as refused:
        google_oauth.read_state(state)
    assert refused.value.reason == "invalid"


def test_a_state_older_than_ten_minutes_is_refused(monkeypatch):
    import itsdangerous.timed

    real_time = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real_time() - 601)
    state = sign()
    monkeypatch.setattr(itsdangerous.timed.time, "time", real_time)

    with pytest.raises(google_oauth.StateRejected) as refused:
        google_oauth.read_state(state)
    assert refused.value.reason == "expired"


@pytest.mark.parametrize(
    # eesposito.com: a connection started there before the team pages moved.
    "origin",
    ["https://elsewhere.example.com", "https://eesposito.com"],
)
def test_a_state_naming_an_origin_off_the_list_is_refused(origin):
    forged = google_oauth._serializer().dumps(
        {"u": "user_test", "b": TEAM_MEMBER, "o": origin, "n": "x" * 22}
    )
    with pytest.raises(google_oauth.StateRejected) as refused:
        google_oauth.read_state(forged)
    assert refused.value.reason == "invalid"


# Where a connection may start


@pytest.mark.parametrize(
    "origin, allowed",
    [
        ("https://serniacapital.com", "https://serniacapital.com"),
        ("https://dev.eesposito.com/", "https://dev.eesposito.com"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://localhost", "http://localhost"),
        # The portfolio site no longer serves the team pages.
        ("https://eesposito.com", None),
        ("http://serniacapital.com", None),
        ("https://serniacapital.com.example.com", None),
        ("https://www.serniacapital.com", None),
        ("https://react-router-portfolio-pr-1.up.railway.app", None),
        ("http://127.0.0.1:5173", None),
        ("http://localhost:5173/systems", None),
        ("localhost:5173", None),
        ("", None),
        (None, None),
    ],
)
def test_origins_come_from_the_allow_list(origin, allowed):
    assert google_oauth.allowed_origin(origin) == allowed


def test_a_redirect_uri_is_built_only_on_an_allowed_origin():
    assert google_oauth.redirect_uri("https://serniacapital.com") == (
        "https://serniacapital.com/api/showing-bookings/calendar/callback"
    )
    assert google_oauth.redirect_uri("https://dev.eesposito.com") == (
        "https://dev.eesposito.com/api/showing-bookings/calendar/callback"
    )
    with pytest.raises(ValueError):
        google_oauth.redirect_uri("https://eesposito.com")
    with pytest.raises(ValueError):
        google_oauth.redirect_uri("https://elsewhere.example.com")
    with pytest.raises(ValueError):
        sign("https://elsewhere.example.com")


def test_the_consent_url_asks_for_offline_access_to_the_calendar_and_picker_scopes(google):
    client = google_oauth.oauth_client()
    url = google_oauth.consent_url(
        client, redirect_to=REDIRECT_URI, state="the-state", login_hint=TEAM_ACCOUNT
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == google_oauth.AUTH_URL
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    assert set(query.pop("scope").split()) == {
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.events.freebusy",
        "https://www.googleapis.com/auth/calendar.app.created",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/directory.readonly",
    }
    assert query == {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "state": "the-state",
        "login_hint": TEAM_ACCOUNT,
    }
    assert CLIENT_SECRET not in url


# Codes and tokens


@pytest.mark.asyncio
async def test_a_code_is_exchanged_for_the_account_and_its_tokens(google):
    code = google.approve()

    grant = await google_oauth.exchange_code(code, redirect_to=REDIRECT_URI)

    assert (grant.subject, grant.email, grant.missing_scopes) == (TEAM_SUBJECT, TEAM_ACCOUNT, [])
    assert grant.refresh_token in google.refresh_tokens
    (request,) = google.sent("POST", "/token")
    assert google.form(request) == {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert code not in str(request.url)
    # Tokens never show up when the grant is printed or logged.
    assert grant.refresh_token not in repr(grant) and grant.access_token not in repr(grant)


@pytest.mark.asyncio
async def test_the_permissions_left_unticked_are_reported(google):
    narrow = Account(TEAM_SUBJECT, TEAM_ACCOUNT, scopes=("openid", google_oauth.FREEBUSY_SCOPE))
    grant = await google_oauth.exchange_code(google.approve(narrow), redirect_to=REDIRECT_URI)
    assert grant.missing_scopes == [google_oauth.APP_CALENDARS_SCOPE]


@pytest.mark.asyncio
async def test_the_pickers_permissions_are_never_a_condition(google):
    """Only free/busy and the app's own calendars are required of a grant."""
    assert google_oauth.CALENDAR_SCOPES == (
        "https://www.googleapis.com/auth/calendar.events.freebusy",
        "https://www.googleapis.com/auth/calendar.app.created",
    )
    old = Account(TEAM_SUBJECT, TEAM_ACCOUNT, scopes=("openid", *google_oauth.CALENDAR_SCOPES))
    grant = await google_oauth.exchange_code(google.approve(old), redirect_to=REDIRECT_URI)
    assert grant.missing_scopes == []


@pytest.mark.asyncio
async def test_a_used_or_unknown_code_is_refused_without_quoting_it(google):
    code = google.approve()
    await google_oauth.exchange_code(code, redirect_to=REDIRECT_URI)

    with pytest.raises(google_oauth.OAuthError) as refused:
        await google_oauth.exchange_code(code, redirect_to=REDIRECT_URI)
    assert refused.value.reason == "invalid_grant"
    assert code not in str(refused.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        {"access_token": "ya29.x", "id_token": "a.b.c"},  # no refresh token
        {"refresh_token": "1//r", "access_token": "ya29.x"},  # no account
        {
            "refresh_token": "1//r",
            "access_token": "ya29.x",
            "id_token": id_token(
                {
                    "iss": "https://accounts.google.com",
                    "aud": "another-client",
                    "sub": "1",
                    "email": "a@example.com",
                }
            ),
        },
        {
            "refresh_token": "1//r",
            "access_token": "ya29.x",
            "id_token": id_token(
                {
                    "iss": "https://elsewhere.example.com",
                    "aud": CLIENT_ID,
                    "sub": "1",
                    "email": "a@example.com",
                }
            ),
        },
    ],
)
async def test_an_answer_missing_the_token_or_the_account_is_refused(google, answer):
    google.forced[("POST", "/token")] = httpx.Response(200, json=answer)
    with pytest.raises(google_oauth.OAuthError) as refused:
        await google_oauth.exchange_code("4/code", redirect_to=REDIRECT_URI)
    assert refused.value.reason == "incomplete"


@pytest.mark.asyncio
async def test_a_wrong_client_secret_is_the_clients_problem(google, monkeypatch):
    monkeypatch.setenv(google_oauth.CLIENT_SECRET_ENV, "not-the-secret")
    with pytest.raises(google_oauth.OAuthError) as refused:
        await google_oauth.exchange_code(google.approve(), redirect_to=REDIRECT_URI)
    assert refused.value.reason == "invalid_client"


@pytest.mark.asyncio
async def test_without_the_client_nothing_reaches_google(google, monkeypatch):
    monkeypatch.delenv(google_oauth.CLIENT_ID_ENV)
    assert google_oauth.oauth_client() is None
    with pytest.raises(google_oauth.OAuthError) as refused:
        await google_oauth.exchange_code("4/code", redirect_to=REDIRECT_URI)
    assert refused.value.reason == "not_configured"
    assert google.requests == []


@pytest.mark.asyncio
async def test_a_refresh_gives_a_new_access_token(google):
    refresh_token = google.refresh_token_for()

    token = await google_oauth.refresh_access_token(refresh_token)

    assert token.value in google.access_tokens and token.expires_in == google.access_lifetime
    assert token.value not in repr(token)
    (request,) = google.sent("POST", "/token")
    assert google.form(request)["grant_type"] == "refresh_token"


@pytest.mark.asyncio
async def test_a_refresh_token_google_rejects_is_a_lost_grant(google):
    refresh_token = google.refresh_token_for()
    google.revoke_everything()

    with pytest.raises(google_oauth.GrantLost) as lost:
        await google_oauth.refresh_access_token(refresh_token)
    assert refresh_token not in str(lost.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.Response(503, json={})],
)
async def test_google_out_of_reach_is_reported_as_unreachable(google, answer):
    google.forced[("POST", "/token")] = answer
    with pytest.raises(google_oauth.OAuthError) as failure:
        await google_oauth.refresh_access_token(google.refresh_token_for())
    assert failure.value.reason == "unreachable"
    assert not isinstance(failure.value, google_oauth.GrantLost)


@pytest.mark.asyncio
async def test_a_revocation_sends_the_token_in_the_body_not_the_address(google):
    refresh_token = google.refresh_token_for()

    await google_oauth.revoke(refresh_token)

    (request,) = google.sent("POST", "/revoke")
    assert google.form(request) == {"token": refresh_token}
    assert request.url.query == b""
    assert google.refresh_tokens == {}
    # Revoking a token Google already rejects is not an error.
    await google_oauth.revoke(refresh_token)


@pytest.mark.asyncio
async def test_a_revocation_google_cannot_confirm_is_an_error(google):
    google.forced[("POST", "/revoke")] = httpx.Response(503)
    with pytest.raises(google_oauth.OAuthError) as failure:
        await google_oauth.revoke("1//test")
    assert failure.value.reason == "unreachable"


def test_the_real_client_never_reaches_google_from_a_test():
    with pytest.raises(google_http.LiveGoogleInTests):
        google_http.client()


# The token key


def test_a_refresh_token_is_stored_encrypted_and_reads_back(monkeypatch):
    configure(monkeypatch)
    ciphertext = token_crypto.encrypt("1//test-refresh-token")

    assert "1//test-refresh-token" not in ciphertext
    assert token_crypto.decrypt(ciphertext) == "1//test-refresh-token"
    assert token_crypto.key_problem() is None


def test_without_the_key_a_token_can_be_neither_saved_nor_read(monkeypatch):
    configure(monkeypatch)
    ciphertext = token_crypto.encrypt("1//test-refresh-token")
    monkeypatch.delenv(token_crypto.TOKEN_KEY_ENV)

    assert token_crypto.key_problem().reason == "no_key"
    for attempt in (lambda: token_crypto.encrypt("1//x"), lambda: token_crypto.decrypt(ciphertext)):
        with pytest.raises(token_crypto.TokenUnreadable) as unreadable:
            attempt()
        assert unreadable.value.reason == "no_key"


def test_a_token_saved_with_another_key_cannot_be_read(monkeypatch):
    configure(monkeypatch)
    ciphertext = token_crypto.encrypt("1//test-refresh-token")
    monkeypatch.setenv(token_crypto.TOKEN_KEY_ENV, OTHER_TOKEN_KEY)

    with pytest.raises(token_crypto.TokenUnreadable) as unreadable:
        token_crypto.decrypt(ciphertext)
    assert unreadable.value.reason == "wrong_key"
    assert "1//test-refresh-token" not in str(unreadable.value)


def test_a_malformed_key_is_reported(monkeypatch):
    monkeypatch.setenv(token_crypto.TOKEN_KEY_ENV, "not a key")
    assert token_crypto.key_problem().reason == "bad_key"
    with pytest.raises(token_crypto.TokenUnreadable):
        token_crypto.encrypt("1//x")
