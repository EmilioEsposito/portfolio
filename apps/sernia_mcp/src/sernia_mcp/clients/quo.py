"""OpenPhone (Quo) HTTP client + minimal contact helpers.

Vendored from api/src/open_phone/service.py and api/src/tool_core/quo/_client.py.
Drops the DB-backed sync helpers and the upsert path the monorepo uses — we
only need read + send for MCP tools.
"""
from __future__ import annotations

import os
import time

import httpx
import logfire

_CONTACT_CACHE_TTL = 300  # 5 minutes
_contact_cache: list[dict] = []
_cache_ts: float = 0.0


def build_quo_client() -> httpx.AsyncClient:
    """Return a fresh AsyncClient for the OpenPhone API.

    Use inside ``async with`` so connections close cleanly.
    """
    api_key = os.environ.get("QUO_API_KEY", "")
    if not api_key:
        logfire.warn("QUO_API_KEY not set — Quo tool calls will fail")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": api_key},
        timeout=30,
    )


async def get_all_contacts(client: httpx.AsyncClient | None = None) -> list[dict]:
    """Return all OpenPhone contacts, cached for ``_CONTACT_CACHE_TTL`` seconds."""
    global _contact_cache, _cache_ts

    if _contact_cache and (time.monotonic() - _cache_ts) < _CONTACT_CACHE_TTL:
        return _contact_cache

    async def _fetch(c: httpx.AsyncClient) -> list[dict]:
        contacts: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            resp = await c.get("/v1/contacts", params=params)
            resp.raise_for_status()
            data = resp.json()
            contacts.extend(data.get("data", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return contacts

    if client is not None:
        all_contacts = await _fetch(client)
    else:
        async with build_quo_client() as c:
            all_contacts = await _fetch(c)

    _contact_cache = all_contacts
    _cache_ts = time.monotonic()
    logfire.info("openphone contact cache refreshed", count=len(all_contacts))
    return all_contacts


def invalidate_contact_cache() -> None:
    """Force a refresh on next access. Call after any contact mutation."""
    global _cache_ts
    _cache_ts = 0


async def find_contacts_by_phone(
    phone: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Return all OpenPhone contacts whose phoneNumbers contain ``phone``."""
    contacts = await get_all_contacts(client)
    matches: list[dict] = []
    for contact in contacts:
        for pn in contact.get("defaultFields", {}).get("phoneNumbers", []) or []:
            val = pn.get("value") if isinstance(pn, dict) else pn
            if val == phone:
                matches.append(contact)
                break
    return matches


async def find_contact_by_phone(
    phone: str,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """First contact matching ``phone``, or None."""
    matches = await find_contacts_by_phone(phone, client)
    return matches[0] if matches else None
