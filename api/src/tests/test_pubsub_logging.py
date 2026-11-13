"""Pub/Sub credentials must never enter application telemetry, even on rejection."""

import base64
import json
from unittest.mock import AsyncMock, Mock

import logfire
import pytest
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from starlette.requests import Request

from api.src.google.pubsub import routes, service


@pytest.fixture
def telemetry(monkeypatch):
    exporter = TestExporter()
    # Disable scrubbing so these tests prove that secrets never reach the logger.
    logger = logfire.configure(
        local=True,
        send_to_logfire=False,
        console=False,
        scrubbing=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
    )
    monkeypatch.setattr(routes, "logfire", logger)
    monkeypatch.setattr(service, "logfire", logger)
    return exporter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "processing_failure",
        "missing_header",
        "malformed_header",
        "malformed_jwt",
        "decoder_error",
        "nested_decoder_error",
        "invalid_audience",
        "invalid_issuer",
        "invalid_email",
    ],
)
async def test_webhook_does_not_log_credentials(monkeypatch, telemetry, outcome):
    token = "synthetic-bearer-value-that-must-never-be-logged"
    cookie = "synthetic-cookie-value-that-must-never-be-logged"
    custom_header = "synthetic-custom-header-value"
    audience = "https://example.test/api/google/pubsub/gmail/notifications"
    monkeypatch.setenv("PUBSUB_AUDIENCE_URL", audience)
    authorization = ("Basic " if outcome == "malformed_header" else "Bearer ") + token
    if outcome == "missing_header":
        authorization = ""
    monkeypatch.setenv("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", "push@example.iam.gserviceaccount.com")
    claims = {
        "aud": audience,
        "iss": "https://accounts.google.com",
        "email": "push@example.iam.gserviceaccount.com",
        "extra": token,
        "email_verified": True,
    }
    claim_key = {"invalid_audience": "aud", "invalid_issuer": "iss", "invalid_email": "email"}
    if outcome in claim_key:
        claims[claim_key[outcome]] = token
    monkeypatch.setattr(service, "get_google_public_keys", Mock(return_value={}))
    if outcome != "malformed_jwt":
        # malformed_jwt exercises Google's real parser, which echoes malformed tokens.
        decoder = Mock(return_value=claims)
        if outcome == "decoder_error":
            decoder.side_effect = RuntimeError(token)
        if outcome == "nested_decoder_error":

            def fail_with_context(*args, **kwargs):
                try:
                    raise RuntimeError(cookie)
                except RuntimeError as error:
                    raise ValueError(token) from error

            decoder.side_effect = fail_with_context
        monkeypatch.setattr(service.jwt, "decode", decoder)
    processor = AsyncMock(return_value={"status": "success", "messages": []})
    if outcome == "processing_failure":
        processor.side_effect = RuntimeError("synthetic processing failure")
    monkeypatch.setattr(routes, "process_gmail_notification", processor)
    notification = {"emailAddress": "mailbox@example.test", "historyId": "123"}
    body = json.dumps(
        {"message": {"data": base64.b64encode(json.dumps(notification).encode()).decode()}}
    ).encode()
    receive = AsyncMock(return_value={"type": "http.request", "body": body})
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pubsub/gmail/notifications",
            "headers": [
                (b"authorization", authorization.encode()),
                (b"cookie", cookie.encode()),
                (b"x-api-key", custom_header.encode()),
            ],
        },
        receive=receive,
    )

    response = await routes.handle_gmail_notifications(request)

    assert response.status_code == (204 if outcome == "success" else 500)
    if outcome in {"success", "processing_failure"}:
        processor.assert_awaited_once_with(notification)
    else:
        processor.assert_not_awaited()
        receive.assert_not_awaited()
    # Inspect full events, including exception stacktraces (snapshot helpers trim them).
    records = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [
                    {"name": event.name, "attributes": dict(event.attributes or {})}
                    for event in span.events
                ],
            }
            for span in telemetry.exported_spans
        ]
    )
    assert telemetry.exported_spans
    for secret in (token, token[:20], cookie, custom_header):
        assert secret not in records
        assert secret.encode() not in response.body
    assert "Gmail Notification" in records
