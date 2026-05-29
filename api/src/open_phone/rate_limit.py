"""Shared rate limiting + 429 retry for OpenPhone (Quo) API calls.

OpenPhone enforces a per-API-key rate limit (documented at ~10 requests/sec).
The Sernia AI agent fires bursts of parallel reads — e.g. ``list_active_sms_threads``
refreshes the paginated contact cache and then fans out ``/v1/messages`` +
``/v1/calls`` for every active conversation via ``asyncio.gather`` — which
periodically blew past that limit and came back ``429 Too Many Requests``.

Those 429s were non-fatal (callers swallow ``httpx.HTTPError`` and degrade
gracefully, e.g. dropping a thread snippet) but they:
  1. Logged at error level, tripping the "Error-level records (non-local)"
     Logfire alert (pure noise — the agent run still succeeded), and
  2. Silently dropped data (missing snippets / thread history).

The fix is to throttle *before* requests leave the process so we stay under
the limit, plus a bounded ``Retry-After``-aware retry as a safety net for the
rare residual 429. A single process-wide token bucket is shared across every
OpenPhone client (the central service client, the agent's Quo client, and the
FastMCP-bridged tools that reuse it) so concurrent agent runs can't collectively
exceed the limit.

Wire it up by passing ``transport=build_rate_limited_transport()`` when
constructing the ``httpx.AsyncClient`` — see ``service._openphone_client`` and
``quo_tools._build_quo_client``.
"""

import asyncio
import time

import httpx
import logfire

# OpenPhone documents 10 req/s per API key. Stay safely under it to leave
# headroom for clock skew, retries, and any traffic we don't route through
# the bucket (e.g. one-off sends).
MAX_REQUESTS_PER_SECOND = 8.0

# Safety-net retries for the rare 429 that slips past the throttle. Kept small:
# the token bucket is the real fix, retries just paper over jitter.
MAX_RETRIES = 3

# Fallback backoff (seconds) when the 429 response carries no ``Retry-After``
# header. Exponential: 0.5, 1.0, 2.0, ... capped at MAX_BACKOFF.
BASE_BACKOFF = 0.5
MAX_BACKOFF = 8.0


class _TokenBucket:
    """Async token bucket — smooths bursts to a sustained ``rate`` per second.

    ``capacity`` tokens accumulate while idle, allowing a short burst, then
    requests are paced at ``rate`` per second. Process-wide and asyncio-safe.
    """

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
                # Refill based on elapsed time since last update.
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) * self._rate,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            # Sleep outside the lock so other tasks can refill/recheck.
            await asyncio.sleep(wait)


# Process-wide bucket shared by every OpenPhone client.
_bucket = _TokenBucket(MAX_REQUESTS_PER_SECOND, MAX_REQUESTS_PER_SECOND)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value (delta-seconds) into seconds.

    OpenPhone returns delta-seconds; HTTP-date form is not handled (treated as
    absent so we fall back to exponential backoff).
    """
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


class RateLimitedTransport(httpx.AsyncBaseTransport):
    """httpx transport that paces requests through the shared token bucket and
    retries ``429`` responses with ``Retry-After``-aware backoff.

    Wraps a real ``AsyncHTTPTransport`` for connection pooling. Each request
    acquires a token before being sent, keeping aggregate throughput under the
    OpenPhone limit regardless of how many tasks fan out concurrently.
    """

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
            # Discard the throttled response body before retrying.
            await response.aclose()
            logfire.debug(
                "openphone 429 — retrying after {delay}s",
                delay=delay,
                attempt=attempt + 1,
                max_retries=MAX_RETRIES,
                url=str(request.url),
            )
            await asyncio.sleep(delay)

        # Unreachable in practice (loop always returns), but satisfies typing.
        assert response is not None
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_rate_limited_transport() -> RateLimitedTransport:
    """Construct a transport that throttles + retries OpenPhone requests."""
    return RateLimitedTransport()
