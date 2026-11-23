"""Signature verification rejects unsigned/malformed OpenPhone webhooks with 403."""

import base64
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.src.open_phone.routes import verify_open_phone_signature

SECRET = base64.b64encode(b"test-signing-key").decode()


def _request(headers: dict[str, str], body: bytes = b"{}") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/open_phone/webhook",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("QUO_WEBHOOK_SECRET", SECRET)


@pytest.mark.asyncio
async def test_missing_signature_header_is_403():
    with pytest.raises(HTTPException) as exc:
        await verify_open_phone_signature(_request({}))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_malformed_signature_header_is_403():
    with pytest.raises(HTTPException) as exc:
        await verify_open_phone_signature(_request({"openphone-signature": "garbage"}))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_valid_signature_passes():
    body = b'{"id": "evt"}'
    ts = "1700000000000"
    digest = base64.b64encode(
        hmac.new(base64.b64decode(SECRET), ts.encode() + b"." + body, "sha256").digest()
    ).decode()
    header = f"hmac;1;{ts};{digest}"
    assert (
        await verify_open_phone_signature(_request({"openphone-signature": header}, body)) is True
    )


@pytest.mark.asyncio
async def test_bad_digest_is_403():
    header = "hmac;1;1700000000000;" + base64.b64encode(b"nope").decode()
    with pytest.raises(HTTPException) as exc:
        await verify_open_phone_signature(_request({"openphone-signature": header}))
    assert exc.value.status_code == 403
