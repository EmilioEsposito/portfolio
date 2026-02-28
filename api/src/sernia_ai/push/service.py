"""Web Push notification service for Sernia AI HITL approvals."""

import asyncio
import json
import os

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
    except Exception:
        logfire.exception("Failed to load VAPID private key")


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
