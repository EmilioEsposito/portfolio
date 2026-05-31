"""Shared rate limiting + 429 retry for OpenPhone (Quo) API calls.

VENDORED from ``api/src/open_phone/rate_limit.py`` (keep in sync — see the
vendored-client policy in ``apps/sernia_mcp/CLAUDE.md``).

OpenPhone enforces a per-API-key rate limit (~10 requests/sec). Bursts of
parallel reads periodically exceeded it and came back ``429 Too Many Requests``
— non-fatal but logged at error level (alert noise) and silently dropping data.

The fix has two parts:

  * **Throttle before requests leave the process** via a single process-wide,
    *low-burst* token bucket, so a fan-out of parallel requests can't fire all
    at once and trip the limit.
  * **One span per logical request, level set from the FINAL status.** Each
    inner attempt runs under ``logfire.suppress_instrumentation()`` (so a
    retried 429 leaves no error-level span), and the transport emits a single
    span whose level reflects the final outcome — recovered 429 → info (no
    alert); genuine 4xx/5xx (or a 429 that exhausts retries) → error.

Wire it up via ``transport=build_rate_limited_transport()`` in
``clients/quo.build_quo_client``.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import logfire

# OpenPhone documents 10 req/s per API key. Sustained pace, under the limit.
MAX_REQUESTS_PER_SECOND = 6.0

# Max instantaneous burst. Small on purpose: rate=6 + capacity=3 → worst case
# ~9 requests in any one-second window, safely under 10.
BURST_CAPACITY = 3.0

# Safety-net retries for the rare 429 that slips past the throttle.
MAX_RETRIES = 3

# Fallback backoff (seconds) when the 429 carries no ``Retry-After`` header.
BASE_BACKOFF = 0.5
MAX_BACKOFF = 8.0


class _TokenBucket:
    """Async token bucket — smooths bursts to a sustained ``rate`` per second."""

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) * self._rate,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


_bucket = _TokenBucket(MAX_REQUESTS_PER_SECOND, BURST_CAPACITY)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds) into seconds, else None."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


def _level_for_status(status_code: int) -> str | None:
    """Logfire level for a request's *final* status, or None for default.

    A 429 retried into a 2xx lands at the default (info) level (no alert);
    genuine errors (incl. a 429 that exhausts retries) surface at error level.
    """
    if status_code >= 400:
        return "error"
    return None


class RateLimitedTransport(httpx.AsyncBaseTransport):
    """httpx transport that paces requests through the shared token bucket and
    retries ``429`` responses with ``Retry-After``-aware backoff. Per-attempt
    instrumentation is suppressed; one span per request is emitted with a level
    driven by the final status (see module doc)."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        url = str(request.url)
        response: httpx.Response | None = None
        attempts = 0

        with logfire.span("{method} {url}", method=method, url=url, _span_name=method) as span:
            for attempt in range(MAX_RETRIES + 1):
                attempts += 1
                await _bucket.acquire()
                with logfire.suppress_instrumentation():
                    response = await self._inner.handle_async_request(request)
                if response.status_code != 429 or attempt == MAX_RETRIES:
                    break

                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                delay = (
                    retry_after
                    if retry_after is not None
                    else min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                )
                await response.aclose()
                await asyncio.sleep(delay)

            assert response is not None
            span.set_attribute("http.method", method)
            span.set_attribute("http.url", url)
            span.set_attribute("http.status_code", response.status_code)
            if attempts > 1:
                span.set_attribute("openphone.attempts", attempts)
            level = _level_for_status(response.status_code)
            if level is not None:
                span.set_level(level)

        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_rate_limited_transport() -> RateLimitedTransport:
    """Construct a transport that throttles + retries OpenPhone requests."""
    return RateLimitedTransport()
