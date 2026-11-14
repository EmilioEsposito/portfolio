"""Persisted handler identity used to route Zillow conversations."""

from typing import Any

HANDLER = "zillow_lead_agent"


def is_lead_conversation(metadata: dict[str, Any] | None) -> bool:
    return bool(metadata) and metadata.get("handler") == HANDLER
