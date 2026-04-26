"""Google service-account auth with domain-wide delegation.

Vendored from api/src/google/common/service_account_auth.py. Trimmed to drop
the FastAPI HTTPException baggage — we raise plain ValueError / RuntimeError
and let the calling layer translate to MCP ToolError.

Required env var: GOOGLE_SERVICE_ACCOUNT_CREDENTIALS — base64-encoded JSON of
a service account that has domain-wide delegation enabled in Google Workspace.
"""
from __future__ import annotations

import base64
import binascii
import json
import os

from google.oauth2 import service_account


def _load_service_account_dict() -> dict:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS")
    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS env var is not set. "
            "Provide the base64-encoded service-account JSON."
        )
    cleaned = raw.strip().strip('"').strip("'")
    padding = 4 - (len(cleaned) % 4)
    if padding != 4:
        cleaned += "=" * padding
    try:
        json_str = base64.b64decode(cleaned).decode("utf-8")
        creds = json.loads(json_str)
    except (json.JSONDecodeError, binascii.Error) as exc:
        raise RuntimeError(f"Invalid GOOGLE_SERVICE_ACCOUNT_CREDENTIALS: {exc}") from exc

    required = ("type", "project_id", "private_key", "client_email")
    missing = [k for k in required if k not in creds]
    if missing:
        raise RuntimeError(f"Service account JSON missing fields: {missing}")
    if creds["type"] != "service_account":
        raise RuntimeError(f"Expected service_account credentials, got {creds['type']!r}")
    return creds


def get_delegated_credentials(
    user_email: str, scopes: list[str]
) -> service_account.Credentials:
    """Return service-account credentials delegated to act as ``user_email``.

    Requires domain-wide delegation to be set up in Google Workspace admin for
    the configured service account, with the requested scopes whitelisted.
    """
    info = _load_service_account_dict()
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=scopes
    )
    return credentials.with_subject(user_email)
