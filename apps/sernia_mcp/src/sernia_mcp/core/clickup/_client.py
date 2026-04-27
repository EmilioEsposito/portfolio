"""Shared ClickUp HTTP helper used by all ``core.clickup.*`` modules.

Centralizes the auth header, timeout, and error wrapping so each tool's
core function focuses on building the request body / parsing the response.
"""
from __future__ import annotations

import os

import httpx

from sernia_mcp.core.errors import ExternalServiceError

_BASE = "https://api.clickup.com/api/v2"
_TIMEOUT = 20


async def clickup_request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
) -> httpx.Response:
    """Send an authenticated request to the ClickUp v2 API.

    Wraps transport errors in ``ExternalServiceError`` so callers can
    translate to a ``ToolError`` consistently. Non-success HTTP status codes
    are NOT raised here — callers inspect ``resp.status_code`` and decide
    how to format the error string for the model.
    """
    headers: dict[str, str] = {
        "accept": "application/json",
        "Authorization": os.environ.get("CLICKUP_API_KEY", ""),
    }
    if json is not None:
        headers["content-type"] = "application/json"

    async with httpx.AsyncClient() as client:
        try:
            return await client.request(
                method,
                f"{_BASE}{path}",
                headers=headers,
                json=json,
                params=params,
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"ClickUp API error: {exc}") from exc
