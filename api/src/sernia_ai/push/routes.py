"""Push notification endpoints for Sernia AI.

Mounted as a sub-router under /sernia-ai/push.
Inherits the _sernia_gate auth dependency from the parent router.
"""

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from clerk_backend_api import User

from api.src.sernia_ai.push.service import (
    save_subscription,
    remove_subscription,
    notify_all_sernia_users,
)
from api.src.database.database import DBSession

router = APIRouter(prefix="/push", tags=["sernia-ai-push"])

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")


async def _get_sernia_user(request: Request) -> User:
    """Retrieve user set by the parent router's _sernia_gate dependency."""
    return request.state.sernia_user


# ── Schemas ──────────────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class UnsubscribeRequest(BaseModel):
    endpoint: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key for the frontend to use in PushManager.subscribe()."""
    return {"publicKey": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    user: User = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Save a browser push subscription."""
    await save_subscription(
        clerk_user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=request.headers.get("user-agent"),
        session=session,
    )
    return {"status": "subscribed"}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    user: User = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Remove a browser push subscription."""
    deleted = await remove_subscription(
        endpoint=body.endpoint,
        clerk_user_id=user.id,
        session=session,
    )
    return {"status": "unsubscribed" if deleted else "not_found"}


@router.post("/test")
async def test_push(
    user: User = Depends(_get_sernia_user),
):
    """Send a test push notification to all subscribed devices."""
    await notify_all_sernia_users(
        title="Test from Sernia AI",
        body=f"Push notifications are working! Triggered by {user.first_name or 'unknown'}.",
        data={"url": "/sernia-chat", "conversation_id": "test"},
    )
    return {"status": "sent"}
