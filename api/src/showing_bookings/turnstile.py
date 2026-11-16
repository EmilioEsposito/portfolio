"""Cloudflare Turnstile, checked on the server before a public booking or the team number.

The page shows Turnstile's widget with the site key and sends the token it
gets; ``verify`` asks Cloudflare whether that token is good. A token works
once, so each action the page takes needs a fresh one.

Keys are read at call time:

- ``TURNSTILE_SECRET_KEY``: the secret. In production, a missing secret
  refuses every check (``TurnstileUnavailable``), so nothing is booked
  unchecked. Elsewhere, a missing secret uses Cloudflare's published test
  secret that always passes, answered here without a call (see ``verify``).
- ``TURNSTILE_SITE_KEY``: the site key the page shows. Outside production,
  a missing one uses the test site key that always passes.

Cloudflare's test keys:
https://developers.cloudflare.com/turnstile/troubleshooting/testing/

Every request to Cloudflare goes through ``client``. Under pytest the real
client refuses, so tests reach Cloudflare only through ``httpx.MockTransport``.
"""

import os

import httpx
import logfire

from api.src.showing_bookings.settings import current_environment

SECRET_ENV = "TURNSTILE_SECRET_KEY"
SITE_KEY_ENV = "TURNSTILE_SITE_KEY"
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# Cloudflare's published test keys, which always pass.
TEST_SECRET = "1x0000000000000000000000000000000AA"
TEST_SITE_KEY = "1x00000000000000000000AA"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_TOKEN_LENGTH = 2048


class TurnstileUnavailable(RuntimeError):
    """No secret is set in production, so no check can pass."""


class LiveTurnstileInTests(RuntimeError):
    """A test reached for the real Cloudflare client instead of a mock transport."""


def _production() -> bool:
    return current_environment() == "production"


def secret_key() -> str:
    secret = os.getenv(SECRET_ENV, "").strip()
    if secret:
        return secret
    if _production():
        raise TurnstileUnavailable(f"{SECRET_ENV} is not set.")
    return TEST_SECRET


def site_key() -> str | None:
    """The site key the page shows, or None in production when it is not set."""
    key = os.getenv(SITE_KEY_ENV, "").strip()
    if key:
        return key
    return None if _production() else TEST_SITE_KEY


def client() -> httpx.AsyncClient:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise LiveTurnstileInTests(
            "Tests reach Cloudflare through httpx.MockTransport; the real client is never used."
        )
    return httpx.AsyncClient(timeout=TIMEOUT)


async def verify(token: str | None, remote_ip: str | None = None) -> bool:
    """True when Cloudflare says ``token`` is good. Raises ``TurnstileUnavailable``.

    A missing token, an unreachable Cloudflare or an unreadable answer is a
    failed check, never a pass.

    Outside production, with Cloudflare's published test secret in use, any
    non-empty token passes without a call to Cloudflare: that is the answer
    Cloudflare gives for that secret, and it keeps local runs and the browser
    test from reaching anything outside. Production always asks Cloudflare.
    """
    secret = secret_key()
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False
    if secret == TEST_SECRET and not _production():
        return True
    form = {"secret": secret, "response": token}
    if remote_ip:
        form["remoteip"] = remote_ip
    try:
        async with client() as http:
            response = await http.post(VERIFY_URL, data=form)
        answer = response.json()
    except LiveTurnstileInTests:
        raise
    except Exception as exc:
        logfire.warn("turnstile check could not be completed", error=type(exc).__name__)
        return False
    ok = bool(isinstance(answer, dict) and answer.get("success") is True)
    if not ok:
        codes = answer.get("error-codes") if isinstance(answer, dict) else None
        logfire.info("turnstile check failed", error_codes=codes)
    return ok
