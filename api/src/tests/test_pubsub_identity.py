"""Signed synthetic notifications cannot choose a principal or delegated mailbox."""

import base64
import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth import crypt, jwt

from api.src.google.gmail import service as gmail
from api.src.google.gmail.config import GMAIL_WATCH_MAILBOX
from api.src.google.pubsub import routes, service

AUDIENCE = "https://example.test/api/google/pubsub/gmail/notifications"
IDENTITY = "push@example-project.iam.gserviceaccount.com"
PATH = "/pubsub/gmail/notifications"


@pytest.fixture
def signed_notification(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signer = crypt.RSASigner.from_string(private, key_id="test-key")
    monkeypatch.setattr(service, "get_google_public_keys", lambda: {"test-key": public})
    monkeypatch.setenv("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", IDENTITY)
    monkeypatch.setenv("PUBSUB_AUDIENCE_URL", AUDIENCE)
    delegate = Mock()
    history = AsyncMock(return_value={"status": "no_messages", "reason": "No changes"})
    monkeypatch.setattr(routes, "get_delegated_credentials", delegate)
    monkeypatch.setattr(routes, "get_gmail_service", Mock())
    monkeypatch.setattr(routes, "get_email_changes", history)
    app = FastAPI()
    app.include_router(routes.router)

    def send(overrides=None, missing=(), mailbox=GMAIL_WATCH_MAILBOX, tamper=False):
        claims = {
            "aud": AUDIENCE,
            "iss": "https://accounts.google.com",
            "email": IDENTITY,
            "email_verified": True,
            "iat": int(time.time()) - 10,
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides or {})
        for field in missing:
            claims.pop(field)
        token = jwt.encode(signer, claims).decode()
        if tamper:
            parts = token.split(".")
            parts[1] = base64.urlsafe_b64encode(b'{"email":"forged"}').decode().rstrip("=")
            token = ".".join(parts)
        data = {"emailAddress": mailbox, "historyId": "123"}
        with TestClient(app) as client:
            return client.post(
                PATH,
                headers={"Authorization": f"Bearer {token}"},
                json={"message": {"data": base64.b64encode(json.dumps(data).encode()).decode()}},
            )

    return send, delegate, history


def test_exact_identity_and_mailbox_are_accepted(signed_notification):
    send, delegate, history = signed_notification
    assert send().status_code == 204
    delegate.assert_called_once_with(
        user_email=GMAIL_WATCH_MAILBOX, scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    history.assert_awaited_once()


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "other@example-project.iam.gserviceaccount.com"},
        {"email": "push@unrelated.iam.gserviceaccount.com"},
        {"email": None},
        {"email_verified": False},
        {"email_verified": "true"},
        {"email_verified": 1},
        {"aud": AUDIENCE + "?bypass=true"},
        {"aud": AUDIENCE + "/"},
        {"aud": [AUDIENCE]},
        {"aud": None},
        {"iss": "https://unrelated.test"},
        {"exp": 1},
        {"iat": 9999999999},
    ],
)
def test_wrong_signed_claims_cannot_delegate(signed_notification, overrides):
    send, delegate, history = signed_notification
    assert send(overrides).status_code == 401
    delegate.assert_not_called()
    history.assert_not_awaited()


@pytest.mark.parametrize("field", ["email", "email_verified", "aud", "iss", "iat", "exp"])
def test_missing_claim_cannot_delegate(signed_notification, field):
    send, delegate, history = signed_notification
    assert send(missing=(field,)).status_code == 401
    delegate.assert_not_called()
    history.assert_not_awaited()


def test_signature_is_still_required(signed_notification):
    send, delegate, _ = signed_notification
    assert send(tamper=True).status_code == 401
    delegate.assert_not_called()


@pytest.mark.parametrize("setting", ["PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", "PUBSUB_AUDIENCE_URL"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_configuration_fails_closed(signed_notification, monkeypatch, setting, value):
    send, delegate, _ = signed_notification
    if value is None:
        monkeypatch.delenv(setting)
    else:
        monkeypatch.setenv(setting, value)
    assert send().status_code == 503
    delegate.assert_not_called()


@pytest.mark.parametrize("mailbox", ["other@serniacapital.com", "", None, [GMAIL_WATCH_MAILBOX]])
def test_valid_push_identity_cannot_choose_mailbox(signed_notification, mailbox):
    send, delegate, history = signed_notification
    assert send(mailbox=mailbox).status_code == 403
    delegate.assert_not_called()
    history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], {"emailAddress": "other@serniacapital.com"}])
async def test_direct_processor_call_cannot_bypass_mailbox_guard(monkeypatch, payload):
    delegate = Mock()
    monkeypatch.setattr(routes, "get_delegated_credentials", delegate)
    with pytest.raises(routes.HTTPException) as caught:
        await routes.process_gmail_notification(payload)
    assert caught.value.status_code == 403
    delegate.assert_not_called()


def test_configured_audience_query_is_compared_exactly(signed_notification, monkeypatch):
    send, _, _ = signed_notification
    exact = AUDIENCE + "?configured=true"
    monkeypatch.setenv("PUBSUB_AUDIENCE_URL", exact)
    assert send({"aud": exact}).status_code == 204


def test_watch_uses_the_same_mailbox(monkeypatch):
    delegate = Mock()
    monkeypatch.setattr(gmail, "get_delegated_credentials", delegate)
    monkeypatch.setattr(gmail, "get_gmail_service", Mock())
    gmail.setup_gmail_watch()
    assert delegate.call_count == 2  # stop existing watch, then start it
    assert all(c.kwargs["user_email"] == GMAIL_WATCH_MAILBOX for c in delegate.call_args_list)
