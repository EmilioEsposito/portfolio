"""Push notification endpoints for Sernia AI.

Mounted as a sub-router under /sernia-ai/push.
Inherits the _sernia_gate auth dependency from the parent router.
"""

import os

from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.src.agent_conversations.push import presence
from api.src.agent_conversations.push.inventory import MemberRegistrations, team_registrations
from api.src.agent_conversations.push.service import (
    has_ios_app_subscription,
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


class PresenceReport(BaseModel):
    # A random ID the page picks when it loads, so that one page leaving does
    # not cancel another page that still has the conversation open.
    client_id: str = Field(min_length=1, max_length=64)
    # The conversation the page shows in front of the person; None when it is
    # hidden, out of focus, or closed.
    conversation_id: str | None = Field(default=None, max_length=200)
    # Counts up with each report from the page; an older report that arrives
    # late is ignored.
    seq: int = Field(ge=0)


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


@router.post("/presence", status_code=204)
async def report_presence(
    body: PresenceReport,
    user: User = Depends(_get_sernia_user),
) -> Response:
    """The conversation this page has in front of its reader, if any.

    Pushes about that conversation are not sent to them while it is (see
    ``presence``). Only the signed-in user's own pages are affected.
    """
    presence.report(user.id, body.client_id, body.conversation_id, body.seq)
    return Response(status_code=204)


@router.get("/devices")
async def get_devices(
    user: User = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """What the caller's own saved subscriptions say about their devices.

    ``ios_app``: the caller has the Home Screen app of an iPhone or iPad
    subscribed at some point. This account-level record does not establish
    whether the app is still installed on a particular device.
    """
    return {"ios_app": await has_ios_app_subscription(clerk_user_id=user.id, session=session)}


@router.get("/admin/registrations")
async def get_team_registrations(
    response: Response,
    user: User = Depends(_get_sernia_user),
    session: DBSession = None,
) -> list[MemberRegistrations]:
    """Only the verified team administrator can inspect other users' registrations."""
    if not any(
        email.id == user.primary_email_address_id
        and email.email_address.lower() == "emilio@serniacapital.com"
        and email.verification
        and email.verification.status == "verified"
        for email in user.email_addresses
    ):
        raise HTTPException(status_code=403, detail="Administrator access required")
    response.headers["Cache-Control"] = "private, no-store"
    return await team_registrations(session)


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
