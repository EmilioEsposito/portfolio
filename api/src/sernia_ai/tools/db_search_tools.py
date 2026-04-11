"""
Database search tools — conversation history + SMS history search.

Queries the agent_conversations and open_phone_events tables.
"""

from datetime import datetime, timedelta

import logfire
from pydantic_ai import FunctionToolset, RunContext
from sqlalchemy import cast, select, String, or_, and_

from api.src.ai_demos.models import AgentConversation
from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.models import OpenPhoneEvent
from api.src.sernia_ai.deps import SerniaDeps
from api.src.utils.fuzzy_json import fuzzy_filter

db_search_toolset = FunctionToolset()


# ---------------------------------------------------------------------------
# Shared SMS helpers
# ---------------------------------------------------------------------------

async def _resolve_contact_phones(contact_name: str) -> tuple[str, list[str]]:
    """Fuzzy-match a contact name to a display name and list of E.164 phone numbers.

    Uses the TTL-cached contact list from open_phone.service.

    Returns:
        (display_name, ["+14155550100", ...])

    Raises:
        ValueError: If no matching contact found.
    """
    from api.src.open_phone.service import get_all_contacts

    contacts = await get_all_contacts()

    matches = fuzzy_filter(contacts, contact_name, top_n=1, threshold=50)
    if not matches:
        raise ValueError(f"No contact found matching '{contact_name}'")

    contact = matches[0][0]
    # Extract display name
    first = contact.get("defaultFields", {}).get("firstName", "")
    last = contact.get("defaultFields", {}).get("lastName", "")
    company = contact.get("defaultFields", {}).get("company", "")
    display_name = f"{first} {last}".strip()
    if company and company != display_name:
        display_name = f"{display_name} ({company})" if display_name else company

    # Extract all phone numbers
    phones = []
    for pn in contact.get("defaultFields", {}).get("phoneNumbers", []):
        val = pn.get("value")
        if val:
            phones.append(val)

    if not phones:
        raise ValueError(f"Contact '{display_name}' has no phone numbers")

    return display_name, phones


def _build_contact_map(contacts: list[dict]) -> dict[str, str]:
    """Build a phone→display_name map from the full contact list."""
    phone_map: dict[str, str] = {}
    for contact in contacts:
        first = contact.get("defaultFields", {}).get("firstName", "")
        last = contact.get("defaultFields", {}).get("lastName", "")
        company = contact.get("defaultFields", {}).get("company", "")
        name = f"{first} {last}".strip()
        if company and company != name:
            name = f"{name} ({company})" if name else company
        if not name:
            name = "Unknown"
        for pn in contact.get("defaultFields", {}).get("phoneNumbers", []):
            val = pn.get("value")
            if val:
                phone_map[val] = name
    return phone_map


def _enrich_phone(phone: str | None, contact_map: dict[str, str]) -> str:
    """Resolve a phone number to a contact name, or return the number as-is."""
    if not phone:
        return "Unknown"
    return contact_map.get(phone, phone)


def _format_sms_event(
    event: OpenPhoneEvent,
    contact_map: dict[str, str],
    is_match: bool = False,
) -> str:
    """Format a single SMS event into a readable line."""
    ts = event.event_timestamp or event.created_at
    ts_str = ts.strftime("%Y-%m-%d %I:%M %p") if ts else "?"
    sender = _enrich_phone(event.from_number, contact_map)
    recipient = _enrich_phone(event.to_number, contact_map)
    text = event.message_text or "(no text)"
    match_marker = "  <-- MATCH" if is_match else ""
    return f"[{ts_str}] {sender} -> {recipient}: {text}{match_marker}"


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a YYYY-MM-DD date string, returning None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _build_phone_filter(phones: list[str]):
    """Build a SQLAlchemy OR filter for from_number/to_number matching any phone."""
    return or_(
        OpenPhoneEvent.from_number.in_(phones),
        OpenPhoneEvent.to_number.in_(phones),
    )


def _build_date_filters(after: str | None, before: str | None) -> list:
    """Build SQLAlchemy date range filters from optional YYYY-MM-DD strings."""
    filters = []
    after_dt = _parse_date(after)
    before_dt = _parse_date(before)
    if after_dt:
        filters.append(OpenPhoneEvent.event_timestamp >= after_dt)
    if before_dt:
        # Include the entire "before" day
        filters.append(
            OpenPhoneEvent.event_timestamp < before_dt.replace(hour=0, minute=0, second=0)
            + timedelta(days=1)
        )
    return filters


async def _get_contact_map() -> dict[str, str]:
    """Fetch all contacts and build a phone→name map for enrichment."""
    from api.src.open_phone.service import get_all_contacts
    from api.src.sernia_ai.tools.quo_tools import _build_quo_client

    client = _build_quo_client()
    try:
        contacts = await get_all_contacts(client)
    except Exception:
        logfire.warning("Failed to fetch contacts for phone enrichment")
        return {}
    finally:
        await client.aclose()
    return _build_contact_map(contacts)


# ---------------------------------------------------------------------------
# Tool: search_conversations (existing)
# ---------------------------------------------------------------------------


@db_search_toolset.tool
async def search_conversations(
    ctx: RunContext[SerniaDeps],
    query: str,
    limit: int = 10,
) -> str:
    """Search past agent conversations for a text query.

    Args:
        query: Text to search for in conversation messages (case-insensitive).
        limit: Maximum number of results to return (default 10).
    """
    async with AsyncSessionFactory() as session:
        stmt = (
            select(AgentConversation)
            .where(
                cast(AgentConversation.messages, String).ilike(f"%{query}%"),
            )
            .order_by(AgentConversation.updated_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        conversations = result.scalars().all()

    if not conversations:
        return f"No conversations found matching '{query}'."

    lines = []
    for conv in conversations:
        ts = conv.updated_at.strftime("%Y-%m-%d %I:%M %p") if conv.updated_at else "?"
        # Extract a snippet from messages containing the query
        snippet = _extract_snippet(conv.messages, query)
        lines.append(
            f"[{ts}] agent: {conv.agent_name}, id: {conv.id}\n"
            f"  {snippet}"
        )
    return "\n\n".join(lines)


def _extract_snippet(messages: list[dict], query: str, max_len: int = 200) -> str:
    """Find the first message containing the query and return a snippet."""
    query_lower = query.lower()
    for msg in reversed(messages):  # Search newest first
        # Messages are stored as PydanticAI ModelMessage dicts
        for part in msg.get("parts", []):
            content = part.get("content", "")
            if isinstance(content, str) and query_lower in content.lower():
                idx = content.lower().index(query_lower)
                start = max(0, idx - 50)
                end = min(len(content), idx + max_len)
                snippet = content[start:end].replace("\n", " ")
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."
                return snippet
    return "(match in message metadata)"


# ---------------------------------------------------------------------------
# Tool: get_contact_sms_history (chronological thread, no keyword required)
# ---------------------------------------------------------------------------


@db_search_toolset.tool
async def get_contact_sms_history(
    ctx: RunContext[SerniaDeps],
    contact_name: str,
    days_back: int = 30,
    max_messages: int = 50,
) -> str:
    """Get chronological SMS history for a contact — no keyword needed.

    Returns all messages to/from a contact in chronological order.
    Use this when you need more conversation context beyond the default
    trimmed window (e.g. to understand prior discussions before replying).
    For keyword-based search, use search_sms_history instead.

    Args:
        contact_name: Contact name (fuzzy matched) or phone number.
        days_back: How far back to look (default 30 days).
        max_messages: Maximum messages to return (default 50).
    """
    try:
        display_name, phones = await _resolve_contact_phones(contact_name)
    except ValueError as e:
        return str(e)

    after_dt = datetime.now() - timedelta(days=days_back)

    filters = [
        OpenPhoneEvent.event_type.like("message.%"),
        OpenPhoneEvent.message_text.isnot(None),
        _build_phone_filter(phones),
        OpenPhoneEvent.event_timestamp >= after_dt,
    ]

    async with AsyncSessionFactory() as session:
        stmt = (
            select(OpenPhoneEvent)
            .where(and_(*filters))
            .order_by(OpenPhoneEvent.event_timestamp.asc())
            .limit(max_messages)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()

    if not events:
        return f"No SMS messages found for '{display_name}' in the last {days_back} days."

    contact_map = await _get_contact_map()
    lines = [f"SMS history with {display_name} — {len(events)} messages (last {days_back} days)\n"]
    for evt in events:
        lines.append(_format_sms_event(evt, contact_map))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: search_sms_history
# ---------------------------------------------------------------------------

# Context window: how many messages to show around each keyword match
_CONTEXT_RADIUS = 5


@db_search_toolset.tool
async def search_sms_history(
    ctx: RunContext[SerniaDeps],
    query: str,
    contact_name: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 5,
) -> str:
    """Search SMS message history by keyword, with optional contact and date filters.

    Returns matching messages with ~10 surrounding messages for conversation context.

    Args:
        query: Text to search for in message content (case-insensitive).
        contact_name: Optional — filter to a specific contact (fuzzy matched).
                      Supports partial names, building/unit numbers, typos.
                      Examples: "John", "Unit 203", "Peppino Bldg A".
        after: Optional — only messages after this date (YYYY-MM-DD).
        before: Optional — only messages before this date (YYYY-MM-DD).
        limit: Max matching messages to return (default 5).
               Each match includes ~10 surrounding messages for context.
    """
    # Build filters
    filters = [
        OpenPhoneEvent.event_type.like("message.%"),
        OpenPhoneEvent.message_text.isnot(None),
        OpenPhoneEvent.message_text.ilike(f"%{query}%"),
    ]

    contact_display = None
    if contact_name:
        try:
            contact_display, phones = await _resolve_contact_phones(contact_name)
            filters.append(_build_phone_filter(phones))
        except ValueError as e:
            return str(e)

    filters.extend(_build_date_filters(after, before))

    async with AsyncSessionFactory() as session:
        # Find matching messages
        stmt = (
            select(OpenPhoneEvent)
            .where(and_(*filters))
            .order_by(OpenPhoneEvent.event_timestamp.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        matches = result.scalars().all()

        if not matches:
            contact_note = f" for contact '{contact_name}'" if contact_name else ""
            return f"No SMS messages found matching '{query}'{contact_note}."

        # Fetch contact map for phone enrichment
        contact_map = await _get_contact_map()

        # For each match, fetch surrounding context messages
        sections: list[str] = []
        seen_ids: set[int] = set()

        for i, match in enumerate(matches, 1):
            if not match.conversation_id:
                # No conversation thread — show just the match
                sections.append(
                    f"=== Match {i} of {len(matches)} ===\n"
                    + _format_sms_event(match, contact_map, is_match=True)
                )
                continue

            # Fetch context window around this message
            context_stmt = (
                select(OpenPhoneEvent)
                .where(
                    OpenPhoneEvent.conversation_id == match.conversation_id,
                    OpenPhoneEvent.event_type.like("message.%"),
                )
                .order_by(OpenPhoneEvent.event_timestamp.asc())
            )
            ctx_result = await session.execute(context_stmt)
            thread = ctx_result.scalars().all()

            # Find the match position and extract a window
            match_idx = None
            for j, evt in enumerate(thread):
                if evt.id == match.id:
                    match_idx = j
                    break

            if match_idx is None:
                match_idx = 0

            start = max(0, match_idx - _CONTEXT_RADIUS)
            end = min(len(thread), match_idx + _CONTEXT_RADIUS + 1)
            window = thread[start:end]

            # Skip if we've already shown this context (overlapping windows)
            window_ids = {evt.id for evt in window}
            if window_ids & seen_ids:
                continue
            seen_ids.update(window_ids)

            # Determine conversation partner name
            partner = contact_display
            if not partner:
                # Try to find a non-Sernia phone in the thread
                for evt in window:
                    for phone in [evt.from_number, evt.to_number]:
                        name = contact_map.get(phone)
                        if name and "sernia" not in name.lower():
                            partner = name
                            break
                    if partner:
                        break

            header = f"=== Match {i} of {len(matches)}"
            if partner:
                header += f" (conversation with {partner})"
            header += " ==="

            lines = [header]
            for evt in window:
                lines.append(_format_sms_event(evt, contact_map, is_match=(evt.id == match.id)))
            sections.append("\n".join(lines))

    return "\n\n".join(sections)

