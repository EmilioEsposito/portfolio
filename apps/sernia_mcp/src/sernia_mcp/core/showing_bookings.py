"""Showings, through the API's ``/api/showing-bookings/mcp`` endpoints.

Tour times and booked showings live in the API's booking service, which this
service does not reach (see ``AGENTS.md``). Every call here goes to the API
with the signed-in user's own Clerk token, so the API checks who it is and a
booking is recorded as taken by that person.

Nothing is worked out here. What a booking or a cancellation would do, and
who would be told, is one paragraph the API writes
(``api/src/showing_bookings/effects.py``), the same one the Sernia agent puts
on its approval card. ``preview_*`` asks for it; ``book`` and ``cancel`` send
it back as the thing the person approved. If it no longer holds, the API
refuses with the paragraph as it now reads (``EffectChangedError``) and does
nothing.
"""

from __future__ import annotations

from typing import Any

import httpx

from sernia_mcp.config import SERNIA_MCP_API_BASE_URL
from sernia_mcp.core.errors import ExternalServiceError, ValidationError

_TIMEOUT = 30
_PREFIX = "/api/showing-bookings/mcp"
# The API's code for an approved paragraph that no longer describes what would happen.
_EFFECT_CHANGED = "effect_changed"


class RefusedError(ValidationError):
    """The API refused: the reason, in words for the model and the person."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class EffectChangedError(RefusedError):
    """What the action would do changed after the person was asked. ``effect`` is it now."""

    def __init__(self, message: str, effect: str) -> None:
        self.effect = effect
        super().__init__(message, _EFFECT_CHANGED)


def _refusal(detail: Any) -> RefusedError:
    if not isinstance(detail, dict):
        return RefusedError(str(detail or "The API refused this."))
    message = str(detail.get("message") or "The API refused this.")
    if detail.get("code") == _EFFECT_CHANGED:
        return EffectChangedError(message, str(detail.get("effect", "")))
    fields = "; ".join(f"{name}: {line}" for name, line in (detail.get("fields") or {}).items())
    return RefusedError(f"{message} ({fields})" if fields else message, detail.get("code"))


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
) -> Any:
    """Call the API as the signed-in user. Raises on anything but success."""
    if not SERNIA_MCP_API_BASE_URL:
        raise ExternalServiceError("SERNIA_MCP_API_BASE_URL is not set; the showing tools are off.")
    if not token:
        raise ValidationError("Showings need a signed-in user.")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method,
                f"{SERNIA_MCP_API_BASE_URL.rstrip('/')}{_PREFIX}{path}",
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                json=json,
                params=params,
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise ExternalServiceError(f"The API could not be reached: {error}") from error
    if response.status_code in (200, 201):
        return response.json()
    detail: Any = None
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        pass
    if response.status_code in (401, 403):
        raise ExternalServiceError(
            f"The API did not accept this user for showings (HTTP {response.status_code})."
        )
    if response.status_code == 404 and detail == "Not Found":
        # FastAPI's answer for a path it does not have, not for a showing it cannot find.
        raise ExternalServiceError(
            "The API has no showings routes for this service (HTTP 404). It may be an older "
            "deployment."
        )
    if response.status_code in (404, 409, 422):
        raise _refusal(detail)
    if isinstance(detail, dict) and detail.get("message"):
        raise ExternalServiceError(str(detail["message"]))
    raise ExternalServiceError(f"The API answered HTTP {response.status_code}: {detail}")


async def read_showings(
    token: str, unit: str | None = None, zillow_thread_id: str | None = None
) -> dict[str, Any]:
    """Open times, booking links and booked showings, as the Sernia agent's read gives them."""
    params = {
        name: value.strip()
        for name, value in (("unit", unit), ("zillow_thread_id", zillow_thread_id))
        if value and value.strip()
    }
    return await _request("GET", "/showings", token, params=params or None)


async def preview_booking(token: str, booking: dict[str, Any]) -> str:
    """What booking this person would do and who would be told. Books nothing."""
    answer = await _request("POST", "/showings/bookings/preview", token, json=booking)
    return answer["effect"]


async def book(token: str, booking: dict[str, Any], effect: str) -> str:
    """Book, if it would still do exactly ``effect``. Answers with what was done."""
    answer = await _request("POST", "/showings/bookings", token, json={**booking, "effect": effect})
    return answer["result"]


async def preview_cancellation(token: str, showing_id: int, attendee_id: int | None) -> str:
    """What cancelling the showing, or removing one person, would do. Cancels nothing."""
    answer = await _request(
        "POST",
        "/showings/cancellations/preview",
        token,
        json={"showing_id": showing_id, "attendee_id": attendee_id},
    )
    return answer["effect"]


async def cancel(token: str, showing_id: int, attendee_id: int | None, effect: str) -> str:
    """Cancel, if it would still do exactly ``effect``. Answers with what was done."""
    answer = await _request(
        "POST",
        "/showings/cancellations",
        token,
        json={"showing_id": showing_id, "attendee_id": attendee_id, "effect": effect},
    )
    return answer["result"]
