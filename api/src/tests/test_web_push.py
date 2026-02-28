"""
Smoke + live tests for the Web Push notification module.

Smoke: Verifies model, service, and routes import cleanly and are wired correctly.
Live:  Sends a real push notification to all subscribed devices (requires VAPID keys + DB).
"""

import pytest


class TestSmoke:
    """Verify web push components import and are wired correctly."""

    def test_model_imports_and_table_name(self):
        from api.src.sernia_ai.push.models import WebPushSubscription

        assert WebPushSubscription.__tablename__ == "web_push_subscriptions"

    def test_service_functions_import(self):
        from api.src.sernia_ai.push.service import (
            save_subscription,
            remove_subscription,
            notify_all_sernia_users,
            notify_pending_approval,
        )

        assert callable(save_subscription)
        assert callable(remove_subscription)
        assert callable(notify_all_sernia_users)
        assert callable(notify_pending_approval)

    def test_routes_have_expected_paths(self):
        from api.src.sernia_ai.push.routes import router

        paths = {route.path for route in router.routes}
        assert "/push/vapid-public-key" in paths
        assert "/push/subscribe" in paths
        assert "/push/unsubscribe" in paths

    def test_push_router_mounted_on_sernia(self):
        """Push router should be included in the sernia-ai router."""
        from api.src.sernia_ai.routes import router as sernia_router

        paths = set()
        for route in sernia_router.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
        assert "/sernia-ai/push/vapid-public-key" in paths
        assert "/sernia-ai/push/subscribe" in paths
        assert "/sernia-ai/push/unsubscribe" in paths

    def test_test_endpoint_exists(self):
        from api.src.sernia_ai.push.routes import router

        paths = {route.path for route in router.routes}
        assert "/push/test" in paths


@pytest.mark.live
@pytest.mark.asyncio
async def test_send_push_notification():
    """Send a real test push to all subscribed devices.

    Run with: pytest -m live api/src/tests/test_web_push.py::test_send_push_notification -v -s
    Requires: VAPID keys in .env, at least one subscription in DB.
    """
    from api.src.sernia_ai.push.service import notify_all_sernia_users, _vapid
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.push.models import WebPushSubscription
    from sqlalchemy import select, func

    assert _vapid, "VAPID_PRIVATE_KEY not set or failed to load — add to .env"

    async with AsyncSessionFactory() as session:
        count = await session.scalar(
            select(func.count()).select_from(WebPushSubscription)
        )

    assert count and count > 0, (
        "No subscriptions in DB. Go to /sernia-chat and click the bell icon first."
    )

    print(f"\nSending test push to {count} subscription(s)...")

    await notify_all_sernia_users(
        title="Live Test from pytest",
        body="If you see this, push notifications work!",
        data={"url": "/sernia-chat", "conversation_id": "pytest-test"},
    )

    print("Sent! Check your device for the notification.")
