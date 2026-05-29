"""Shared rate limiting + 429 retry for OpenPhone (Quo) API calls.

VENDORED from ``api/src/open_phone/rate_limit.py`` (keep in sync — see the
vendored-client policy in ``apps/sernia_mcp/CLAUDE.md``).

OpenPhone enforces a per-API-key rate limit (~10 requests/sec). Bursts of
parallel reads (contact-cache pagination + per-conversation message/call
fan-out) periodically exceeded it and came back ``429 Too Many Requests``.
Those 429s were non-fatal but logged at error level (alert noise) and silently
dropped data. The fix throttles requests *before* they leave the process so we
stay under the limit, with a bounded ``Retry-After``-aware retry as a safety
net. A single process-wide token bucket is shared across every OpenPhone client.

Wire it up via ``transport=build_rate_limited_transport()`` in
``clients/quo.build_quo_client``.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import logfire

# OpenPhone documents 10 req/s per API key. Stay safely under it.
MAX_REQUESTS_PER_SECOND = 8.0

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


_bucket = _TokenBucket(MAX_REQUESTS_PER_SECOND, MAX_REQUESTS_PER_SECOND)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds) into seconds, else None."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


class RateLimitedTransport(httpx.AsyncBaseTransport):
    """httpx transport that paces requests through the shared token bucket and
    retries ``429`` responses with ``Retry-After``-aware backoff."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(MAX_RETRIES + 1):
            await _bucket.acquire()
            response = await self._inner.handle_async_request(request)
            if response.status_code != 429 or attempt == MAX_RETRIES:
                return response

            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            delay = (
                retry_after
                if retry_after is not None
                else min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            )
            await response.aclose()
            logfire.debug(
                "openphone 429 — retrying after {delay}s",
                delay=delay,
                attempt=attempt + 1,
                max_retries=MAX_RETRIES,
                url=str(request.url),
            )
            await asyncio.sleep(delay)

        assert response is not None
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_rate_limited_transport() -> RateLimitedTransport:
    """Construct a transport that throttles + retries OpenPhone requests."""
    return RateLimitedTransport()
