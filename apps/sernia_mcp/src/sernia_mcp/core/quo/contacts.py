"""Quo contact search + conversation thread retrieval (SMS + calls)."""
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


def _format_call_snippet(call: dict) -> str:
    """One-line snippet for a call activity, including the Call ID so the
    caller can fetch summary + transcript via ``get_call_details_core``."""
    direction = call.get("direction") or "?"
    duration = call.get("duration")
    status = call.get("status") or ""
    dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
    bits = [direction, dur_str]
    if status and status != "completed":
        bits.append(status)
    return f"Call ({', '.join(bits)}) — Call ID {call.get('id', '?')}"


async def _fetch_latest_message(
    client: httpx.AsyncClient, phone: str,
) -> dict | None:
    """Fetch the most recent SMS for a phone on the shared team line."""
    try:
        resp = await client.get(
            "/v1/messages",
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone,
                "maxResults": "1",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    msgs = resp.json().get("data", [])
    return msgs[0] if msgs else None


async def _fetch_latest_call(
    client: httpx.AsyncClient, phone: str,
) -> dict | None:
    """Fetch the most recent call for a phone on the shared team line."""
    try:
        resp = await client.get(
            "/v1/calls",
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone,
                "maxResults": "1",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    calls = resp.json().get("data", [])
    return calls[0] if calls else None


def _format_call_timestamp(seconds: float | int | None) -> str:
    """Render dialogue offsets as ``M:SS`` (or ``H:MM:SS`` for long calls)."""
    if not isinstance(seconds, (int, float)):
        return "?:??"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def get_call_details_core(
    call_id: str,
    transcript_max_chars: int = 4000,
) -> str:
    """Fetch a Quo call's summary AND transcript in one shot, rendered as
    markdown (summary on top, transcript below). Pairs with the Call ID
    surfaced by ``list_active_threads_core`` / ``get_thread_messages_core``.

    Transcript is truncated at ``transcript_max_chars`` (default 4000) — the
    caller can pass a larger value for the full text of long calls.
    """
    async with build_quo_client() as client:
        async def _fetch_call() -> dict | None:
            try:
                resp = await client.get(f"/v1/calls/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

        async def _fetch_summary() -> dict | None:
            try:
                resp = await client.get(f"/v1/call-summaries/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

        async def _fetch_transcript() -> dict | None:
            try:
                resp = await client.get(f"/v1/call-transcripts/{call_id}")
                resp.raise_for_status()
                return resp.json().get("data")
            except httpx.HTTPError:
                return None

        call, summary, transcript = await asyncio.gather(
            _fetch_call(), _fetch_summary(), _fetch_transcript(),
        )

        if call is None and summary is None and transcript is None:
            return (
                f"No call found with ID {call_id} "
                "(or transcript/summary not yet ready)."
            )

        try:
            contacts = await get_all_contacts(client)
            phone_map = _build_phone_map(contacts)
        except httpx.HTTPError:
            phone_map = {}

    parts: list[str] = [f"# Call {call_id}\n"]

    if call:
        meta_bits = []
        direction = call.get("direction")
        if direction:
            meta_bits.append(f"**Direction:** {direction}")
        duration = call.get("duration")
        if isinstance(duration, int):
            meta_bits.append(f"**Duration:** {duration}s")
        status = call.get("status")
        if status:
            meta_bits.append(f"**Status:** {status}")
        created = call.get("createdAt")
        if created:
            meta_bits.append(f"**Created:** {created}")
        participants = call.get("participants") or []
        if participants:
            named = [
                f"{phone_map.get(p, p)} ({p})" if phone_map.get(p, p) != p else p
                for p in participants
            ]
            meta_bits.append(f"**Participants:** {', '.join(named)}")
        if meta_bits:
            parts.append("\n".join(meta_bits) + "\n")

    parts.append("## Summary\n")
    if summary:
        bullets = summary.get("summary") or []
        if bullets:
            parts.append("\n".join(f"- {b}" for b in bullets))
        else:
            parts.append("_(no summary text available)_")

        next_steps = summary.get("nextSteps") or []
        if next_steps:
            parts.append("\n\n### Next Steps\n")
            parts.append("\n".join(f"- {s}" for s in next_steps))
        parts.append("\n")
    else:
        parts.append("_(summary not available — Quo may still be generating it)_\n")

    parts.append("## Transcript\n")
    if transcript:
        dialogue = transcript.get("dialogue") or []
        if not dialogue:
            parts.append("_(transcript empty)_")
        else:
            lines: list[str] = []
            running = 0
            truncated = False
            for turn in dialogue:
                ts = _format_call_timestamp(turn.get("start"))
                speaker_phone = turn.get("identifier") or "?"
                speaker_name = phone_map.get(speaker_phone, speaker_phone)
                team_tag = " (team)" if turn.get("userId") else ""
                content = (turn.get("content") or "").strip()
                line = f"[{ts}] {speaker_name}{team_tag}: {content}"
                if running + len(line) + 1 > transcript_max_chars:
                    truncated = True
                    break
                lines.append(line)
                running += len(line) + 1
            parts.append("\n".join(lines))
            if truncated:
                parts.append(
                    f"\n\n_(transcript truncated at {transcript_max_chars} chars; "
                    f"call ``quo_get_call_details`` with a larger "
                    f"``transcript_max_chars`` to see more)_"
                )
    else:
        parts.append("_(transcript not available — Quo may still be generating it)_")

    return "\n".join(parts)


async def search_contacts_core(query: str) -> str:
    """Fuzzy-search Quo contacts by name, phone, or company. Returns top 5 as JSON."""
    async with build_quo_client() as client:
        contacts = await get_all_contacts(client)
    return fuzzy_filter_json(contacts, query, top_n=5)


async def list_active_threads_core(
    max_results: int = 20,
    updated_after_days: int | None = None,
) -> str:
    """List active conversation threads on the shared team number.

    Mirrors the Quo active inbox: pages through ``/v1/conversations`` filtering
    out 'done' threads (snoozed 100+ years out), then sorts by most recent
    activity. Each thread is enriched with contact names and a one-line
    snippet of the most recent activity — SMS or call — fetched in parallel.
    Call snippets include the Call ID so the caller can chain to
    ``get_call_details_core`` for the summary + transcript.
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

        # Fetch latest message + latest call per thread; pick whichever is
        # more recent. The conversation object's ``lastActivityId`` shares
        # the ``AC...`` prefix between calls and messages, so we have to
        # fetch both to know which it is.
        async def _fetch_latest(participant_phone: str) -> dict | None:
            msg, call = await asyncio.gather(
                _fetch_latest_message(client, participant_phone),
                _fetch_latest_call(client, participant_phone),
            )
            if msg and call:
                if msg.get("createdAt", "") >= call.get("createdAt", ""):
                    return msg
                return call | {"_kind": "call"}
            if call:
                return call | {"_kind": "call"}
            return msg

        snippet_phones = [
            (conv.get("participants") or [""])[0] for conv in conversations
        ]
        snippet_results = await asyncio.gather(
            *(_fetch_latest(p) for p in snippet_phones if p),
            return_exceptions=True,
        )
        snippet_map: dict[str, dict] = {}
        for phone, result in zip(snippet_phones, snippet_results, strict=False):
            if isinstance(result, dict):
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
        latest = snippet_map.get(ext_phone)
        if latest:
            if latest.get("_kind") == "call":
                snippet_line = f"\n  Snippet: {_format_call_snippet(latest)}"
            else:
                direction = latest.get("direction", "")
                text = latest.get("text") or latest.get("body") or ""
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
    """Get the recent thread (SMS + calls) for ``phone_number``.

    Returns SMS messages and calls interleaved chronologically (oldest →
    newest), enriched with contact names. Call entries include the Call ID
    so the caller can chain to ``get_call_details_core`` for the
    summary + transcript.
    """
    async def _fetch(client: httpx.AsyncClient, path: str) -> dict:
        resp = await client.get(
            path,
            params={
                "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
                "participants": phone_number,
                "maxResults": str(max_results),
            },
        )
        resp.raise_for_status()
        return resp.json()

    async with build_quo_client() as client:
        try:
            msg_data, call_data = await asyncio.gather(
                _fetch(client, "/v1/messages"),
                _fetch(client, "/v1/calls"),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"Quo API error: {exc}") from exc

        messages = msg_data.get("data", [])
        calls = call_data.get("data", [])
        if not messages and not calls:
            return f"No messages or calls found with {phone_number}."

        contacts = await get_all_contacts(client)

    phone_map = _build_phone_map(contacts)
    contact_name = phone_map.get(phone_number, phone_number)

    items: list[tuple[str, dict]] = (
        [("message", m) for m in messages] + [("call", c) for c in calls]
    )
    items.sort(key=lambda kv: kv[1].get("createdAt", ""))

    lines: list[str] = [
        f"Thread with {contact_name} ({phone_number}) — "
        f"{len(messages)} message{'s' if len(messages) != 1 else ''}, "
        f"{len(calls)} call{'s' if len(calls) != 1 else ''}\n"
    ]

    for kind, item in items:
        created = item.get("createdAt", "?")
        if kind == "call":
            direction = item.get("direction", "?")
            duration = item.get("duration")
            dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
            status = item.get("status") or ""
            status_str = f", {status}" if status and status != "completed" else ""
            arrow = (
                f"{contact_name} → Sernia Capital" if direction == "incoming"
                else f"Sernia Capital → {contact_name}"
            )
            lines.append(
                f"[{created}] CALL {arrow} ({direction}, {dur_str}{status_str}) "
                f"— Call ID {item.get('id', '?')}"
            )
        else:
            direction = item.get("direction", "?")
            text = item.get("text") or item.get("body") or "(no text)"
            sender_phone = item.get("from_") or item.get("from", "?")

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
