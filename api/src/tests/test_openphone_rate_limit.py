"""Unit tests for the OpenPhone rate-limiting + 429-retry transport.

These guard the denoising fix: bursts of OpenPhone reads must stay under the
per-key rate limit, and the rare residual 429 must be retried (Retry-After
aware) instead of bubbling up as an error-level log that pages the team.
"""
import asyncio
import time

import httpx
import pytest

from api.src.open_phone.rate_limit import (
    MAX_RETRIES,
    RateLimitedTransport,
    _level_for_status,
    _parse_retry_after,
    _TokenBucket,
)


@pytest.mark.parametrize(
    "status, expected",
    [
        (200, None),
        (202, None),
        (404, "error"),
        (429, "error"),  # only seen here when retries are exhausted
        (500, "error"),
        (503, "error"),
    ],
)
def test_level_for_status(status, expected):
    # A recovered 429 reaches this helper as its final 2xx status, so it maps
    # to None (default/info) — no error-level span, no alert.
    assert _level_for_status(status) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0", 0.0),
        ("2", 2.0),
        ("  3.5 ", 3.5),
        ("-1", 0.0),  # clamped to 0
        (None, None),
        ("", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", None),  # HTTP-date not handled
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


@pytest.mark.asyncio
async def test_transport_retries_429_then_succeeds():
    """A 429 followed by a 200 should transparently return the 200."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # Retry-After: 0 keeps the test fast.
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    assert calls["n"] == 2  # one failure + one success


@pytest.mark.asyncio
async def test_transport_gives_up_after_max_retries():
    """Persistent 429s exhaust the retry budget and return the final 429."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 429
    # Initial attempt + MAX_RETRIES retries.
    assert calls["n"] == MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_transport_passes_through_non_429():
    """Non-429 responses are returned on the first attempt, no retry."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/calls/bogus")

    assert resp.status_code == 404
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_token_bucket_throttles_burst():
    """With capacity exhausted, the next acquire waits ~1/rate seconds."""
    # rate=20/s, capacity=2: first two acquires are instant (burst), the third
    # must wait for a token to refill (~0.05s).
    bucket = _TokenBucket(rate=20.0, capacity=2.0)
    await bucket.acquire()
    await bucket.acquire()

    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start

    # Allow generous slack for scheduler jitter, but it must have waited.
    assert elapsed >= 0.03


@pytest.mark.asyncio
async def test_token_bucket_allows_initial_burst():
    """Up to `capacity` acquires complete without throttling."""
    bucket = _TokenBucket(rate=1.0, capacity=5.0)
    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(5)))
    elapsed = time.monotonic() - start
    # Five tokens were pre-loaded; none should have blocked.
    assert elapsed < 0.05
