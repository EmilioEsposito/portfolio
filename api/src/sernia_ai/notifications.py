"""SMS notifications for the general agent's internal team workflows."""

import os

import httpx
import logfire

_RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")

# ---------------------------------------------------------------------------
# SMS team notification (shared Quo number)
# ---------------------------------------------------------------------------

_shared_team_phone: str | None = None


async def _get_shared_team_phone() -> str | None:
    """Look up the shared team phone number from Quo, caching at module level."""
    global _shared_team_phone
    if _shared_team_phone is not None:
        return _shared_team_phone

    from api.src.sernia_ai.config import QUO_SHARED_TEAM_CONTACT_ID

    api_key = os.environ.get("QUO_API_KEY_PORTFOLIO", "")
    if not api_key:
        logfire.error("QUO_API_KEY_PORTFOLIO not set — cannot look up shared team phone")
        return None

    try:
        async with httpx.AsyncClient(
            base_url="https://api.openphone.com",
            headers={"Authorization": api_key},
            timeout=15,
        ) as client:
            resp = await client.get(f"/v1/contacts/{QUO_SHARED_TEAM_CONTACT_ID}")
            resp.raise_for_status()
            data = resp.json().get("data", {})
            phones = data.get("defaultFields", {}).get("phoneNumbers", [])
            if phones:
                _shared_team_phone = phones[0].get("value")
                logfire.info("cached shared team phone", phone=_shared_team_phone)
                return _shared_team_phone
            logfire.error(
                "shared team contact has no phone numbers", contact_id=QUO_SHARED_TEAM_CONTACT_ID
            )
    except Exception:
        logfire.exception("failed to look up shared team phone number")
    return None


async def notify_team_sms(
    title: str,
    body: str,
    conversation_id: str,
) -> None:
    """Send an SMS to the shared Quo team number with a deeplink to the web chat conversation.

    Failures are logged but never re-raised — SMS should not block trigger flow.
    """
    from api.src.open_phone.service import send_message
    from api.src.sernia_ai.config import (
        FRONTEND_BASE_URL,
        QUO_SERNIA_AI_PHONE_ID,
    )

    try:
        to_phone = await _get_shared_team_phone()
        if not to_phone:
            return

        # Prefix title with environment label when not in production
        if _RAILWAY_ENV != "production":
            env_label = _RAILWAY_ENV.upper() if _RAILWAY_ENV else "LOCAL"
            title = f"[{env_label}] {title}"

        deeplink = f"{FRONTEND_BASE_URL}/sernia-chat?id={conversation_id}"
        message = f"{title}\n{body}\n\n{deeplink}"

        await send_message(
            message=message,
            to_phone_number=to_phone,
            from_phone_number=QUO_SERNIA_AI_PHONE_ID,
        )
        logfire.info("team SMS sent", conversation_id=conversation_id)
    except Exception:
        logfire.exception("notify_team_sms failed", conversation_id=conversation_id)
