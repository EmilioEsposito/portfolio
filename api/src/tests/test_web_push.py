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
        from api.src.agent_conversations.push.models import WebPushSubscription

        assert WebPushSubscription.__tablename__ == "web_push_subscriptions"

    def test_service_functions_import(self):
        from api.src.agent_conversations.push.service import (
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
        from api.src.agent_conversations.push.routes import router

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
        from api.src.agent_conversations.push.routes import router

        paths = {route.path for route in router.routes}
        assert "/push/test" in paths

    def test_devices_endpoint_is_mounted_behind_the_sernia_gate(self):
        from api.src.sernia_ai.routes import router as sernia_router

        paths = {route.path for route in sernia_router.routes if hasattr(route, "path")}
        assert "/sernia-ai/push/devices" in paths


_APPLE = "https://web.push.apple.com/QGuQyavXutnMH"
_FCM = "https://fcm.googleapis.com/fcm/send/abc"
# A Home Screen web app leaves "Version/… Safari/…" out of its user agent.
_IPHONE_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
_IPAD_MOBILE_APP = "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
_IPAD_AS_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
)
_MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15"
_ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"


@pytest.mark.parametrize(
    ("endpoint", "user_agent", "ios_app"),
    [
        (_APPLE, _IPHONE_APP, True),
        (_APPLE, _IPAD_MOBILE_APP, True),
        # A desktop browser posing as an iPhone pushes through its own vendor.
        (_FCM, _IPHONE_APP, False),
        # An iPad with a Mac user agent looks like Safari on a Mac: not counted.
        (_APPLE, _IPAD_AS_MAC, False),
        (_APPLE, _MAC_SAFARI, False),
        (_FCM, _ANDROID, False),
        (_APPLE, None, False),
        (_APPLE, "", False),
        ("https://web.push.apple.com.example.test/x", _IPHONE_APP, False),
        ("not a url", _IPHONE_APP, False),
        ("https://[bad/x", _IPHONE_APP, False),
    ],
)
def test_is_ios_app_subscription(endpoint, user_agent, ios_app):
    from api.src.agent_conversations.push.service import is_ios_app_subscription

    assert is_ios_app_subscription(endpoint, user_agent) is ios_app


@pytest.mark.asyncio
async def test_ios_app_is_decided_from_the_callers_own_subscriptions():
    from api.src.agent_conversations.push import service

    def session_with(rows):
        query_result = MagicMock()
        query_result.all.return_value = rows
        session = AsyncMock()
        session.execute.return_value = query_result
        return session

    session = session_with([(_FCM, _ANDROID), (_APPLE, _IPHONE_APP)])
    assert await service.has_ios_app_subscription("user-456", session=session) is True
    statement = session.execute.await_args.args[0]
    # Only the caller's rows are read.
    assert statement.compile().params == {"clerk_user_id_1": "user-456"}

    assert (
        await service.has_ios_app_subscription("user-456", session=session_with([(_FCM, _ANDROID)]))
        is False
    )
    assert await service.has_ios_app_subscription("user-456", session=session_with([])) is False


@pytest.mark.asyncio
async def test_devices_route_answers_for_the_caller(monkeypatch):
    from api.src.agent_conversations.push import routes

    has_ios_app = AsyncMock(return_value=True)
    monkeypatch.setattr(routes, "has_ios_app_subscription", has_ios_app)
    user = MagicMock()
    user.id = "user-456"
    session = object()

    assert await routes.get_devices(user=user, session=session) == {"ios_app": True}
    has_ios_app.assert_awaited_once_with(clerk_user_id="user-456", session=session)


@pytest.mark.asyncio
async def test_test_notification_goes_only_to_the_person_who_asked(monkeypatch):
    from api.src.agent_conversations.push import routes

    notify_user = AsyncMock()
    monkeypatch.setattr(routes, "notify_user_push", notify_user)
    # The team-wide send is not reachable from the route at all.
    assert not hasattr(routes, "notify_all_sernia_users")

    user = MagicMock()
    user.id = "user-456"
    assert await routes.send_test_push(user=user) == {"status": "sent"}

    notify_user.assert_awaited_once()
    sent = notify_user.await_args.kwargs
    assert sent["clerk_user_id"] == "user-456"
    assert sent["data"] == {"url": "/team/chat"}
    assert "Test notification" in sent["body"]


@pytest.mark.asyncio
async def test_chat_response_notification_has_preview_and_is_user_targeted(monkeypatch):
    from api.src.agent_conversations.push import service

    notify_user = AsyncMock()
    monkeypatch.setattr(service, "notify_user_push", notify_user)

    await service.notify_chat_response(
        conversation_id="conversation-123",
        clerk_user_id="user-456",
        response_text="  First line.\n\nSecond line.  ",
    )

    notify_user.assert_awaited_once_with(
        clerk_user_id="user-456",
        title="Sernia AI",
        body="First line. Second line.",
        data={
            "url": "/team/chat?id=conversation-123",
            "conversation_id": "conversation-123",
            "type": "response",
        },
    )


@pytest.mark.asyncio
async def test_chat_response_preview_falls_back_and_is_bounded(monkeypatch):
    from api.src.agent_conversations.push import service

    notify_user = AsyncMock()
    monkeypatch.setattr(service, "notify_user_push", notify_user)

    await service.notify_chat_response(
        conversation_id="conversation-123",
        clerk_user_id="user-456",
        response_text=" \n ",
    )
    assert notify_user.await_args.kwargs["body"] == "Your response is ready."

    notify_user.reset_mock()
    await service.notify_chat_response(
        conversation_id="conversation-123",
        clerk_user_id="user-456",
        response_text="x" * 240,
    )
    preview = notify_user.await_args.kwargs["body"]
    assert len(preview) == 180
    assert preview.endswith("…")


@pytest.mark.asyncio
async def test_targeted_push_does_not_broadcast_without_user_subscription(monkeypatch):
    from api.src.agent_conversations.push import service

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


def _push_exc(status: int, body: str):
    from pywebpush import WebPushException

    response = MagicMock()
    response.status_code = status
    response.text = body
    return WebPushException(f"Push failed: {status}", response=response)


@pytest.mark.parametrize(
    ("status", "body", "stale"),
    [
        (410, "", True),
        (404, "", True),
        # Apple after a VAPID key rotation (seen in production 2026-10-31).
        (400, '{"reason":"VapidPkHashMismatch"}', True),
        # FCM's equivalent rejection.
        (
            403,
            "the VAPID credentials in the authorization header do not correspond "
            "to the credentials used to create the subscriptions.",
            True,
        ),
        (400, '{"reason":"BadJwtToken"}', False),
        (413, "payload too large", False),
        (500, "VapidPkHashMismatch", False),
    ],
)
def test_stale_subscription_reason(status, body, stale):
    from api.src.agent_conversations.push.service import _stale_subscription_reason

    assert bool(_stale_subscription_reason(_push_exc(status, body))) is stale


@pytest.mark.asyncio
async def test_vapid_mismatch_subscriptions_are_pruned(monkeypatch):
    from api.src.agent_conversations.push import service

    stale = MagicMock(endpoint="https://web.push.apple.com/stale", p256dh="k", auth="a")
    live = MagicMock(endpoint="https://web.push.apple.com/live", p256dh="k", auth="a")
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = [stale, live]
    session = AsyncMock()
    session.execute.return_value = query_result

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    def fake_send(subscription_info, payload):
        if subscription_info["endpoint"] == stale.endpoint:
            raise _push_exc(400, '{"reason":"VapidPkHashMismatch"}')
        return MagicMock(status_code=201)

    monkeypatch.setattr(service, "_get_vapid", lambda: object())
    monkeypatch.setattr(service, "AsyncSessionFactory", FakeSessionContext)
    monkeypatch.setattr(service, "_send_push", fake_send)

    await service.notify_user_push(clerk_user_id="user-1", title="Sernia AI", body="hi")

    # select + delete of the stale endpoint only
    assert session.execute.await_count == 2
    delete_stmt = session.execute.await_args_list[1].args[0]
    assert delete_stmt.compile().params == {"endpoint_1": [stale.endpoint]}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_agent_run_result_reports_commit_status(monkeypatch):
    from api.src.agent_conversations import models

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
    from api.src.agent_conversations import models
    from api.src.sernia_ai import runs
    from api.src.sernia_ai.chat_runs import ChatRun

    save_started = asyncio.Event()
    allow_save_to_finish = asyncio.Event()

    class FakeSessionContext:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *args):
            return False

    result = MagicMock()
    result.output = "Completed response"
    result.all_messages.return_value = []

    async def delayed_successful_save(**kwargs):
        save_started.set()
        await allow_save_to_finish.wait()

    scheduled_tasks = []
    response_notification = {}

    def capture_response_notification(**kwargs):
        response_notification.update(kwargs)

        async def completed():
            return None

        return completed()

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()

    monkeypatch.setattr(models, "provide_session", FakeSessionContext)
    monkeypatch.setattr(models, "save_agent_conversation", delayed_successful_save)
    monkeypatch.setattr(runs, "extract_pending_approvals", MagicMock(return_value=[]))
    monkeypatch.setattr(runs, "notify_chat_response", capture_response_notification)
    monkeypatch.setattr(runs, "create_logged_task", capture_task)
    deps = MagicMock(
        conversation_id="conversation-123", user_identifier="user-456", sms_reply_recipients=[]
    )
    execution = ChatRun(MagicMock(), [], deps, {"id": "run-123"})
    completion = asyncio.create_task(execution.complete(result))
    await asyncio.wait_for(save_started.wait(), timeout=5)
    assert "notify_chat_response" not in scheduled_tasks
    completion.cancel()
    allow_save_to_finish.set()
    await completion

    assert execution.finished
    assert scheduled_tasks == ["git_sync", "notify_chat_response"]
    assert response_notification == {
        "conversation_id": "conversation-123",
        "clerk_user_id": "user-456",
        "response_text": "Completed response",
    }


@pytest.mark.asyncio
async def test_chat_run_skips_push_when_persistence_fails(monkeypatch):
    from api.src.sernia_ai import runs
    from api.src.sernia_ai.chat_runs import ChatRun

    result = MagicMock()
    result.output = "Completed response"
    scheduled_tasks = []

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()

    monkeypatch.setattr(runs, "persist_agent_run_result", AsyncMock(return_value=False))
    monkeypatch.setattr(runs, "create_logged_task", capture_task)
    deps = MagicMock(
        conversation_id="conversation-123", user_identifier="user-456", sms_reply_recipients=[]
    )
    execution = ChatRun(MagicMock(), [], deps, {"id": "run-123"})
    with pytest.raises(RuntimeError, match="could not be saved"):
        await execution.complete(result)
    assert not execution.finished
    assert scheduled_tasks == ["git_sync"]


@pytest.mark.asyncio
async def test_approval_route_skips_push_when_persistence_fails(monkeypatch):
    from api.src.agent_conversations import routes, schemas
    from api.src.sernia_ai import conversation_handler, runs

    body = schemas.ApprovalRequest(
        decisions=[
            schemas.ApprovalDecisionRequest(
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

    monkeypatch.setattr(conversation_handler, "capture_run_messages", FakeCaptureContext)
    monkeypatch.setattr(
        conversation_handler, "resume_with_approvals", AsyncMock(return_value=result)
    )
    monkeypatch.setattr(runs, "persist_agent_run_result", AsyncMock(return_value=False))
    monkeypatch.setattr(
        conversation_handler, "resolve_active_run_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(routes, "get_agent_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runs,
        "extract_pending_approvals",
        MagicMock(return_value=[{"tool_name": "send_email", "args": {}}]),
    )
    monkeypatch.setattr(conversation_handler, "extract_tool_results", MagicMock(return_value={}))
    monkeypatch.setattr(runs, "create_logged_task", capture_task)

    response = await routes.approve_conversation(
        conversation_id="conversation-123",
        body=body,
        user=user,
        session=MagicMock(),
    )

    assert response["status"] == "pending_approval"
    assert scheduled_tasks == ["git_sync"]


@pytest.mark.asyncio
async def test_approval_route_schedules_response_preview_after_persistence(monkeypatch):
    from api.src.agent_conversations import routes, schemas
    from api.src.sernia_ai import conversation_handler, runs

    body = schemas.ApprovalRequest(
        decisions=[
            schemas.ApprovalDecisionRequest(
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
    response_notification: dict[str, str] = {}

    class FakeCaptureContext:
        def __enter__(self):
            return []

        def __exit__(self, exc_type, exc, traceback):
            return False

    def capture_task(coro, *, name=None):
        scheduled_tasks.append(name)
        coro.close()
        return MagicMock()

    def capture_response_notification(**kwargs):
        response_notification.update(kwargs)

        async def completed():
            return None

        return completed()

    monkeypatch.setattr(conversation_handler, "capture_run_messages", FakeCaptureContext)
    monkeypatch.setattr(
        conversation_handler, "resume_with_approvals", AsyncMock(return_value=result)
    )
    monkeypatch.setattr(runs, "persist_agent_run_result", AsyncMock(return_value=True))
    monkeypatch.setattr(
        conversation_handler, "resolve_active_run_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(routes, "get_agent_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(runs, "extract_pending_approvals", MagicMock(return_value=[]))
    monkeypatch.setattr(conversation_handler, "extract_tool_results", MagicMock(return_value={}))
    monkeypatch.setattr(runs, "notify_chat_response", capture_response_notification)
    monkeypatch.setattr(runs, "create_logged_task", capture_task)

    response = await routes.approve_conversation(
        conversation_id="conversation-123",
        body=body,
        user=user,
        session=MagicMock(),
    )

    assert response["status"] == "completed"
    assert scheduled_tasks == ["git_sync", "notify_chat_response"]
    assert response_notification == {
        "conversation_id": "conversation-123",
        "clerk_user_id": "user-456",
        "response_text": "Approval follow-up",
    }


@pytest.mark.live
@pytest.mark.asyncio
async def test_send_push_notification():
    """Send a real test push to all subscribed devices.

    Run with: pytest -m live api/src/tests/test_web_push.py::test_send_push_notification -v -s
    Requires: VAPID keys in .env, at least one subscription in DB.
    """
    from sqlalchemy import func, select

    from api.src.agent_conversations.push.models import WebPushSubscription
    from api.src.agent_conversations.push.service import _get_vapid, notify_all_sernia_users
    from api.src.database.database import AsyncSessionFactory

    assert _get_vapid(), "VAPID_PRIVATE_KEY not set or failed to load — add to .env"

    async with AsyncSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(WebPushSubscription))

    assert count and count > 0, (
        "No subscriptions in DB. Go to /team/chat and click the bell icon first."
    )

    print(f"\nSending test push to {count} subscription(s)...")

    await notify_all_sernia_users(
        title="Live Test from pytest",
        body="If you see this, push notifications work!",
        data={"url": "/team/chat", "conversation_id": "pytest-test"},
    )

    print("Sent! Check your device for the notification.")


# ── Conversation presence: no push about a conversation its reader has open ──


@pytest.fixture
def presence():
    from api.src.agent_conversations.push import presence

    presence.clear()
    yield presence
    presence.clear()


def test_presence_is_per_page_and_expires(presence, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(presence.time, "monotonic", lambda: now[0])

    presence.report("user-1", "phone", "conversation-123", seq=1)
    presence.report("user-1", "laptop", "conversation-123", seq=1)
    presence.report("user-2", "laptop", "conversation-999", seq=1)
    assert presence.readers("conversation-123") == {"user-1"}
    assert presence.readers(None) == set()

    # One page leaving does not cancel the other page that still has it open.
    presence.report("user-1", "laptop", None, seq=2)
    assert presence.readers("conversation-123") == {"user-1"}
    presence.report("user-1", "phone", "conversation-999", seq=2)
    assert presence.readers("conversation-123") == set()
    assert presence.readers("conversation-999") == {"user-1", "user-2"}

    # A page that went away without saying so stops counting.
    now[0] += presence.PRESENCE_TTL_SECONDS
    assert presence.readers("conversation-999") == set()


def test_presence_ignores_a_report_that_arrives_late(presence):
    presence.report("user-1", "phone", "conversation-123", seq=1)
    presence.report("user-1", "phone", None, seq=3)
    # The page's earlier "open" was handled after its "left".
    presence.report("user-1", "phone", "conversation-123", seq=2)
    assert presence.readers("conversation-123") == set()

    presence.report("user-1", "phone", "conversation-123", seq=4)
    assert presence.readers("conversation-123") == {"user-1"}


@pytest.mark.asyncio
async def test_presence_route_records_the_signed_in_users_page(presence):
    from api.src.agent_conversations.push import routes

    user = MagicMock()
    user.id = "user-456"
    body = routes.PresenceReport(client_id="page-1", conversation_id="conversation-123", seq=1)
    response = await routes.report_presence(body=body, user=user)
    assert response.status_code == 204
    assert presence.readers("conversation-123") == {"user-456"}

    away = routes.PresenceReport(client_id="page-1", seq=2)
    await routes.report_presence(body=away, user=user)
    assert presence.readers("conversation-123") == set()


def _fake_session(subs):
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = subs
    session = AsyncMock()
    session.execute.return_value = query_result

    class FakeSessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    return session, FakeSessionContext


@pytest.mark.asyncio
async def test_targeted_push_skips_a_user_with_the_conversation_open(presence, monkeypatch):
    from api.src.agent_conversations.push import service

    sub = MagicMock(endpoint="https://web.push.apple.com/a", p256dh="k", auth="a")
    session, factory = _fake_session([sub])
    sent = []
    monkeypatch.setattr(service, "_get_vapid", lambda: object())
    monkeypatch.setattr(service, "AsyncSessionFactory", factory)
    monkeypatch.setattr(service, "_send_push", lambda info, payload: sent.append(info))

    presence.report("user-1", "phone", "conversation-123", seq=1)
    await service.notify_chat_response(
        conversation_id="conversation-123", clerk_user_id="user-1", response_text="Done."
    )
    assert sent == []
    session.execute.assert_not_awaited()

    # Another conversation, or the same one after the page leaves, is pushed.
    await service.notify_chat_response(
        conversation_id="conversation-999", clerk_user_id="user-1", response_text="Done."
    )
    presence.report("user-1", "phone", None, seq=2)
    await service.notify_chat_response(
        conversation_id="conversation-123", clerk_user_id="user-1", response_text="Done."
    )
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_team_push_skips_only_people_with_the_conversation_open(presence, monkeypatch):
    from api.src.agent_conversations.push import service

    reader = MagicMock(clerk_user_id="user-1", endpoint="https://e/1", p256dh="k", auth="a")
    other = MagicMock(clerk_user_id="user-2", endpoint="https://e/2", p256dh="k", auth="a")
    _, factory = _fake_session([reader, other])
    sent = []
    monkeypatch.setattr(service, "_get_vapid", lambda: object())
    monkeypatch.setattr(service, "AsyncSessionFactory", factory)
    monkeypatch.setattr(service, "_send_push", lambda info, payload: sent.append(info["endpoint"]))

    presence.report("user-1", "laptop", "conversation-123", seq=1)
    await service.notify_pending_approval(
        conversation_id="conversation-123", tool_name="send_sms", tool_args={}
    )
    assert sent == ["https://e/2"]

    # A push about no conversation (a showing alert) reaches everyone.
    sent.clear()
    await service.notify_all_sernia_users(title="Showing booked", body="Tomorrow 10am")
    assert sent == ["https://e/1", "https://e/2"]
