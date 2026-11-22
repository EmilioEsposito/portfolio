"""The one HTTP client this module uses to reach Google, and one request with an access token.

``google_oauth.py``, ``google_calendar.py`` and ``google_lists.py`` open every
connection to Google through ``client``. Tests replace it with an
``httpx.MockTransport`` client; under pytest the real one refuses, so no test
can reach Google by accident.
"""

import os
from contextlib import nullcontext
from typing import Protocol

import httpx
import logfire

# Google answers in well under a second; a much slower answer counts as a failure.
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class LiveGoogleInTests(RuntimeError):
    """A test reached for the real Google client instead of a mock transport."""


class AccessTokens(Protocol):
    async def access_token(self, *, fresh: bool = False) -> str:
        """A current access token; ``fresh`` asks Google for a new one.

        Raises ``google_oauth.GrantLost`` when Google no longer accepts the
        grant, and ``google_oauth.OAuthError`` when no token can be had now.
        """
        ...


def client() -> httpx.AsyncClient:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise LiveGoogleInTests(
            "Tests reach Google through httpx.MockTransport; the real client is never used in tests."
        )
    return httpx.AsyncClient(timeout=TIMEOUT)


async def call(
    tokens: AccessTokens,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    traced: bool = True,
) -> httpx.Response:
    """One request with a current access token. A 401 gets a new token and one
    more try: Google refuses an expired token before doing anything, so a
    second try cannot repeat a change.

    The HTTP client's tracing records the whole URL, query included. Pass
    ``traced=False`` when the query must stay out of it.
    """
    for fresh in (False, True):
        token = await tokens.access_token(fresh=fresh)
        async with client() as http:
            # Only the request: what the token refresh logs still goes out.
            with nullcontext() if traced else logfire.suppress_instrumentation():
                response = await http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
        if response.status_code != 401:
            break
    return response
