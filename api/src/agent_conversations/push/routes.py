"""Push notification endpoints for Sernia AI.

Mounted as a sub-router under /sernia-ai/push.
Inherits the _sernia_gate auth dependency from the parent router.
"""

import os

from clerk_backend_api import User
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.src.agent_conversations.push.service import (
    notify_user_push,
    remove_subscription,
    save_subscription,
)
from api.src.database.database import DBSession
from api.src.utils import team_links

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
async def send_test_push(
    user: User = Depends(_get_sernia_user),
):
    """Send a test notification to the signed-in user's own devices.

    The team pages offer this next to "Turn on notifications", so that a person
    can see their device receive one. Nobody else is notified.
    """
    await notify_user_push(
        clerk_user_id=user.id,
        title="Sernia Capital",
        body="Test notification: this device will be told when a conversation needs you.",
        data={"url": team_links.CHAT_PATH},
    )
    return {"status": "sent"}
