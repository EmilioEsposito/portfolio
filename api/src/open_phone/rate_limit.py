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

The fix has two parts:

  * **Throttle before requests leave the process.** A single process-wide token
    bucket paces all OpenPhone traffic to stay under the limit. The bucket is
    deliberately *low-burst* (small ``capacity``) so a fan-out of N parallel
    requests can't all fire in the same instant and trip the limit — the
    earlier version allowed an 8-request burst and still drew 429s.

  * **One span per logical request, level set from the FINAL status.** httpx's
    auto-instrumentation emits a span per transport attempt and flags any 4xx
    at error level — so a 429 that we *successfully retried* still left an
    error-level span behind and paged us. Instead, each inner attempt runs
    under ``logfire.suppress_instrumentation()`` (no per-attempt span) and the
    transport emits a single span whose level reflects the final outcome: a
    recovered 429 → final 200 → info (no alert); a genuine 4xx/5xx (or a 429
    that exhausts retries) → error, so real problems still page.

A single process-wide bucket is shared across every OpenPhone client (the
central service client, the agent's Quo client, and the FastMCP-bridged tools
that reuse it) so concurrent agent runs can't collectively exceed the limit.

Wire it up by passing ``transport=build_rate_limited_transport()`` when
constructing the ``httpx.AsyncClient`` — see ``service._openphone_client`` and
``quo_tools._build_quo_client``.
"""

import asyncio
import time

import httpx
import logfire

# OpenPhone documents 10 req/s per API key. Sustained pace, kept under the
# limit with headroom for clock skew and traffic we don't route through the
# bucket (e.g. one-off sends).
MAX_REQUESTS_PER_SECOND = 6.0

# Max instantaneous burst. Small on purpose: with rate=6 and capacity=3 the
# worst case in any one-second window is ~9 requests, safely under 10. A larger
# capacity is what let the previous version trip the limit on fan-out.
BURST_CAPACITY = 3.0

# Safety-net retries for the rare 429 that slips past the throttle.
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
_bucket = _TokenBucket(MAX_REQUESTS_PER_SECOND, BURST_CAPACITY)


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


def _level_for_status(status_code: int) -> str | None:
    """Logfire level for a request's *final* status, or None for the default.

    Mirrors httpx auto-instrumentation (4xx/5xx → error) but applies only to
    the final outcome — so a 429 retried into a 2xx lands at the default
    (info) level and doesn't trip the error-level alert, while genuine errors
    (including a 429 that exhausts retries) still surface at error level.
    """
    if status_code >= 400:
        return "error"
    return None


class RateLimitedTransport(httpx.AsyncBaseTransport):
    """httpx transport that paces requests through the shared token bucket and
    retries ``429`` responses with ``Retry-After``-aware backoff.

    Wraps a real ``AsyncHTTPTransport`` for connection pooling. Each request
    acquires a token before being sent, keeping aggregate throughput under the
    OpenPhone limit regardless of how many tasks fan out concurrently. Per-
    attempt httpx instrumentation is suppressed; the transport emits one span
    per logical request whose level reflects the final status (see module doc).
    """

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
                # Suppress per-attempt auto-instrumentation: a retried 429 would
                # otherwise leave an error-level span behind and page us. We
                # record one span (this context) for the logical request below.
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
                # Discard the throttled response body before retrying.
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
