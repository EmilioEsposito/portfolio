"""Rental listings, through the API's ``/api/rental-listings`` endpoints.

The listings live in the API's database, which this service does not reach
(see ``AGENTS.md``). Every call here goes to the API with the signed-in
user's own Clerk token, so the API checks who it is and records the change
under their name, the same as an edit made on the Rental Listings screen.

A listing is one unit's listing: the next lease on offer and what may be said
about the unit. It is not the current lease. The fields match
``api/src/rental_listings/schema.py``.

Changes are planned here and saved by the API, which runs every check again
(``api/src/rental_listings/checks.py``). A save sends the listing as it was
read (``based_on``); if what is on file changed in between, the API refuses
it and nothing is overwritten.

``plan_listing_change`` mirrors ``rental_listing_update`` in
``api/src/sernia_ai/tools/rental_listing_tools.py``, including which changes
need a person's approval. Keep the two in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict

from sernia_mcp.config import SERNIA_MCP_API_BASE_URL
from sernia_mcp.core.errors import ExternalServiceError, ValidationError

_TIMEOUT = 20
_PREFIX = "/api/rental-listings"

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ClearableField = Literal[
    "asking_rent",
    "next_lease_start_earliest",
    "next_lease_start_latest",
    "off_market_on",
    "preferred_showing_times",
]


class PreferredWindow(BaseModel):
    """Start times we would like a showing to land on: a nudge, never a limit."""

    model_config = ConfigDict(extra="forbid")

    days: list[Weekday]
    start: time
    end: time


class RefusedError(ValidationError):
    """The API refused the change. ``problems`` are its reasons, for the model."""

    def __init__(self, message: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{message} {'; '.join(problems)}".strip())


@dataclass(frozen=True)
class ListingPlan:
    """What a change would make of one unit's listing."""

    listing: dict[str, Any]  # the listing after the change
    based_on: dict[str, Any] | None  # as on file now; None for a new listing
    needs_person: bool
    changes: list[str]  # one line per change, for the approval card


@dataclass(frozen=True)
class RulesPlan:
    rules: dict[str, Any]
    based_on: dict[str, Any]
    needs_person: bool
    changes: list[str]


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
        raise ExternalServiceError("SERNIA_MCP_API_BASE_URL is not set; rental listings are off.")
    if not token:
        raise ValidationError("Rental listings need a signed-in user.")
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
    except ValueError:
        pass
    if response.status_code in (409, 422) and isinstance(detail, dict):
        raise RefusedError(detail.get("message", "Not saved."), list(detail.get("problems", [])))
    if response.status_code in (401, 403):
        raise ExternalServiceError(
            f"The API did not accept this user for rental listings (HTTP {response.status_code})."
        )
    if response.status_code == 404:
        raise RefusedError("Not found.", [str(detail or "No such listing.")])
    raise ExternalServiceError(f"The API answered HTTP {response.status_code}: {detail}")


async def read_rental_data(token: str) -> dict[str, Any]:
    """Every listing, and the showing rules: ``{"listings": [...], "showing_rules": {...}}``."""
    return await _request("GET", "", token)


async def read_history(token: str, subject: str | None, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if subject:
        params["subject"] = subject
    return await _request("GET", "/history", token, params=params)


async def save_listing(token: str, plan: ListingPlan, *, dry_run: bool = False) -> dict[str, Any]:
    """Save a planned listing. ``dry_run`` checks it without saving."""
    params = {"dry_run": "true"} if dry_run else None
    if plan.based_on is None:
        return await _request(
            "POST", "/listings", token, json={"listing": plan.listing}, params=params
        )
    return await _request(
        "PUT",
        f"/listings/{quote(plan.based_on['unit'], safe='')}",
        token,
        json={"listing": plan.listing, "based_on": plan.based_on},
        params=params,
    )


async def save_rules(token: str, plan: RulesPlan, *, dry_run: bool = False) -> dict[str, Any]:
    params = {"dry_run": "true"} if dry_run else None
    return await _request(
        "PUT",
        "/showing-rules",
        token,
        json={"showing_rules": plan.rules, "based_on": plan.based_on},
        params=params,
    )


def _same_line(first: str, second: str) -> bool:
    return first.strip().casefold() == second.strip().casefold()


def _changed_lines(
    kind: str, unit: str, current: list[str], add: list[str] | None, remove: list[str] | None
) -> tuple[list[str], list[str]]:
    """``current`` with lines removed and added, and a line per change."""
    lines = list(current)
    described: list[str] = []
    for line in remove or []:
        kept = [item for item in lines if not _same_line(item, line)]
        if len(kept) == len(lines):
            on_file = "; ".join(lines) or "none"
            raise RefusedError(
                "Not saved.", [f"{unit} has no {kind} {line!r}. On file: {on_file}."]
            )
        lines = kept
        described.append(f"Remove {kind}: {line.strip()}")
    for line in add or []:
        text = " ".join(line.split())  # one fact or note is one line
        if text and not any(_same_line(item, text) for item in lines):
            lines.append(text)
            described.append(f"Add {kind}: {text}")
    return lines, described


def _iso(value: date | time | None) -> str | None:
    return value.isoformat() if value is not None else None


def plan_listing_change(
    data: dict[str, Any],
    *,
    unit: str,
    create: bool = False,
    address: str | None = None,
    asking_rent: int | None = None,
    next_lease_start_earliest: date | None = None,
    next_lease_start_latest: date | None = None,
    virtual_tours_allowed: bool | None = None,
    off_market_on: date | None = None,
    showing_booking_url: str | None = None,
    add_public_facts: list[str] | None = None,
    remove_public_facts: list[str] | None = None,
    add_internal_notes: list[str] | None = None,
    remove_internal_notes: list[str] | None = None,
    preferred_showing_times: list[PreferredWindow] | None = None,
    clear: list[ClearableField] | None = None,
) -> ListingPlan:
    """Work out a change to one unit's listing, from what is on file.

    Raises ``RefusedError`` with the reason when the change cannot be made as
    asked. Whether the result passes the API's checks is the API's to say.
    """
    wanted = unit.strip().casefold()
    existing = next((item for item in data["listings"] if item["unit"].casefold() == wanted), None)
    if existing is None and not create:
        names = ", ".join(item["unit"] for item in data["listings"]) or "none"
        raise RefusedError(
            "Not saved.",
            [
                f"No listing for a unit named {unit!r} is on file (on file: {names}). "
                "Use the name on file, or pass create=True to add a listing."
            ],
        )
    if existing is not None and create:
        raise RefusedError(
            "Not saved.",
            [f"{existing['unit']} is already on file. Leave create False to change it."],
        )

    current: dict[str, Any] = existing or {
        "unit": unit.strip(),
        "address": "",
        "asking_rent": None,
        "next_lease_start_earliest": None,
        "next_lease_start_latest": None,
        "virtual_tours_allowed": False,
        "off_market_on": None,
        "showing_booking_url": "",
        "preferred_showing_times": [],
        "public_facts": [],
        "internal_notes": [],
    }
    changes: dict[str, Any] = {
        name: value
        for name, value in {
            "address": address.strip() if address is not None else None,
            "asking_rent": asking_rent,
            "next_lease_start_earliest": _iso(next_lease_start_earliest),
            "next_lease_start_latest": _iso(next_lease_start_latest),
            "virtual_tours_allowed": virtual_tours_allowed,
            "off_market_on": _iso(off_market_on),
            "showing_booking_url": (
                showing_booking_url.strip() if showing_booking_url is not None else None
            ),
            "preferred_showing_times": (
                [window.model_dump(mode="json") for window in preferred_showing_times]
                if preferred_showing_times is not None
                else None
            ),
        }.items()
        if value is not None
    }
    for name in clear or []:
        if name in changes:
            raise RefusedError("Not saved.", [f"{name} is both set and cleared; do one."])
        changes[name] = [] if name == "preferred_showing_times" else None

    facts, fact_lines = _changed_lines(
        "public fact",
        current["unit"],
        current["public_facts"],
        add_public_facts,
        remove_public_facts,
    )
    notes, note_lines = _changed_lines(
        "internal note",
        current["unit"],
        current["internal_notes"],
        add_internal_notes,
        remove_internal_notes,
    )
    updated = {**current, **changes, "public_facts": facts, "internal_notes": notes}
    described = [
        f"{name}: {current.get(name)!r} -> {value!r}"
        for name, value in changes.items()
        if current.get(name) != value
    ]
    described += fact_lines + note_lines
    if existing is None:
        described.insert(0, f"New listing for unit {updated['unit']}")
    if existing is not None and updated == existing:
        raise RefusedError("Nothing to change:", [f"{existing['unit']} already reads that way."])

    needs_person = existing is None or any(
        value is not None and value != []
        for value in (
            address,
            showing_booking_url,
            add_public_facts,
            remove_public_facts,
            add_internal_notes,
            remove_internal_notes,
        )
    )
    return ListingPlan(
        listing=updated, based_on=existing, needs_person=needs_person, changes=described
    )


def plan_rules_change(
    data: dict[str, Any],
    *,
    earliest_start: time | None = None,
    latest_start: time | None = None,
    add_blackout_dates: list[date] | None = None,
    remove_blackout_dates: list[date] | None = None,
    showing_booking_url: str | None = None,
) -> RulesPlan:
    """Work out a change to the showing rules, from what is on file."""
    current = data["showing_rules"]
    on_file = {date.fromisoformat(day) for day in current["blackout_dates"]}
    blackout = (on_file | set(add_blackout_dates or [])) - set(remove_blackout_dates or [])
    updated = {**current, "blackout_dates": [day.isoformat() for day in sorted(blackout)]}
    if earliest_start is not None:
        updated["earliest_start"] = _iso(earliest_start)
    if latest_start is not None:
        updated["latest_start"] = _iso(latest_start)
    if showing_booking_url is not None:
        updated["showing_booking_url"] = showing_booking_url.strip()
    described = [
        f"{name}: {current.get(name)!r} -> {value!r}"
        for name, value in updated.items()
        if current.get(name) != value and name != "blackout_dates"
    ]
    described += [f"Add blackout date: {day}" for day in sorted(blackout - on_file)]
    described += [f"Remove blackout date: {day}" for day in sorted(on_file - blackout)]
    if not described:
        raise RefusedError("Nothing to change:", ["the showing rules already read that way."])
    return RulesPlan(
        rules=updated,
        based_on=current,
        needs_person=showing_booking_url is not None,
        changes=described,
    )
