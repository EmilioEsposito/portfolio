"""Quo contact search + SMS thread retrieval."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from sernia_mcp.clients._fuzzy import fuzzy_filter_json
from sernia_mcp.clients.quo import build_quo_client, get_all_contacts
from sernia_mcp.config import QUO_SHARED_EXTERNAL_PHONE_ID
from sernia_mcp.core.errors import ExternalServiceError


def _build_phone_map(contacts: list[dict]) -> dict[str, str]:
    phone_map: dict[str, str] = {}
    for c in contacts:
        defaults = c.get("defaultFields", {})
        first = defaults.get("firstName") or ""
        last = defaults.get("lastName") or ""
        name = f"{first} {last}".strip() or defaults.get("company") or "Unknown"
        for pn in defaults.get("phoneNumbers", []) or []:
            val = pn.get("value") if isinstance(pn, dict) else pn
            if val:
                phone_map[val] = name
    return phone_map


def _is_done_conversation(conv: dict) -> bool:
    """Quo marks a conversation 'done' by snoozing it 100+ years out."""
    snoozed = conv.get("snoozedUntil")
    if not snoozed:
        return False
    try:
        return snoozed[:4] > "2100"
    except (TypeError, IndexError):
        return False


async def search_contacts_core(query: str) -> str:
    """Fuzzy-search Quo contacts by name, phone, or company. Returns top 5 as JSON."""
    async with build_quo_client() as client:
        contacts = await get_all_contacts(client)
    return fuzzy_filter_json(contacts, query, top_n=5)


async def list_active_threads_core(
    max_results: int = 20,
    updated_after_days: int | None = None,
) -> str:
    """List active SMS conversation threads on the shared team number.

    Mirrors the Quo active inbox: pages through ``/v1/conversations`` filtering
    out 'done' threads (snoozed 100+ years out), then sorts by most recent
    activity. Each thread is enriched with contact names and a one-line
    snippet of the last message (fetched in parallel).
    """
    active: list[dict] = []
    page_token: str | None = None
    max_pages = 5  # safety limit; ~95% of threads are 'done', so we page past them

    async with build_quo_client() as client:
        for _ in range(max_pages):
            params: list[tuple[str, str]] = [
                ("phoneNumbers[]", QUO_SHARED_EXTERNAL_PHONE_ID),
                ("maxResults", "100"),
                ("excludeInactive", "true"),
            ]
            if updated_after_days is not None:
                cutoff = (
                    datetime.now(UTC) - timedelta(days=updated_after_days)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                params.append(("updatedAfter", cutoff))
            if page_token:
                params.append(("pageToken", page_token))

            try:
                resp = await client.get("/v1/conversations", params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ExternalServiceError(f"Quo API error: {exc}") from exc

            data = resp.json()
            for conv in data.get("data", []):
                if not _is_done_conversation(conv):
                    active.append(conv)

            page_token = data.get("nextPageToken")
            if not page_token or len(active) >= max_results:
                break

        active.sort(key=lambda c: c.get("lastActivityAt", ""), reverse=True)
        conversations = active[:max_results]

        if not conversations:
            return "No active conversation threads found."

        contacts = await get_all_contacts(client)
        phone_map = _build_phone_map(contacts)

        async def _fetch_snippet(participant_phone: str) -> tuple[str, str] | None:
            try:
                resp = await client.get(
                    "/v1/messages",
                    params={
                        "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                        "participants": participant_phone,
                        "maxResults": "1",
                    },
                )
                resp.raise_for_status()
                msgs = resp.json().get("data", [])
                if msgs:
                    direction = msgs[0].get("direction", "")
                    text = msgs[0].get("text") or msgs[0].get("body") or ""
                    return (direction, text)
            except httpx.HTTPError:
                pass
            return None

        snippet_phones = [
            (conv.get("participants") or [""])[0] for conv in conversations
        ]
        snippet_results = await asyncio.gather(
            *(_fetch_snippet(p) for p in snippet_phones if p),
            return_exceptions=True,
        )
        snippet_map: dict[str, tuple[str, str]] = {}
        for phone, result in zip(snippet_phones, snippet_results, strict=False):
            if isinstance(result, tuple):
                snippet_map[phone] = result

    lines: list[str] = []
    for conv in conversations:
        participants = conv.get("participants", [])
        last_activity = conv.get("lastActivityAt", "?")
        conv_id = conv.get("id", "?")

        enriched = []
        for phone in participants:
            name = phone_map.get(phone, phone)
            enriched.append(f"{name} ({phone})" if name != phone else phone)

        snippet_line = ""
        ext_phone = participants[0] if participants else ""
        snippet = snippet_map.get(ext_phone)
        if snippet:
            direction, text = snippet
            if text:
                preview = text[:80] + "..." if len(text) > 80 else text
                if direction == "outgoing":
                    snippet_line = f"\n  Snippet: You: {preview}"
                else:
                    sender = phone_map.get(ext_phone, ext_phone).split(" (")[0]
                    snippet_line = f"\n  Snippet: {sender}: {preview}"

        lines.append(
            f"Thread: {', '.join(enriched)}{snippet_line}\n"
            f"  Last activity: {last_activity}\n"
            f"  Conversation ID: {conv_id}"
        )

    return f"Active threads ({len(conversations)}):\n\n" + "\n\n".join(lines)


async def get_thread_messages_core(phone_number: str, max_results: int = 20) -> str:
    """Get recent SMS thread messages for ``phone_number``.

    Returns chronological text (oldest → newest), enriched with contact names.
    """
    async with build_quo_client() as client:
        try:
            resp = await client.get(
                "/v1/messages",
                params={
                    "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                    "participants": phone_number,
                    "maxResults": str(max_results),
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"Quo API error: {exc}") from exc

        data = resp.json()
        messages = data.get("data", [])
        if not messages:
            return f"No messages found with {phone_number}."

        contacts = await get_all_contacts(client)

    phone_map = _build_phone_map(contacts)
    messages = list(reversed(messages))

    contact_name = phone_map.get(phone_number, phone_number)
    lines: list[str] = [
        f"SMS thread with {contact_name} ({phone_number}) — {len(messages)} messages\n"
    ]
    for msg in messages:
        created = msg.get("createdAt", "?")
        direction = msg.get("direction", "?")
        text = msg.get("text") or msg.get("body") or "(no text)"
        sender_phone = msg.get("from_") or msg.get("from", "?")

        if direction == "outgoing":
            sender_name = "Sernia Capital"
            recipient_name = contact_name
        else:
            sender_name = (
                phone_map.get(sender_phone, sender_phone)
                if isinstance(sender_phone, str)
                else "?"
            )
            recipient_name = "Sernia Capital"

        if len(text) > 500:
            text = text[:500] + "..."

        lines.append(f"[{created}] {sender_name} → {recipient_name}: {text}")

    return "\n".join(lines)
