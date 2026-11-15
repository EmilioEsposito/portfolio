"""The one HTTP client this module uses to reach Google.

``google_oauth.py`` and ``google_calendar.py`` open every connection to Google
through ``client``. Tests replace it with an ``httpx.MockTransport`` client;
under pytest the real one refuses, so no test can reach Google by accident.
"""

import os

import httpx

# Google answers in well under a second; a much slower answer counts as a failure.
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class LiveGoogleInTests(RuntimeError):
    """A test reached for the real Google client instead of a mock transport."""


def client() -> httpx.AsyncClient:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise LiveGoogleInTests(
            "Tests reach Google through httpx.MockTransport; the real client is never used in tests."
        )
    return httpx.AsyncClient(timeout=TIMEOUT)
