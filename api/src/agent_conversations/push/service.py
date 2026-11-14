"""Web Push for shared Sernia conversations and approvals."""

import asyncio
import json
import os

import logfire
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.push.models import WebPushSubscription
from api.src.database.database import AsyncSessionFactory, provide_session

_RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@serniacapital.com")

# Lazy-loaded Vapid object.  Reading the env var at module import time is
# unreliable on Railway (multi-worker startup race), so we defer to first use
# and retry on subsequent calls if the initial attempt failed.
_vapid: Vapid | None = None
_vapid_loaded: bool = False

_RESPONSE_PREVIEW_MAX_CHARS = 180
_RESPONSE_READY_FALLBACK = "Your response is ready."


def _get_vapid() -> Vapid | None:
    """Return the cached Vapid object, loading from env on first call.

    Retries each call if the previous attempt failed — covers the case where
    the env var wasn't readable at module import but is available later.
    """
    global _vapid, _vapid_loaded
    if _vapid is not None:
        return _vapid
    if _vapid_loaded:
        # Already tried and failed — but retry in case the env var appeared
        pass

    raw = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not raw:
        if not _vapid_loaded:
            logfire.warn("VAPID_PRIVATE_KEY not set — push notifications disabled")
        _vapid_loaded = True
        return None

    try:
        pem = raw.replace("\\n", "\n").strip()
        if pem.startswith("-----"):
            _vapid = Vapid.from_pem(pem.encode())
        else:
            _vapid = Vapid.from_string(pem)
        _vapid_loaded = True
        logfire.info("VAPID private key loaded successfully")
        return _vapid
    except Exception:
        logfire.exception(
            "Failed to load VAPID private key (got {length} chars). Will retry next call.",
            length=len(raw),
        )
        _vapid_loaded = True
        return None


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
        stmt = (
            pg_insert(WebPushSubscription)
            .values(
                clerk_user_id=clerk_user_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
            )
            .on_conflict_do_update(
                index_elements=["endpoint"],
                set_={
                    "clerk_user_id": clerk_user_id,
                    "p256dh": p256dh,
                    "auth": auth,
                    "user_agent": user_agent,
                },
            )
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


# Push-service responses that mean a stored subscription can never succeed
# again with the current VAPID identity. After a VAPID key rotation, browsers
# that have not revisited the app still hold subscriptions bound to the old
# public key; push services reject every send to them:
#   - Apple:   400 {"reason":"VapidPkHashMismatch"}
#   - FCM:     403 "...VAPID credentials ... do not correspond to the
#              credentials used to create the subscriptions."
# Such rows are as dead as a 404/410 and are pruned the same way; the browser
# re-subscribes with the current key the next time the user opens the app.
_VAPID_MISMATCH_MARKERS = (
    "VapidPkHashMismatch",
    "do not correspond to the credentials used to create the subscription",
)


def _stale_subscription_reason(exc: WebPushException) -> str | None:
    """Return why a failed send means the subscription is permanently dead, else None."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (404, 410):
        return f"gone ({status})"
    if status is None and ("410" in str(exc) or "404" in str(exc)):
        # No response object attached — fall back to the legacy message check.
        return "gone"
    try:
        body = response.text if response is not None else ""
    except Exception:
        body = ""
    if status in (400, 401, 403) and any(m in (body or "") for m in _VAPID_MISMATCH_MARKERS):
        return f"vapid key mismatch ({status})"
    return None


def _send_push(subscription_info: dict, payload: str):
    """Synchronous push send — run via asyncio.to_thread."""
    vapid = _get_vapid()
    if not vapid:
        raise RuntimeError("VAPID key not available")
    return webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=vapid,
        vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
        ttl=86400,  # 24 hours — default 0 means "discard if not delivered immediately"
    )


async def notify_all_sernia_users(
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to all active Sernia subscriptions."""
    if not _get_vapid():
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
                stale_reason = _stale_subscription_reason(e)
                if stale_reason:
                    expired_endpoints.append(sub.endpoint)
                    logfire.info(
                        "web push subscription expired",
                        endpoint=sub.endpoint[:40],
                        reason=stale_reason,
                    )
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
    if not _get_vapid():
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
            select(WebPushSubscription).where(WebPushSubscription.clerk_user_id == clerk_user_id)
        )
        subs = result.scalars().all()

        if not subs:
            logfire.info(
                "web push: no subscriptions for targeted user — skipping",
                clerk_user_id=clerk_user_id,
                title=title,
            )
            return

        logfire.info(
            "web push sending (user-targeted)",
            sub_count=len(subs),
            title=title,
            clerk_user_id=clerk_user_id,
        )

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
                stale_reason = _stale_subscription_reason(e)
                if stale_reason:
                    expired_endpoints.append(sub.endpoint)
                    logfire.info(
                        "web push subscription expired",
                        endpoint=sub.endpoint[:40],
                        reason=stale_reason,
                    )
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


async def notify_chat_response(
    conversation_id: str,
    clerk_user_id: str,
    response_text: str | None = None,
) -> None:
    """Notify only the user who requested a completed web-chat response.

    The notification includes a short, whitespace-normalized response preview.
    ``notify_user_push`` does not broadcast when the user has no subscription.
    """
    if not conversation_id or not clerk_user_id:
        logfire.warn(
            "notify_chat_response called without conversation or user — skipping",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
        )
        return

    data = {
        "url": f"/sernia-chat?id={conversation_id}",
        "conversation_id": conversation_id,
        "type": "response",
    }

    preview = " ".join((response_text or "").split())
    if not preview:
        preview = _RESPONSE_READY_FALLBACK
    elif len(preview) > _RESPONSE_PREVIEW_MAX_CHARS:
        preview = preview[: _RESPONSE_PREVIEW_MAX_CHARS - 1].rstrip() + "…"

    try:
        await notify_user_push(
            clerk_user_id=clerk_user_id,
            title="Sernia AI",
            body=preview,
            data=data,
        )
    except Exception:
        logfire.exception(
            "notify_chat_response failed",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
        )


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
