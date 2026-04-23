"""Shared httpx client factory for Quo (OpenPhone) API calls."""
import os

import httpx
import logfire


def build_quo_client() -> httpx.AsyncClient:
    """Return a fresh AsyncClient pointed at the OpenPhone API.

    Callers should use this inside ``async with`` so connections are cleaned up.
    """
    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        logfire.warn("OPEN_PHONE_API_KEY not set — Quo tool calls will fail")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": api_key},
        timeout=30,
    )
