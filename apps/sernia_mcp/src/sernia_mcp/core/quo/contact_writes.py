"""Quo (OpenPhone) contact write tools.

Lifted from ``api/src/sernia_ai/tools/quo_tools.py``. Only ``create_contact``
is here for now — sernia_ai's ``update_contact`` is HITL-gated and will land
with the approval-flow batch (see ``apps/sernia_mcp/TODOS.md``).
"""
from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from sernia_mcp.clients.quo import build_quo_client, invalidate_contact_cache
from sernia_mcp.core.errors import ExternalServiceError

# UUID of Quo's "Tags" multi-select custom field. Hardcoded so we can map the
# friendly ``tags`` arg without a round-trip lookup. Same value sernia_ai uses;
# update both if Quo ever rotates it (unlikely — these UUIDs are stable).
_CF_KEY_TAGS = "6827a195fe60ba0130f30b92"


class PhoneNumber(BaseModel):
    """A phone number entry on a Quo contact."""

    name: str = Field(
        default="Phone Number",
        description='Label, e.g. "Phone Number", "Work", "mobile".',
    )
    value: str = Field(description="Phone number in E.164 format, e.g. +14125551234.")


class Email(BaseModel):
    """An email entry on a Quo contact."""

    name: str = Field(default="Email", description='Label, e.g. "Email", "Work".')
    value: str = Field(description="Email address.")


class CustomField(BaseModel):
    """A custom field entry."""

    key: str = Field(description="The 24-char hex custom field key.")
    value: str | list[str] | None = Field(
        description=(
            "Value — string for text/date fields, list of strings for "
            "multi-select (e.g. Tags)."
        )
    )


def _build_custom_fields(
    tags: list[str] | None,
    custom_fields: list[CustomField] | None,
) -> list[dict]:
    """Merge first-class ``tags`` arg with raw custom fields, dedup by key."""
    cf_map: dict[str, dict] = {}
    if custom_fields:
        for cf in custom_fields:
            d = cf.model_dump()
            cf_map[d["key"]] = d
    if tags is not None:
        cf_map[_CF_KEY_TAGS] = {"key": _CF_KEY_TAGS, "value": tags}
    return list(cf_map.values())


def _build_contact_payload(
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    role: str | None = None,
    phone_numbers: list[PhoneNumber] | None = None,
    emails: list[Email] | None = None,
    tags: list[str] | None = None,
    custom_fields: list[CustomField] | None = None,
) -> dict:
    """Build a ``defaultFields`` + ``customFields`` payload for create."""
    df: dict = {}
    for field_name, value in [
        ("firstName", first_name),
        ("lastName", last_name),
        ("company", company),
        ("role", role),
    ]:
        if value is not None:
            df[field_name] = value

    if phone_numbers is not None:
        df["phoneNumbers"] = [pn.model_dump() for pn in phone_numbers]
    if emails is not None:
        df["emails"] = [em.model_dump() for em in emails]

    return {
        "defaultFields": df,
        "customFields": _build_custom_fields(tags, custom_fields),
    }


async def create_contact_core(
    first_name: str,
    last_name: str,
    *,
    company: str | None = None,
    role: str | None = None,
    phone_numbers: list[PhoneNumber] | None = None,
    emails: list[Email] | None = None,
    tags: list[str] | None = None,
    custom_fields: list[CustomField] | None = None,
) -> str:
    """Create a new Quo contact. No HITL approval — same contract as sernia_ai."""
    payload = _build_contact_payload(
        first_name=first_name,
        last_name=last_name,
        company=company,
        role=role,
        phone_numbers=phone_numbers,
        emails=emails,
        tags=tags,
        custom_fields=custom_fields,
    )
    async with build_quo_client() as client:
        try:
            resp = await client.post("/v1/contacts", json=payload)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"Quo API error: {exc}") from exc

    if resp.status_code in (200, 201):
        invalidate_contact_cache()
        created = resp.json().get("data", {})
        return (
            f"Contact created: {first_name} {last_name} "
            f"(id: {created.get('id', '?')})"
        )

    raise ExternalServiceError(
        f"Quo API HTTP {resp.status_code}: {resp.text[:300]}"
    )
