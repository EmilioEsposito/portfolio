"""Unit tests for the OpenPhone rate-limiting + 429-retry transport.

These guard the denoising fix: bursts of OpenPhone reads must stay under the
per-key rate limit, and the rare residual 429 must be retried (Retry-After
aware) instead of bubbling up as an error-level log that pages the team.
"""

import asyncio
import gzip
import time
from unittest import mock

import httpx
import pytest

from api.src.open_phone.rate_limit import (
    MAX_RETRIES,
    RateLimitedTransport,
    _is_retryable_error,
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


# ---- transient network error retries ----


@pytest.mark.parametrize(
    "exc, method, expected",
    [
        # Never reached OpenPhone — safe to replay for any method.
        (httpx.ConnectError("boom"), "GET", True),
        (httpx.ConnectError("boom"), "POST", True),
        (httpx.ConnectTimeout("boom"), "POST", True),
        (httpx.PoolTimeout("boom"), "POST", True),
        # Request was sent — replay only when idempotent.
        (httpx.ReadTimeout("boom"), "GET", True),
        (httpx.ReadTimeout("boom"), "DELETE", True),
        (httpx.ReadTimeout("boom"), "POST", False),  # would double-send an SMS
        (httpx.RemoteProtocolError("boom"), "POST", False),
        (httpx.WriteTimeout("boom"), "POST", False),
        # Not a transport blip at all.
        (ValueError("boom"), "GET", False),
    ],
)
def test_is_retryable_error(exc, method, expected):
    assert _is_retryable_error(exc, method) is expected


@pytest.mark.asyncio
async def test_transport_retries_read_timeout_on_get():
    """A transient ReadTimeout on an idempotent GET is retried, not raised."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_transport_does_not_retry_read_timeout_on_post():
    """A POST that timed out mid-flight must NOT be replayed — the message may
    already have been sent, and a retry would deliver a duplicate SMS."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post("/v1/messages", json={"content": "hi"})

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_transport_retries_connect_error_on_post():
    """A connect-level failure never reached OpenPhone, so even a POST is
    safe to replay."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(202, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.post("/v1/messages", json={"content": "hi"})

    assert resp.status_code == 202
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_transport_gives_up_on_persistent_connect_error():
    """Retryable network errors still surface once the budget is exhausted."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/v1/contacts")

    assert calls["n"] == MAX_RETRIES + 1


# ---- body-phase (post-header) failures ----


class _FailingStream(httpx.AsyncByteStream):
    """Response stream that raises partway through the body, mimicking
    OpenPhone returning headers and then stalling/disconnecting."""

    def __init__(self, exc: Exception, chunks: list[bytes] | None = None) -> None:
        self._exc = exc
        self._chunks = chunks or []

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise self._exc


@pytest.mark.asyncio
async def test_transport_retries_body_read_timeout_on_get():
    """A transport returns once headers land; the body streams afterwards. A
    stall mid-body must still be retried for idempotent methods, otherwise the
    retry guarantee silently doesn't apply to the most common timeout shape."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200, stream=_FailingStream(httpx.ReadTimeout("stalled", request=request))
            )
        return httpx.Response(200, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_transport_does_not_retry_body_failure_on_post():
    """POST bodies are never replayed, even when the failure is body-phase."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            202, stream=_FailingStream(httpx.ReadTimeout("stalled", request=request))
        )

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post("/v1/messages", json={"content": "hi"})

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_buffered_response_preserves_status_headers_and_body():
    """Rebuilding the response around its buffered body must not lose
    status, headers, or content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Custom": "abc", "Content-Type": "application/json"},
            json={"data": [1, 2, 3]},
        )

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    assert resp.headers["X-Custom"] == "abc"
    assert resp.json() == {"data": [1, 2, 3]}


class _RawStream(httpx.AsyncByteStream):
    """Response stream that yields raw (undecoded) wire bytes, so httpx applies
    the ``Content-Encoding`` decoder on read — mimicking a real gzipped
    OpenPhone response rather than MockTransport's pre-decoded ``content=``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_buffered_response_decodes_gzip_body():
    """Regression guard: OpenPhone/Cloudflare gzip their responses. ``aread()``
    hands back the DECODED body, so rebuilding the buffered response around the
    original headers (which still say ``Content-Encoding: gzip``) made httpx
    re-run the gzip decoder on already-plain bytes and raise
    ``DecodingError: Error -3 while decompressing data: incorrect header
    check``. The rebuild must strip the stale wire-framing headers."""
    payload = {"data": [{"id": "AC1", "body": "hello world"}]}
    body = gzip.compress(b'{"data": [{"id": "AC1", "body": "hello world"}]}')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(body)),
                "Content-Type": "application/json",
            },
            stream=_RawStream(body),
        )

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    # Would raise httpx.DecodingError before the fix.
    assert resp.status_code == 200
    assert resp.json() == payload
    # The stale wire-framing header is gone; the body is already decoded.
    assert "content-encoding" not in resp.headers
    assert resp.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_body_buffering_does_not_swallow_error_statuses():
    """A 404 body is still buffered and returned, not converted or retried."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/calls/bogus")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_send_message_uses_generous_timeout():
    """Regression guard for the ClickUp reminder job failing with ReadTimeout:
    ``send_message`` must go through the shared OpenPhone client (long timeout
    + throttle), not a bare ``httpx.AsyncClient`` with httpx's 5s default."""
    from api.src.open_phone import service

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(202, json={"id": "AC123"})

    real_builder = service._openphone_client

    def _client_with_mock_transport() -> httpx.AsyncClient:
        client = real_builder()
        seen["timeout"] = client.timeout
        client._transport = httpx.MockTransport(handler)
        return client

    with mock.patch.object(service, "_openphone_client", _client_with_mock_transport):
        resp = await service.send_message(
            message="hello",
            to_phone_number="+14125550123",
            from_phone_number="+14129101500",
        )

    assert resp.status_code == 202
    assert seen["url"] == "https://api.openphone.com/v1/messages"
    assert seen["method"] == "POST"
    # httpx's default read timeout is 5s — the job needs more headroom.
    assert seen["timeout"].read is not None and seen["timeout"].read > 5


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


# ---- maxResults clamp (denoise OpenPhone 400s) ----


@pytest.mark.asyncio
async def test_fetch_one_to_one_thread_clamps_max_results():
    """A caller-chosen max_results above OpenPhone's per-page cap must be
    clamped to 100 before the request. OpenPhone rejects maxResults > 100 with
    a 400, which is logged error-level and trips the alert; the agent picks
    max_results freely, so the clamp keeps every request valid.
    """
    from api.src.sernia_ai.tools.quo_tools import (
        OPENPHONE_MAX_RESULTS_PER_PAGE,
        _fetch_one_to_one_thread,
    )

    seen_max_results: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_max_results.append(request.url.params.get("maxResults"))
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openphone.com",
    ) as client:
        # 200 exceeds the cap — both /v1/messages and /v1/calls must clamp.
        await _fetch_one_to_one_thread(client, "+14129101500", max_results=200)

    assert seen_max_results, "expected requests to /v1/messages and /v1/calls"
    assert all(v == str(OPENPHONE_MAX_RESULTS_PER_PAGE) for v in seen_max_results), (
        f"maxResults not clamped to {OPENPHONE_MAX_RESULTS_PER_PAGE}: {seen_max_results}"
    )


@pytest.mark.asyncio
async def test_fetch_one_to_one_thread_preserves_small_max_results():
    """A value at or below the cap passes through unchanged."""
    from api.src.sernia_ai.tools.quo_tools import _fetch_one_to_one_thread

    seen_max_results: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_max_results.append(request.url.params.get("maxResults"))
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openphone.com",
    ) as client:
        await _fetch_one_to_one_thread(client, "+14129101500", max_results=20)

    assert seen_max_results
    assert all(v == "20" for v in seen_max_results), seen_max_results
