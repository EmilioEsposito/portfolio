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


async def _fetch_message_by_id(
    client: httpx.AsyncClient, activity_id: str,
) -> dict | None:
    try:
        resp = await client.get(f"/v1/messages/{activity_id}")
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return resp.json().get("data")


async def _fetch_call_by_id(
    client: httpx.AsyncClient, activity_id: str,
) -> dict | None:
    try:
        resp = await client.get(f"/v1/calls/{activity_id}")
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return resp.json().get("data")


async def _fetch_activity_by_id(
    client: httpx.AsyncClient, activity_id: str,
) -> dict | None:
    """Fetch a Quo activity (message or call) by its ``AC...`` ID.

    Both share the same ID prefix and the conversation object exposes only
    ``lastActivityId`` (no type marker), so we probe both endpoints in
    parallel and return whichever succeeds. Group threads can ONLY be read
    this way — OpenPhone's ``/v1/messages?participants[]=…`` silently filters
    to 1:1 threads regardless of how many participants are passed.
    """
    msg, call = await asyncio.gather(
        _fetch_message_by_id(client, activity_id),
        _fetch_call_by_id(client, activity_id),
    )
    if call is not None:
        return call | {"_kind": "call"}
    if msg is not None:
        return msg | {"_kind": "message"}
    return None


async def _find_group_conversation(
    client: httpx.AsyncClient, participants: list[str],
) -> dict | None:
    """Find the OpenPhone conversation whose participants exactly match the
    given set (regardless of ordering). Returns None if none found. Pages
    through up to 5 pages of conversations (~500) — enough for an active
    inbox of any realistic size.
    """
    target = frozenset(participants)
    page_token: str | None = None
    for _ in range(5):
        params: list[tuple[str, str]] = [
            ("phoneNumbers[]", QUO_SHARED_EXTERNAL_PHONE_ID),
            ("maxResults", "100"),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        try:
            resp = await client.get("/v1/conversations", params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        data = resp.json()
        for conv in data.get("data", []):
            if frozenset(conv.get("participants") or []) == target:
                return conv
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None


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

        # Build the snippet for each thread by picking whichever of the
        # latest message / latest call has the more recent ``createdAt``.
        # Group threads (>1 participant) require a different path:
        # OpenPhone's ``/v1/messages?participants[]=…`` filter silently
        # narrows to 1:1 even when both participants are passed, so
        # per-participant fetches return the wrong thread. Instead, follow
        # the conversation's ``lastActivityId`` — that always points at the
        # actual most-recent activity for the thread.
        async def _fetch_snippet_1to1(phone: str) -> dict | None:
            msg, call = await asyncio.gather(
                _fetch_latest_message(client, phone),
                _fetch_latest_call(client, phone),
            )
            if msg and call:
                return (
                    msg | {"_kind": "message"}
                    if msg.get("createdAt", "") >= call.get("createdAt", "")
                    else call | {"_kind": "call"}
                )
            if call:
                return call | {"_kind": "call"}
            if msg:
                return msg | {"_kind": "message"}
            return None

        async def _fetch_snippet_for_conv(conv: dict) -> dict | None:
            participants = conv.get("participants") or []
            if len(participants) > 1:
                last_id = conv.get("lastActivityId")
                if last_id:
                    return await _fetch_activity_by_id(client, last_id)
                return None
            if participants:
                return await _fetch_snippet_1to1(participants[0])
            return None

        snippet_results = await asyncio.gather(
            *(_fetch_snippet_for_conv(c) for c in conversations),
            return_exceptions=True,
        )
        # Index by conversation id so we don't lose group-thread snippets
        # to phone-key collisions when two conversations share a participant.
        snippet_map: dict[str, dict] = {}
        for conv, result in zip(conversations, snippet_results, strict=False):
            if isinstance(result, dict):
                snippet_map[conv.get("id", "")] = result

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
        latest = snippet_map.get(conv_id)
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
                        sender_phone = (
                            latest.get("from_") or latest.get("from") or ""
                        )
                        sender_label = phone_map.get(
                            sender_phone, sender_phone
                        ).split(" (")[0] if sender_phone else "Them"
                        snippet_line = f"\n  Snippet: {sender_label}: {preview}"

        lines.append(
            f"Thread: {', '.join(enriched)}{snippet_line}\n"
            f"  Last activity: {last_activity}\n"
            f"  Conversation ID: {conv_id}"
        )

    return f"Active threads ({len(conversations)}):\n\n" + "\n\n".join(lines)


async def _fetch_one_to_one_thread(
    client: httpx.AsyncClient,
    phone_number: str,
    max_results: int,
) -> tuple[list[dict], list[dict]] | str:
    """Fetch SMS + call list for a single phone (1:1 thread).

    Returns ``(messages, calls)`` on success, or an error string on failure.
    """
    async def _fetch(path: str) -> dict:
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

    try:
        msg_data, call_data = await asyncio.gather(
            _fetch("/v1/messages"),
            _fetch("/v1/calls"),
        )
    except httpx.HTTPError as exc:
        return f"Error fetching thread for {phone_number}: {exc}"

    return msg_data.get("data", []), call_data.get("data", [])


def _render_thread(
    messages: list[dict],
    calls: list[dict],
    contact_name: str,
    phone_number: str,
    phone_map: dict[str, str],
    *,
    header_prefix: str = "Thread with",
) -> str:
    """Render a chronological thread (SMS + calls interleaved)."""
    items: list[tuple[str, dict]] = (
        [("message", m) for m in messages] + [("call", c) for c in calls]
    )
    items.sort(key=lambda kv: kv[1].get("createdAt", ""))

    lines: list[str] = [
        f"{header_prefix} {contact_name} ({phone_number}) — "
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
                    if isinstance(sender_phone, str) else "?"
                )
                recipient_name = "Sernia Capital"

            if len(text) > 500:
                text = text[:500] + "..."

            lines.append(f"[{created}] {sender_name} → {recipient_name}: {text}")

    return "\n".join(lines)


def _format_group_activity_line(
    item: dict,
    phone_map: dict[str, str],
) -> str:
    """Render a single group-thread activity (message or call) as one line."""
    created = item.get("createdAt", "?")
    if item.get("_kind") == "call":
        direction = item.get("direction", "?")
        duration = item.get("duration")
        dur_str = f"{duration}s" if isinstance(duration, int) else "?s"
        return (
            f"[{created}] CALL ({direction}, {dur_str}) — "
            f"Call ID {item.get('id', '?')}"
        )
    text = (item.get("text") or item.get("body") or "(no text)")[:500]
    sender_phone = item.get("from_") or item.get("from") or ""
    sender_name = (
        phone_map.get(sender_phone, sender_phone)
        if isinstance(sender_phone, str) else "?"
    )
    to_phones = item.get("to") or []
    to_names = ", ".join(
        phone_map.get(p, p) if phone_map.get(p, p) != p else p for p in to_phones
    )
    return f"[{created}] {sender_name} → {to_names}: {text}"


async def get_thread_messages_core(
    phone_number: str | list[str],
    max_results: int = 20,
) -> str:
    """Get the recent thread (SMS + calls) for ``phone_number``.

    Accepts either a single phone (1:1 thread) or a list of phones (group
    thread). Group threads are an OpenPhone API limitation: the
    ``/v1/messages?participants[]=…`` filter silently narrows to 1:1 even
    when multiple participants are passed. So for group threads we surface
    the most recent group activity via the conversation's ``lastActivityId``
    and supplement with each participant's 1:1 history (clearly labeled).

    Call entries include the Call ID so the caller can chain to
    ``get_call_details_core`` for the summary + transcript.
    """
    participants_in: list[str] = (
        [phone_number] if isinstance(phone_number, str) else list(phone_number)
    )
    if len(participants_in) == 0:
        return "No phone numbers provided."

    # Deduplicate + sort so that [A, B] and [B, A] produce byte-identical
    # output. The OpenPhone conversation lookup is already order-insensitive
    # (frozenset match), but the rendered labels and per-participant sections
    # would otherwise vary by input order.
    participants_in = sorted(set(participants_in))

    async with build_quo_client() as client:
        try:
            contacts = await get_all_contacts(client)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"Quo API error: {exc}") from exc
        phone_map = _build_phone_map(contacts)

        if len(participants_in) == 1:
            only_phone = participants_in[0]
            result = await _fetch_one_to_one_thread(client, only_phone, max_results)
            if isinstance(result, str):
                raise ExternalServiceError(result)
            messages, calls = result
            if not messages and not calls:
                return f"No messages or calls found with {only_phone}."
            return _render_thread(
                messages, calls,
                phone_map.get(only_phone, only_phone), only_phone, phone_map,
            )

        # ---- Group thread path ----
        # TODO(group-thread-db): The sister monorepo `api/src/sernia_ai`
        # serves full group-thread history from a webhook-ingested
        # `open_phone_events` Postgres table (see
        # `api/src/sernia_ai/tools/quo_tools.py::_fetch_group_thread_from_events_table`).
        # We DON'T do that here on purpose — the MCP service is intentionally
        # lean (no SQLAlchemy / asyncpg per CLAUDE.md), and we'd rather not
        # couple to the backend's DB schema directly.
        # When we want to close this gap, the cleanest path is to expose a
        # service-internal HTTP endpoint on the FastAPI backend
        # (e.g. GET /api/open-phone/conversations/{id}/messages) gated by the
        # existing SERNIA_MCP_INTERNAL_BEARER_TOKEN, and call it from here.
        # Until then, we fall back to the API-only path below and the caveat
        # makes the limitation explicit to the MCP client.
        conv = await _find_group_conversation(client, participants_in)
        last_activity: dict | None = None
        conv_id = "?"
        if conv is not None:
            conv_id = conv.get("id", "?")
            last_id = conv.get("lastActivityId")
            if last_id:
                last_activity = await _fetch_activity_by_id(client, last_id)

        per_participant = await asyncio.gather(
            *(_fetch_one_to_one_thread(client, p, max_results) for p in participants_in),
        )

    participant_labels = ", ".join(
        f"{phone_map.get(p, p)} ({p})" if phone_map.get(p, p) != p else p
        for p in participants_in
    )
    out: list[str] = [
        f"Group thread: {participant_labels}",
        f"Conversation ID: {conv_id}",
        "",
        "**Caveat — partial data:** OpenPhone's public API does not expose "
        "group-thread message history by participant filter. Only the *most "
        "recent* group activity (via the conversation's `lastActivityId`) "
        "and each participant's 1:1 thread can be listed. Older group "
        "messages exist but cannot be retrieved through this tool — view "
        "them in the OpenPhone app, or wait for backend-side group-thread "
        "support (TODO: see comment in core/quo/contacts.py).",
        "",
        "## Most recent group activity",
    ]
    if last_activity is not None:
        out.append(_format_group_activity_line(last_activity, phone_map))
    else:
        out.append("_(no recent group activity available)_")

    out.append("")
    for phone, result in zip(participants_in, per_participant, strict=False):
        contact_name = phone_map.get(phone, phone)
        out.append(f"## 1:1 thread with {contact_name}")
        if isinstance(result, str):
            out.append(result)
        else:
            messages, calls = result
            if not messages and not calls:
                out.append(f"_(no 1:1 messages or calls with {phone})_")
            else:
                out.append(_render_thread(
                    messages, calls, contact_name, phone, phone_map,
                    header_prefix="1:1 with",
                ))
        out.append("")

    return "\n".join(out).rstrip()
