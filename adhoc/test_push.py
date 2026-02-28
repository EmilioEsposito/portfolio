"""Send a test push notification to all subscribed devices.

Usage:
    source .venv/bin/activate && python adhoc/test_push.py

Requires:
    - VAPID keys in .env
    - At least one subscription in web_push_subscriptions table
      (subscribe via the bell icon in /sernia-chat first)
"""

import asyncio
import json

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=False)

from api.src.sernia_ai.push.service import _vapid, VAPID_CLAIMS_EMAIL
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.push.models import WebPushSubscription
from pywebpush import webpush, WebPushException
from sqlalchemy import select


async def main():
    if not _vapid:
        print("ERROR: VAPID_PRIVATE_KEY not set in .env or failed to load")
        return

    # Check subscriptions
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(WebPushSubscription))
        subs = result.scalars().all()

    if not subs:
        print("No subscriptions found in web_push_subscriptions table.")
        print("Go to /sernia-chat and click the bell icon to subscribe first.")
        return

    print(f"Found {len(subs)} subscription(s):")
    for sub in subs:
        print(f"  - user={sub.clerk_user_id}")
        print(f"    endpoint={sub.endpoint}")
        print(f"    p256dh={sub.p256dh[:20]}...")
        print(f"    auth={sub.auth}")
        print(f"    created={sub.created_at}")

    payload = json.dumps({
        "title": "Test from Sernia AI",
        "body": "Push notifications are working!",
        "data": {"url": "/sernia-chat", "conversation_id": "test"},
    })

    for sub in subs:
        print(f"\nSending to {sub.endpoint[:60]}...")
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            resp = webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=_vapid,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
                ttl=86400,
            )
            print(f"  Status: {resp.status_code}")
            print(f"  Headers: {dict(resp.headers)}")
            print(f"  Body: {resp.text[:200]}")
        except WebPushException as e:
            print(f"  WebPushException: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"  Response status: {e.response.status_code}")
                print(f"  Response body: {e.response.text[:200]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
