"""Quo contact search + SMS thread retrieval core functions."""
import httpx

from api.src.open_phone.service import get_all_contacts
from api.src.sernia_ai.config import QUO_SHARED_EXTERNAL_PHONE_ID
from api.src.tool_core.errors import ExternalServiceError
from api.src.tool_core.quo._client import build_quo_client
from api.src.utils.fuzzy_json import fuzzy_filter_json


def _build_phone_map(contacts: list[dict]) -> dict[str, str]:
    """Map phone number → contact display name.

    Quo contacts store names under ``defaultFields``. See the existing
    ``_build_phone_map`` in sernia_ai/tools/quo_tools.py for the reference
    (it also includes a property-unit prefix for tenants which we omit here
    to avoid duplicating the tenant-specific helper).
    """
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


async def search_contacts_core(query: str) -> str:
    """Fuzzy-search Quo contacts by name, phone, or company. Returns top 5 as JSON."""
    async with build_quo_client() as client:
        contacts = await get_all_contacts(client)
    return fuzzy_filter_json(contacts, query, top_n=5)


async def get_thread_messages_core(phone_number: str, max_results: int = 20) -> str:
    """Get recent SMS thread messages for a phone number on the shared team line.

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
    messages = list(reversed(messages))  # API returns newest-first

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
