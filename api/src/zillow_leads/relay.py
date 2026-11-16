"""The one rule for where the lead agent's email may go: a Zillow relay address.

Zillow gives each lead a relay address, and answering it is the only kind of
outbound email the lead agent sends. ``handle.reply_route`` applies this rule
to the lead's latest message before the agent runs; the showing reminder by
email (``showing_bookings/lead_email.py``) applies it again to the route stored
on the conversation, just before it sends. Kept free of heavy imports so both
can use it.
"""

from api.src.google.common.config import INTERNAL_EMAIL_DOMAIN

ZILLOW_DOMAIN = "zillow.com"


def relay_address(address: str | None) -> str | None:
    """The address, trimmed and lowercased, if it is a Zillow relay; else None.

    Refused: anything not at zillow.com or a subdomain of it, one of our own
    addresses, and a no-reply sender.
    """
    address = (address or "").strip().lower()
    local, _, domain = address.partition("@")
    at_zillow = domain == ZILLOW_DOMAIN or domain.endswith(f".{ZILLOW_DOMAIN}")
    if not local or not at_zillow or domain == INTERNAL_EMAIL_DOMAIN:
        return None
    if local.replace("-", "").replace("_", "") in {"noreply", "donotreply"}:
        return None
    return address
