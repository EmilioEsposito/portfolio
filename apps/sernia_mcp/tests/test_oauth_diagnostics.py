"""Safe OAuth diagnostics preserve traffic and exclude all credential material."""

import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from sernia_mcp import oauth_diagnostics as diagnostics

SECRET = "synthetic-private-credential-do-not-log"


async def invoke(status, chunks, *, method="POST", path="/token", receive=None):
    messages = [
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"x-test", SECRET.encode())],
        },
        *[
            {"type": "http.response.body", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)
        ],
    ]
    received = []

    async def app(scope, inner_receive, send):
        assert inner_receive is receive
        for message in messages:
            await send(message)

    async def send(message):
        received.append(message)

    await diagnostics.OAuthDiagnosticsMiddleware(app)(
        {"type": "http", "method": method, "path": path, "query_string": SECRET.encode()},
        receive,
        send,
    )
    assert received == messages
    assert all(left is right for left, right in zip(received, messages, strict=True))


@pytest.mark.parametrize(
    ("error", "description", "reason"),
    [
        ("invalid_client", "Missing client_assertion", "missing_assertion"),
        ("invalid_client", "Invalid client_assertion_type", "assertion_type"),
        ("invalid_client", "Invalid client assertion: Assertion replay detected", "replay"),
        (
            "invalid_client",
            "Invalid client assertion: Assertion lifetime too long",
            "assertion_lifetime",
        ),
        ("invalid_client", "Invalid client assertion: Invalid JWT assertion", "invalid_assertion"),
        ("invalid_client", "Invalid client_id", "client_identity"),
        ("invalid_grant", "PKCE verification failed", "pkce"),
        ("invalid_grant", "Authorization code expired", "grant"),
        ("invalid_grant", "redirect_uri mismatch", "redirect_uri"),
    ],
)
@pytest.mark.parametrize("status", [400, 401])
async def test_categorical_failure_preserves_chunks_and_drops_secrets(
    caplog, error, description, reason, status
):
    body = json.dumps(
        {"error": error, "error_description": f"{description}: {SECRET}", "access_token": SECRET}
    ).encode()
    receive = AsyncMock(side_effect=AssertionError("Request body must not be inspected"))
    await invoke(status, [body[:11], body[11:]], receive=receive)
    receive.assert_not_awaited()
    assert len(caplog.records) == 1
    assert caplog.records[0].args == {"status": status, "error": error, "reason": reason}
    assert SECRET not in caplog.text
    assert "error_description" not in caplog.text


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not-json " + SECRET.encode(), "malformed_response"),
        (b"\xff" + SECRET.encode(), "malformed_response"),
        (b"[" * 1500 + b"]" * 1500, "malformed_response"),
        (json.dumps([SECRET]).encode(), "malformed_response"),
        (
            json.dumps({"error": [SECRET], "error_description": {"secret": SECRET}}).encode(),
            "unspecified",
        ),
        (json.dumps({"error": SECRET, "error_description": SECRET}).encode(), "unspecified"),
    ],
)
async def test_malformed_or_attacker_controlled_fields_never_escape(caplog, body, reason):
    await invoke(401, [body])
    assert caplog.records[0].args == {"status": 401, "error": "unknown", "reason": reason}
    assert SECRET not in caplog.text


async def test_oversized_error_is_not_parsed_and_is_preserved(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics.json, "loads", lambda _: pytest.fail("Oversized body parsed"))
    await invoke(400, [b"a" * diagnostics.MAX_ERROR_BYTES, SECRET.encode(), b"more"])
    assert caplog.records[0].args == {
        "status": 400,
        "error": "unknown",
        "reason": "oversized_response",
    }
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    ("status", "method", "path"),
    [
        (200, "POST", "/token"),
        (500, "POST", "/token"),
        (400, "GET", "/token"),
        (401, "POST", "/other"),
    ],
)
async def test_other_responses_are_not_parsed_or_logged(monkeypatch, caplog, status, method, path):
    monkeypatch.setattr(diagnostics.json, "loads", lambda _: pytest.fail("Unrelated body parsed"))
    await invoke(status, [SECRET.encode()], method=method, path=path)
    assert not caplog.records


async def test_logging_failure_does_not_change_oauth_response(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic log sink failure")

    monkeypatch.setattr(diagnostics.logger, "warning", fail)
    await invoke(400, [b'{"error":"invalid_grant"}'])


@pytest.fixture
def jwt_logging():
    target = logging.getLogger("fastmcp.server.auth.providers.jwt")
    old_level, old_filters = target.level, target.filters[:]
    diagnostics.install_jwt_diagnostics()
    try:
        yield target
    finally:
        target.setLevel(old_level)
        target.filters[:] = old_filters


@pytest.mark.parametrize(
    ("template", "args", "reason"),
    [
        ("Token validation failed: JWT signature/format invalid", (), "signature_or_format"),
        ("Token validation failed: %s", (SECRET,), "validation_error"),
        ("JWKS fetch blocked by SSRF protection: %s", (SECRET,), "jwks_fetch"),
        (
            "Bearer token rejected for client %s: audience mismatch (got %r, expected %r)",
            (SECRET, SECRET, SECRET),
            "audience",
        ),
    ],
)
def test_jwt_failure_records_are_sanitized_before_handlers(
    jwt_logging, caplog, template, args, reason
):
    try:
        raise ValueError(SECRET)
    except ValueError:
        jwt_logging.debug(template, *args, exc_info=True, stack_info=True, extra={"token": SECRET})
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.args == {"reason": reason}
    assert record.levelno == logging.WARNING
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None
    assert "token" not in record.__dict__
    assert SECRET not in repr(record.__dict__)
    assert SECRET not in caplog.text


def test_jwt_unknown_debug_is_dropped_and_unknown_warning_is_sanitized(jwt_logging, caplog):
    jwt_logging.debug("Future message: %s", SECRET)
    assert not caplog.records
    jwt_logging.warning("Future message: %s", SECRET, extra={"claims": SECRET})
    assert len(caplog.records) == 1
    assert caplog.records[0].args == {"reason": "unknown_event"}
    assert SECRET not in repr(caplog.records[0].__dict__)


def test_jwt_filter_never_formats_sensitive_arguments(jwt_logging, caplog):
    class NeverFormat:
        def __str__(self):
            pytest.fail("Sensitive argument was formatted")

    jwt_logging.debug("Token validation failed: %s", NeverFormat())
    assert caplog.records[0].args == {"reason": "validation_error"}


def test_jwt_installation_is_idempotent_and_does_not_enable_other_loggers(jwt_logging):
    unrelated = logging.getLogger("fastmcp.server.auth.cimd")
    before = (unrelated.level, unrelated.filters[:])
    count = len(jwt_logging.filters)
    diagnostics.install_jwt_diagnostics()
    assert len(jwt_logging.filters) == count
    assert (unrelated.level, unrelated.filters) == before


@pytest.mark.parametrize("failure", ["audience", "issuer", "expired", "signature_or_format"])
async def test_real_jwt_verifier_rejection_is_categorical(jwt_logging, caplog, failure):
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    verifier = JWTVerifier(
        public_key=public_key, issuer="expected-issuer", audience="expected-audience"
    )
    claims = {
        "sub": SECRET,
        "iss": "expected-issuer",
        "aud": "expected-audience",
        "exp": int(time.time()) + 60,
    }
    if failure == "audience":
        claims["aud"] = SECRET
    elif failure == "issuer":
        claims["iss"] = SECRET
    elif failure == "expired":
        claims["exp"] = int(time.time()) - 60
    signing_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if failure == "signature_or_format"
        else key
    )
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    assert await verifier.load_access_token(token) is None
    records = [record for record in caplog.records if record.name == diagnostics.logger.name]
    assert len(records) == 1
    assert records[0].args == {"reason": failure}
    assert SECRET not in caplog.text
    assert token not in caplog.text


def test_jwt_logging_sink_failure_cannot_change_verification_flow(jwt_logging, monkeypatch):
    def fail(record):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(diagnostics.logger, "handle", fail)
    jwt_logging.debug("Token validation failed: %s", SECRET)
