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

The transport also retries *transient network* failures, but only where a
retry is provably safe:

  * **Connection-level errors** (``ConnectError``, ``ConnectTimeout``,
    ``PoolTimeout``) mean the request never reached OpenPhone, so replaying it
    can't duplicate a side effect. Retried for every method.

  * **Read/write-level errors** (``ReadTimeout``, ``WriteTimeout``,
    ``RemoteProtocolError``) mean the request *was* sent and we simply never
    read the response. Replaying a ``POST /v1/messages`` in that state could
    send the same SMS twice, so these are retried for idempotent methods only
    (GET/HEAD/OPTIONS/PUT/DELETE). For POSTs the correct mitigation is a
    generous client timeout, not a retry — see ``service._openphone_client``.

Note that a transport returns as soon as response *headers* arrive, with the
body streamed afterwards — so a stall mid-body raises from the caller's
``aread()``, outside the retry loop. For replayable methods the body is
therefore buffered inside the retry boundary and handed back as an
already-read response, so those failures are retried too rather than escaping.

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

# Safety-net retries for the rare 429 that slips past the throttle. Also caps
# transient-network-error retries (see IDEMPOTENT_METHODS below).
MAX_RETRIES = 3

# Methods that may be safely replayed after the request was already sent.
# POST is deliberately absent: a retried POST /v1/messages would double-send.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# Errors meaning the request never reached OpenPhone — safe to retry for any
# method, since no side effect can have been applied.
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Errors meaning the request was sent but the exchange broke down. Safe to
# retry only for idempotent methods.
IN_FLIGHT_ERRORS = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


def _is_retryable_error(exc: Exception, method: str) -> bool:
    """Whether *exc* on a *method* request may be safely retried."""
    if isinstance(exc, CONNECT_ERRORS):
        return True
    if isinstance(exc, IN_FLIGHT_ERRORS):
        return method.upper() in IDEMPOTENT_METHODS
    return False


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


# Extension keys worth preserving when a response is rebuilt around its
# buffered body. Deliberately excludes live-connection objects (e.g.
# ``network_stream``), which must not outlive the response we just closed.
SAFE_EXTENSION_KEYS = ("http_version", "reason_phrase")


def _carryover_extensions(extensions: dict | None) -> dict:
    """Metadata-only subset of *extensions*, safe to attach to a new Response."""
    if not extensions:
        return {}
    return {k: extensions[k] for k in SAFE_EXTENSION_KEYS if k in extensions}


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
    """httpx transport that paces requests through the shared token bucket,
    retries ``429`` responses with ``Retry-After``-aware backoff, and retries
    transient network errors where replaying the request is safe.

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
                try:
                    with logfire.suppress_instrumentation():
                        response = await self._inner.handle_async_request(request)
                except Exception as exc:
                    # Transient network blips (OpenPhone edge hiccup, DNS, a
                    # dropped connection) surface here rather than as a status
                    # code. Retry only where replaying can't duplicate a side
                    # effect; otherwise let the caller see the real error.
                    if attempt == MAX_RETRIES or not _is_retryable_error(exc, method):
                        span.set_attribute("openphone.attempts", attempts)
                        raise
                    logfire.warn(
                        "openphone request failed with {error_type}, retrying",
                        error_type=type(exc).__name__,
                        method=method,
                        url=url,
                        attempt=attempts,
                    )
                    await asyncio.sleep(min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF))
                    continue

                if response.status_code != 429 or attempt == MAX_RETRIES:
                    # A transport returns as soon as headers land; the body is
                    # streamed afterwards. If OpenPhone stalls or drops
                    # mid-body, httpx raises ReadTimeout/ReadError from the
                    # caller's `aread()` — outside this loop — so the retry
                    # guarantee above would silently not apply. Buffer the body
                    # here, inside the retry boundary, for methods we're allowed
                    # to replay.
                    if method.upper() in IDEMPOTENT_METHODS:
                        try:
                            with logfire.suppress_instrumentation():
                                content = await response.aread()
                        except Exception as exc:
                            await response.aclose()
                            if attempt == MAX_RETRIES or not _is_retryable_error(exc, method):
                                span.set_attribute("openphone.attempts", attempts)
                                raise
                            logfire.warn(
                                "openphone response body failed with {error_type}, retrying",
                                error_type=type(exc).__name__,
                                method=method,
                                url=url,
                                attempt=attempts,
                            )
                            await asyncio.sleep(min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF))
                            continue
                        await response.aclose()
                        # Hand back an already-buffered response so httpx's own
                        # read() is a no-op and can't fail a second time.
                        response = httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            content=content,
                            request=request,
                            extensions=_carryover_extensions(response.extensions),
                        )
                    break

                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                delay = (
                    retry_after
                    if retry_after is not None
                    else min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF)
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
