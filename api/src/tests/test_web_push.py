"""
Smoke + live tests for the Web Push notification module.

Smoke: Verifies model, service, and routes import cleanly and are wired correctly.
Live:  Sends a real push notification to all subscribed devices (requires VAPID keys + DB).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSmoke:
    """Verify web push components import and are wired correctly."""

    def test_model_imports_and_table_name(self):
        from api.src.sernia_ai.push.models import WebPushSubscription

        assert WebPushSubscription.__tablename__ == "web_push_subscriptions"

    def test_service_functions_import(self):
        from api.src.sernia_ai.push.service import (
            notify_all_sernia_users,
            notify_chat_response,
            notify_pending_approval,
            remove_subscription,
            save_subscription,
        )

        assert callable(save_subscription)
        assert callable(remove_subscription)
        assert callable(notify_all_sernia_users)
        assert callable(notify_chat_response)
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


@pytest.mark.asyncio
async def test_chat_response_notification_is_private_and_user_targeted(monkeypatch):
    from api.src.sernia_ai.push import service

    notify_user = AsyncMock()
    monkeypatch.setattr(service, "notify_user_push", notify_user)

    await service.notify_chat_response(
        conversation_id="conversation-123",
        clerk_user_id="user-456",
    )

    notify_user.assert_awaited_once_with(
        clerk_user_id="user-456",
        title="Sernia AI",
        body="Your response is ready.",
        data={
            "url": "/sernia-chat?id=conversation-123",
            "conversation_id": "conversation-123",
            "type": "response",
        },
    )


@pytest.mark.asyncio
async def test_targeted_push_does_not_broadcast_without_user_subscription(monkeypatch):
    from api.src.sernia_ai.push import service

    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = query_result

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    notify_all = AsyncMock()
    monkeypatch.setattr(service, "_get_vapid", lambda: object())
    monkeypatch.setattr(service, "AsyncSessionFactory", FakeSessionContext)
    monkeypatch.setattr(service, "notify_all_sernia_users", notify_all)

    await service.notify_user_push(
        clerk_user_id="user-without-subscription",
        title="Sernia AI",
        body="Your response is ready.",
        data={"type": "response"},
    )

    session.execute.assert_awaited_once()
    notify_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_agent_run_result_reports_commit_status(monkeypatch):
    from api.src.ai_demos import models

    session = MagicMock()

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    result = MagicMock()
    result.all_messages.return_value = []
    result.usage.return_value = MagicMock(
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )
    save_conversation = AsyncMock()
    monkeypatch.setattr(models, "provide_session", FakeSessionContext)
    monkeypatch.setattr(models, "save_agent_conversation", save_conversation)

    persisted = await models.persist_agent_run_result(
        result=result,
        conversation_id="conversation-123",
        agent_name="test-agent",
        clerk_user_id="user-456",
    )

    assert persisted is True
    save_conversation.assert_awaited_once()

    save_conversation.reset_mock(side_effect=True)
    save_conversation.side_effect = RuntimeError("database unavailable")

    persisted = await models.persist_agent_run_result(
        result=result,
        conversation_id="conversation-123",
        agent_name="test-agent",
        clerk_user_id="user-456",
    )

    assert persisted is False


@pytest.mark.asyncio
async def test_cancelled_chat_waits_for_commit_then_schedules_response_push(monkeypatch):
    from api.src.ai_demos import models
    from api.src.sernia_ai import routes

    save_started = asyncio.Event()
    allow_save_to_finish = asyncio.Event()
    session = MagicMock()

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    result = MagicMock()
    result.output = "Completed response"
    result.all_messages.return_value = []
    result.usage.return_value = MagicMock(
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )

    async def delayed_successful_save(**kwargs):
        save_started.set()
        await allow_save_to_finish.wait()

    monkeypatch.setattr(models, "provide_session", FakeSessionContext)
    monkeypatch.setattr(models, "save_agent_conversation", delayed_successful_save)

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "id": "conversation-123",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hi"}]}],
        }
    )
    user = MagicMock()
    user.id = "user-456"
    user.first_name = "Test"
    user.last_name = "User"
    user.email_addresses = []
    scheduled_tasks: list[str | None] = []

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()
        return MagicMock()

    async def dispatch_after_persistence(_request, **kwargs):
        await kwargs["on_complete"](result)
        return routes.Response(content="")

    monkeypatch.setattr(routes, "get_conversation_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "extract_pending_approvals", MagicMock(return_value=[]))
    monkeypatch.setattr(routes, "create_logged_task", capture_task)
    monkeypatch.setattr(routes.VercelAIAdapter, "dispatch_request", dispatch_after_persistence)

    chat_request = asyncio.create_task(
        routes.chat_sernia(
            request=request,
            user=user,
            session=MagicMock(),
        )
    )
    await save_started.wait()

    chat_request.cancel()
    allow_save_to_finish.set()

    response = await chat_request

    assert response.status_code == 200
    assert scheduled_tasks == ["git_sync", "notify_chat_response"]


@pytest.mark.asyncio
async def test_chat_route_skips_push_when_persistence_fails(monkeypatch):
    from api.src.sernia_ai import routes

    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "id": "conversation-123",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hi"}]}],
        }
    )
    user = MagicMock()
    user.id = "user-456"
    user.first_name = "Test"
    user.last_name = "User"
    user.email_addresses = []

    result = MagicMock()
    result.output = "Completed response"
    scheduled_tasks: list[str | None] = []

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()
        return MagicMock()

    async def dispatch_with_failed_persistence(_request, **kwargs):
        await kwargs["on_complete"](result)
        return routes.Response(content="")

    monkeypatch.setattr(routes, "get_conversation_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "persist_agent_run_result", AsyncMock(return_value=False))
    monkeypatch.setattr(routes, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "create_logged_task", capture_task)
    monkeypatch.setattr(
        routes.VercelAIAdapter, "dispatch_request", dispatch_with_failed_persistence
    )

    response = await routes.chat_sernia(request=request, user=user, session=MagicMock())

    assert response.status_code == 200
    assert scheduled_tasks == ["git_sync"]


@pytest.mark.asyncio
async def test_approval_route_skips_push_when_persistence_fails(monkeypatch):
    from api.src.sernia_ai import routes

    body = routes.ApprovalRequest(
        decisions=[
            routes.ApprovalDecisionRequest(
                tool_call_id="tool-123",
                approved=True,
            )
        ]
    )
    user = MagicMock()
    user.id = "user-456"
    user.first_name = "Test"
    user.last_name = "User"
    user.email_addresses = []

    result = MagicMock()
    result.output = "Approval follow-up"
    scheduled_tasks: list[str | None] = []

    class FakeCaptureContext:
        def __enter__(self):
            return []

        def __exit__(self, exc_type, exc, traceback):
            return False

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()
        return MagicMock()

    monkeypatch.setattr(routes, "capture_run_messages", FakeCaptureContext)
    monkeypatch.setattr(routes, "resume_with_approvals", AsyncMock(return_value=result))
    monkeypatch.setattr(routes, "persist_agent_run_result", AsyncMock(return_value=False))
    monkeypatch.setattr(routes, "resolve_active_run_kwargs", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "get_agent_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(
        routes,
        "extract_pending_approvals",
        MagicMock(return_value=[{"tool_name": "send_email", "args": {}}]),
    )
    monkeypatch.setattr(routes, "extract_tool_results", MagicMock(return_value={}))
    monkeypatch.setattr(routes, "create_logged_task", capture_task)

    response = await routes.approve_conversation(
        conversation_id="conversation-123",
        body=body,
        user=user,
        session=MagicMock(),
    )

    assert response["status"] == "pending_approval"
    assert scheduled_tasks == ["git_sync"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_send_push_notification():
    """Send a real test push to all subscribed devices.

    Run with: pytest -m live api/src/tests/test_web_push.py::test_send_push_notification -v -s
    Requires: VAPID keys in .env, at least one subscription in DB.
    """
    from sqlalchemy import func, select

    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.push.models import WebPushSubscription
    from api.src.sernia_ai.push.service import _get_vapid, notify_all_sernia_users

    assert _get_vapid(), "VAPID_PRIVATE_KEY not set or failed to load — add to .env"

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(WebPushSubscription))

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
