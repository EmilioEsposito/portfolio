"""Unit tests for the vendored OpenPhone rate-limiting + 429-retry transport.

Mirrors ``api/src/tests/test_openphone_rate_limit.py`` for the self-contained
MCP service. Guards that bursts stay under the per-key rate limit and that the
rare residual 429 is retried instead of bubbling up as error-level noise.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import logfire
import pytest
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from sernia_mcp.clients.rate_limit import (
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
        (429, "error"),
        (500, "error"),
        (503, "error"),
    ],
)
def test_level_for_status(status, expected):
    assert _level_for_status(status) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0", 0.0),
        ("2", 2.0),
        ("  3.5 ", 3.5),
        ("-1", 0.0),
        (None, None),
        ("", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", None),
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


async def test_transport_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_transport_gives_up_after_max_retries():
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
    assert calls["n"] == MAX_RETRIES + 1


async def test_transport_passes_through_non_429():
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


async def test_token_bucket_throttles_burst():
    bucket = _TokenBucket(rate=20.0, capacity=2.0)
    await bucket.acquire()
    await bucket.acquire()

    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.03


async def test_token_bucket_allows_initial_burst():
    bucket = _TokenBucket(rate=1.0, capacity=5.0)
    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(5)))
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


@pytest.fixture
def logfire_exporter():
    """Capture emitted spans via an isolated in-memory exporter."""
    exporter = TestExporter()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
    )
    yield exporter
    exporter.clear()


def _request_spans(exporter: TestExporter) -> list[dict]:
    """The transport's per-request spans (those carrying http.status_code)."""
    return [
        s
        for s in exporter.exported_spans_as_dict()
        if s["attributes"].get("http.status_code") is not None
    ]


async def test_recovered_429_is_not_error_level(logfire_exporter):
    """A 429 retried into a 200 must emit exactly one span, NOT at error level.

    This is the denoising guarantee: a self-healing rate-limit hit no longer
    trips the error-level alert.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 200
    logfire.force_flush()

    spans = _request_spans(logfire_exporter)
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert attrs["http.status_code"] == 200
    assert attrs.get("openphone.attempts") == 2  # one 429 + one success
    # Default (info=9) when set_level was not called for an error status.
    assert attrs.get("logfire.level_num", 9) < 17


async def test_final_error_is_error_level(logfire_exporter):
    """A genuine error (final 5xx) still surfaces at error level → still pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = RateLimitedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.openphone.com"
    ) as client:
        resp = await client.get("/v1/contacts")

    assert resp.status_code == 500
    logfire.force_flush()

    spans = _request_spans(logfire_exporter)
    assert len(spans) == 1
    assert spans[0]["attributes"]["http.status_code"] == 500
    assert spans[0]["attributes"].get("logfire.level_num") == 17
