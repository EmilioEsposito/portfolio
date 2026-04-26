"""Minimal Gmail send/search/read primitives over the v1 API.

Vendored from api/src/google/gmail/service.py. Trimmed to send + get + body
extraction — drops the watch-/history-driven webhook plumbing the FastAPI
monorepo uses for inbound triggers.
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

import googleapiclient.errors
from google.oauth2 import service_account
from googleapiclient.discovery import build

GMAIL_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_gmail_service(credentials: service_account.Credentials):
    """Build a Gmail API client from delegated credentials."""
    return build("gmail", "v1", credentials=credentials)


def _create_message(sender: str, to: str, subject: str, message_text: str) -> dict:
    msg = MIMEText(message_text)
    msg["to"] = to
    msg["from"] = sender
    msg["subject"] = subject
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


async def send_email_via_service(
    service,
    *,
    to: str,
    subject: str,
    message_text: str,
    sender: str,
) -> dict:
    """Send an email through an already-built Gmail service."""
    body = _create_message(sender, to, subject, message_text)
    return service.users().messages().send(userId="me", body=body).execute()


def get_message(service, message_id: str) -> dict | None:
    """Fetch a Gmail message by ID. Returns None for 404s (deleted)."""
    try:
        return (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except googleapiclient.errors.HttpError as error:
        if error.resp.status == 404:
            return None
        raise


def extract_body(message: dict[str, Any]) -> dict[str, str]:
    """Recursively extract text/html bodies from a Gmail message payload."""

    def decode(data: str) -> str:
        padded = data + "=" * (4 - len(data) % 4)
        try:
            return base64.urlsafe_b64decode(padded).decode("utf-8")
        except Exception:
            return ""

    def walk(payload: dict[str, Any]) -> dict[str, str]:
        out = {"text": "", "html": ""}
        body = payload.get("body", {})
        if "data" in body:
            mime = payload.get("mimeType", "")
            if mime == "text/plain":
                out["text"] = decode(body["data"])
            elif mime == "text/html":
                out["html"] = decode(body["data"])
        for part in payload.get("parts", []) or []:
            sub = walk(part)
            if sub["text"]:
                out["text"] += sub["text"]
            if sub["html"]:
                out["html"] += sub["html"]
        return out

    return walk(message.get("payload", {}))
