"""Web Push + SMS notification service for Sernia AI HITL approvals."""

import asyncio
import json
import os

import httpx
import logfire
from py_vapid import Vapid
from pywebpush import webpush, WebPushException
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory, provide_session
from api.src.sernia_ai.push.models import WebPushSubscription

_VAPID_PRIVATE_KEY_RAW = os.environ.get("VAPID_PRIVATE_KEY", "")
_RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@serniacapital.com")

# Build a Vapid object at module load so pywebpush doesn't have to parse
# the key on every send. Handles PEM strings from .env (with literal \n or
# real newlines) and raw base64 DER.
_vapid: Vapid | None = None
if _VAPID_PRIVATE_KEY_RAW:
    try:
        pem = _VAPID_PRIVATE_KEY_RAW.replace("\\n", "\n").strip()
        if pem.startswith("-----"):
            _vapid = Vapid.from_pem(pem.encode())
        else:
            _vapid = Vapid.from_string(pem)
    except Exception as e:
        # Log at warning level — missing/invalid VAPID key disables web push
        # but is not a critical error (graceful degradation)
        logfire.warn(
            "VAPID private key invalid or missing — web push disabled",
            error=str(e),
            key_length=len(_VAPID_PRIVATE_KEY_RAW),
        )


async def save_subscription(
    clerk_user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Upsert a web push subscription (keyed by endpoint)."""
    async with provide_session(session) as s:
        stmt = pg_insert(WebPushSubscription).values(
            clerk_user_id=clerk_user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
        ).on_conflict_do_update(
            index_elements=["endpoint"],
            set_={
                "clerk_user_id": clerk_user_id,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": user_agent,
            },
        )
        await s.execute(stmt)
        await s.commit()
        logfire.info("web push subscription saved", clerk_user_id=clerk_user_id)


async def remove_subscription(
    endpoint: str,
    clerk_user_id: str,
    session: AsyncSession | None = None,
) -> bool:
    """Remove a web push subscription. Returns True if a row was deleted."""
    async with provide_session(session) as s:
        result = await s.execute(
            delete(WebPushSubscription).where(
                WebPushSubscription.endpoint == endpoint,
                WebPushSubscription.clerk_user_id == clerk_user_id,
            )
        )
        await s.commit()
        deleted = result.rowcount > 0  # type: ignore[union-attr]
        logfire.info("web push subscription removed", deleted=deleted, clerk_user_id=clerk_user_id)
        return deleted


def _send_push(subscription_info: dict, payload: str):
    """Synchronous push send — run via asyncio.to_thread."""
    return webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=_vapid,
        vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
        ttl=86400,  # 24 hours — default 0 means "discard if not delivered immediately"
    )


async def notify_all_sernia_users(
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to all active Sernia subscriptions."""
    if not _vapid:
        logfire.warn("VAPID private key not loaded — skipping push notification")
        return

    # Prefix title with environment name when not in production
    if _RAILWAY_ENV != "production":
        env_label = _RAILWAY_ENV.upper() if _RAILWAY_ENV else "LOCAL"
        title = f"[{env_label}] {title}"

    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    expired_endpoints: list[str] = []

    async with AsyncSessionFactory() as session:
        result = await session.execute(select(WebPushSubscription))
        subs = result.scalars().all()

        logfire.info("web push sending", sub_count=len(subs), title=title)

        for sub in subs:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            }
            try:
                resp = await asyncio.to_thread(_send_push, subscription_info, payload)
                logfire.info(
                    "web push sent",
                    endpoint=sub.endpoint[:60],
                    status=getattr(resp, "status_code", "ok"),
                )
            except WebPushException as e:
                if "410" in str(e) or "404" in str(e):
                    expired_endpoints.append(sub.endpoint)
                    logfire.info("web push subscription expired", endpoint=sub.endpoint[:40])
                else:
                    logfire.exception("web push send error", endpoint=sub.endpoint[:40])
            except Exception:
                logfire.exception("web push unexpected error", endpoint=sub.endpoint[:40])

        # Clean up expired subscriptions
        if expired_endpoints:
            await session.execute(
                delete(WebPushSubscription).where(
                    WebPushSubscription.endpoint.in_(expired_endpoints)
                )
            )
            await session.commit()
            logfire.info("cleaned up expired web push subs", count=len(expired_endpoints))


async def notify_user_push(
    clerk_user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to a specific user's subscriptions only."""
    if not _vapid:
        logfire.warn("VAPID private key not loaded — skipping push notification")
        return

    if not clerk_user_id:
        logfire.warn("notify_user_push called with empty clerk_user_id — skipping")
        return

    # Prefix title with environment name when not in production
    if _RAILWAY_ENV != "production":
        env_label = _RAILWAY_ENV.upper() if _RAILWAY_ENV else "LOCAL"
        title = f"[{env_label}] {title}"

    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    expired_endpoints: list[str] = []

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(WebPushSubscription).where(
                WebPushSubscription.clerk_user_id == clerk_user_id
            )
        )
        subs = result.scalars().all()

        if not subs:
            logfire.info(
                "web push: no subscriptions for targeted user — falling back to all users",
                clerk_user_id=clerk_user_id,
                title=title,
            )
            await notify_all_sernia_users(title=title, body=body, data=data)
            return

        logfire.info("web push sending (user-targeted)", sub_count=len(subs), title=title, clerk_user_id=clerk_user_id)

        for sub in subs:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            }
            try:
                resp = await asyncio.to_thread(_send_push, subscription_info, payload)
                logfire.info(
                    "web push sent",
                    endpoint=sub.endpoint[:60],
                    status=getattr(resp, "status_code", "ok"),
                )
            except WebPushException as e:
                if "410" in str(e) or "404" in str(e):
                    expired_endpoints.append(sub.endpoint)
                    logfire.info("web push subscription expired", endpoint=sub.endpoint[:40])
                else:
                    logfire.exception("web push send error", endpoint=sub.endpoint[:40])
            except Exception:
                logfire.exception("web push unexpected error", endpoint=sub.endpoint[:40])

        # Clean up expired subscriptions
        if expired_endpoints:
            await session.execute(
                delete(WebPushSubscription).where(
                    WebPushSubscription.endpoint.in_(expired_endpoints)
                )
            )
            await session.commit()
            logfire.info("cleaned up expired web push subs", count=len(expired_endpoints))


async def notify_pending_approval(
    conversation_id: str,
    tool_name: str,
    tool_args: dict | None = None,
) -> None:
    """Send a push notification for a pending HITL approval."""
    friendly_name = tool_name.replace("_", " ").title()
    title = f"Approval Needed: {friendly_name}"

    # Build a concise body from tool args
    body_parts = []
    if tool_args:
        for key in ("to", "recipient", "name", "subject", "task_name"):
            if key in tool_args:
                body_parts.append(f"{key}: {tool_args[key]}")
    body = ", ".join(body_parts) if body_parts else "Action requires your approval"

    data = {
        "url": f"/sernia-chat?id={conversation_id}",
        "conversation_id": conversation_id,
        "tool_name": tool_name,
        "type": "approval",
    }

    try:
        await notify_all_sernia_users(title=title, body=body, data=data)
    except Exception:
        logfire.exception("notify_pending_approval failed", conversation_id=conversation_id)


async def notify_trigger_alert(
    conversation_id: str,
    trigger_source: str,
    title: str,
    body: str,
) -> None:
    """Send a push notification for a trigger-created conversation (not HITL approval)."""
    data = {
        "url": f"/sernia-chat?id={conversation_id}",
        "conversation_id": conversation_id,
        "type": "alert",
        "trigger_source": trigger_source,
    }

    try:
        await notify_all_sernia_users(title=title, body=body, data=data)
    except Exception:
        logfire.exception(
            "notify_trigger_alert failed",
            conversation_id=conversation_id,
            trigger_source=trigger_source,
        )


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

    api_key = os.environ.get("OPEN_PHONE_API_KEY", "")
    if not api_key:
        logfire.error("OPEN_PHONE_API_KEY not set — cannot look up shared team phone")
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
            logfire.error("shared team contact has no phone numbers", contact_id=QUO_SHARED_TEAM_CONTACT_ID)
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
    from api.src.sernia_ai.config import (
        FRONTEND_BASE_URL,
        QUO_SERNIA_AI_PHONE_ID,
    )
    from api.src.open_phone.service import send_message

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
