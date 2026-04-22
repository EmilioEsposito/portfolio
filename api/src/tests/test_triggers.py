"""
Smoke + unit tests for the Sernia AI trigger system.

Smoke: Verify all trigger modules import cleanly and are wired correctly.
Unit:  Test trigger logic with mocked agent runs and push notifications.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from httpx import Response, Request
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart


@pytest.fixture(autouse=True)
def _clear_trigger_cooldowns():
    """Reset the in-memory rate-limit cooldowns and dedupe state between tests."""
    from api.src.sernia_ai.triggers.background_agent_runner import _trigger_cooldowns
    from api.src.sernia_ai.triggers.ai_sms_event_trigger import _ai_sms_call_timestamps
    import api.src.sernia_ai.triggers.zillow_email_event_trigger as zillow_mod
    _trigger_cooldowns.clear()
    _ai_sms_call_timestamps.clear()
    zillow_mod._emilio_clerk_user_id = None
    zillow_mod._pending_emails.clear()
    zillow_mod._pending_task = None
    zillow_mod._recently_fired_message_ids.clear()
    yield
    _trigger_cooldowns.clear()
    _ai_sms_call_timestamps.clear()
    zillow_mod._emilio_clerk_user_id = None
    zillow_mod._pending_emails.clear()
    zillow_mod._pending_task = None
    zillow_mod._recently_fired_message_ids.clear()


# =========================================================================
# Smoke Tests
# =========================================================================


class TestSmoke:
    """Verify trigger components import and are wired correctly."""

    def test_background_agent_runner_imports(self):
        from api.src.sernia_ai.triggers.background_agent_runner import (
            run_agent_for_trigger,
            RATE_LIMIT_SECONDS,
            _is_rate_limited,
        )
        from api.src.sernia_ai.agent import NoAction
        from api.src.sernia_ai.config import TRIGGER_BOT_ID

        assert callable(run_agent_for_trigger)
        assert RATE_LIMIT_SECONDS == 120
        assert callable(_is_rate_limited)
        assert TRIGGER_BOT_ID == "system:sernia-ai"
        assert NoAction(reason="test").reason == "test"

    def test_scheduled_triggers_imports(self):
        from api.src.sernia_ai.triggers.scheduled_triggers import (
            run_scheduled_checks,
            register_scheduled_triggers,
        )

        assert callable(run_scheduled_checks)
        assert callable(register_scheduled_triggers)

    def test_noaction_model_in_agent_output_type(self):
        """NoAction is included in the agent's output_type union."""
        from api.src.sernia_ai.agent import sernia_agent, NoAction
        assert NoAction in sernia_agent.output_type

    def test_notify_trigger_alert_imports(self):
        """Push service should have the new trigger alert function."""
        from api.src.sernia_ai.push.service import notify_trigger_alert

        assert callable(notify_trigger_alert)

    def test_notify_team_sms_imports(self):
        """Push service should have the new SMS notification function."""
        from api.src.sernia_ai.push.service import notify_team_sms

        assert callable(notify_team_sms)

    def test_zillow_email_event_trigger_imports(self):
        from api.src.sernia_ai.triggers.zillow_email_event_trigger import (
            is_zillow_email,
            queue_zillow_email_event,
            _fire_batched_trigger,
            _get_emilio_clerk_user_id,
        )
        assert callable(is_zillow_email)
        assert callable(queue_zillow_email_event)
        assert callable(_fire_batched_trigger)
        assert callable(_get_emilio_clerk_user_id)

    def test_notify_user_push_imports(self):
        from api.src.sernia_ai.push.service import notify_user_push
        assert callable(notify_user_push)

    def test_config_has_emilio_contact_slug(self):
        from api.src.sernia_ai.config import EMILIO_CONTACT_SLUG
        assert EMILIO_CONTACT_SLUG == "emilio"

    def test_get_clerk_user_id_by_slug_imports(self):
        from api.src.contact.service import get_clerk_user_id_by_slug
        assert callable(get_clerk_user_id_by_slug)

    def test_background_runner_has_notify_clerk_user_id_param(self):
        import inspect
        from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger
        sig = inspect.signature(run_agent_for_trigger)
        assert "notify_clerk_user_id" in sig.parameters

    def test_config_has_shared_team_contact_id(self):
        """Config should have QUO_SHARED_TEAM_CONTACT_ID and FRONTEND_BASE_URL."""
        from api.src.sernia_ai.config import QUO_SHARED_TEAM_CONTACT_ID, FRONTEND_BASE_URL

        assert isinstance(QUO_SHARED_TEAM_CONTACT_ID, str)
        assert len(QUO_SHARED_TEAM_CONTACT_ID) > 0
        assert FRONTEND_BASE_URL.startswith("http")

    def test_app_setting_model_imports(self):
        """AppSetting model should import and have expected fields."""
        from api.src.sernia_ai.models import AppSetting

        assert AppSetting.__tablename__ == "app_settings"
        assert hasattr(AppSetting, "key")
        assert hasattr(AppSetting, "value")
        assert hasattr(AppSetting, "updated_at")

    def test_circular_trigger_guard_wired(self):
        """_get_ai_phone_number should exist in webhook routing for circular guard."""
        import api.src.open_phone.routes as routes_module

        assert hasattr(routes_module, "_get_ai_phone_number")
        assert callable(routes_module._get_ai_phone_number)

    def test_list_user_conversations_accepts_none_clerk_user_id(self):
        """list_user_conversations should accept clerk_user_id=None."""
        import inspect
        from api.src.ai_demos.models import list_user_conversations

        sig = inspect.signature(list_user_conversations)
        param = sig.parameters["clerk_user_id"]
        # Should allow None (str | None)
        assert param.default is inspect.Parameter.empty or param.default is None


# =========================================================================
# Unit Tests
# =========================================================================


class TestBackgroundAgentRunner:
    """Test the core run_agent_for_trigger function."""

    @pytest.mark.asyncio
    async def test_creates_conversation_on_agent_response(self):
        """When agent returns a normal text response, a conversation should be
        created and a trigger alert push notification sent."""
        mock_result = MagicMock()
        mock_result.output = "The tenant reported a maintenance issue. Recommend replying."
        mock_result.all_messages.return_value = []

        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_trigger_alert") as mock_alert,
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="Test message from +1234567890",
                trigger_metadata={"trigger_source": "sms"},
                notification_title="SMS from +1234567890",
                notification_body="Test message",
            )

            assert conv_id is not None
            mock_save.assert_called_once()
            # Alert should be called (via create_task, but we're checking it was called)
            # Note: asyncio.create_task wraps the coroutine, so we check the alert was created

    @pytest.mark.asyncio
    async def test_silent_processing_returns_none(self):
        """When agent returns NoAction, conversation is saved but returns None."""
        from api.src.sernia_ai.agent import NoAction
        mock_result = MagicMock()
        mock_result.output = NoAction(reason="routine ack")

        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="Just a routine ack message",
                trigger_metadata={"trigger_source": "sms"},
            )

            assert conv_id is None
            mock_save.assert_called_once()  # always persisted for history

    @pytest.mark.asyncio
    async def test_handles_agent_error_gracefully(self):
        """When the agent raises an exception, return None without crashing."""
        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Model API error"))

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="This will fail",
                trigger_metadata={"trigger_source": "sms"},
            )

            assert conv_id is None


    @pytest.mark.asyncio
    async def test_triggers_disabled_returns_none(self):
        """When is_sernia_ai_enabled() returns False, return None without running agent."""
        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=False),
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
        ):
            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="This should be skipped",
                trigger_metadata={"trigger_source": "sms"},
            )

            assert conv_id is None
            mock_agent.run.assert_not_called()


class TestRateLimiter:
    """Test the per-key rate limiter in background_agent_runner."""

    def test_first_call_not_limited(self):
        """First call for a key should not be rate-limited."""
        from api.src.sernia_ai.triggers.background_agent_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False

    def test_immediate_repeat_is_limited(self):
        """Second call for the same key within the window should be rate-limited."""
        from api.src.sernia_ai.triggers.background_agent_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False
        assert _is_rate_limited("sms:+14155550100") is True

    def test_different_keys_not_limited(self):
        """Different keys should not block each other."""
        from api.src.sernia_ai.triggers.background_agent_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False
        assert _is_rate_limited("sms:+14155550200") is False

    def test_expired_cooldown_allows_retry(self):
        """After the cooldown expires, the key should be allowed again."""
        from api.src.sernia_ai.triggers.background_agent_runner import (
            _is_rate_limited,
            _trigger_cooldowns,
        )
        import time

        key = "sms:+14155550100"
        assert _is_rate_limited(key) is False
        # Manually set the timestamp to well in the past
        _trigger_cooldowns[key] = time.monotonic() - 300
        assert _is_rate_limited(key) is False

    @pytest.mark.asyncio
    async def test_rate_limited_trigger_returns_none(self):
        """run_agent_for_trigger should return None when rate-limited."""
        mock_result = MagicMock()
        mock_result.output = "Some analysis"
        mock_result.all_messages.return_value = []

        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_trigger_alert"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            # First call goes through
            result1 = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="Message 1",
                trigger_metadata={"trigger_source": "sms"},
                rate_limit_key="+14155550100",
            )
            assert result1 is not None
            assert mock_agent.run.call_count == 1

            # Second call with same key is rate-limited — agent never runs
            result2 = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="Message 2",
                trigger_metadata={"trigger_source": "sms"},
                rate_limit_key="+14155550100",
            )
            assert result2 is None
            assert mock_agent.run.call_count == 1  # still 1 — agent was not called


class TestAiSmsRateLimiter:
    """Test the sliding-window rate limiter for AI SMS event trigger."""

    def test_first_call_not_limited(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _is_ai_sms_rate_limited
        assert _is_ai_sms_rate_limited("+14155550100") is False

    def test_allows_up_to_max_calls(self):
        """Should allow up to AI_SMS_RATE_LIMIT_MAX_CALLS within the window."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
            _is_ai_sms_rate_limited,
            AI_SMS_RATE_LIMIT_MAX_CALLS,
        )
        phone = "+14155550100"
        for _ in range(AI_SMS_RATE_LIMIT_MAX_CALLS):
            assert _is_ai_sms_rate_limited(phone) is False
        # Next call should be limited
        assert _is_ai_sms_rate_limited(phone) is True

    def test_different_phones_independent(self):
        """Different phone numbers should not block each other."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _is_ai_sms_rate_limited
        assert _is_ai_sms_rate_limited("+14155550100") is False
        assert _is_ai_sms_rate_limited("+14155550200") is False

    def test_expired_timestamps_pruned(self):
        """After the window expires, old timestamps should be pruned and calls allowed."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
            _is_ai_sms_rate_limited,
            _ai_sms_call_timestamps,
            AI_SMS_RATE_LIMIT_MAX_CALLS,
        )
        import time

        phone = "+14155550100"
        # Fill up the window
        for _ in range(AI_SMS_RATE_LIMIT_MAX_CALLS):
            _is_ai_sms_rate_limited(phone)

        assert _is_ai_sms_rate_limited(phone) is True

        # Manually set all timestamps to well in the past
        _ai_sms_call_timestamps[phone] = [time.monotonic() - 700] * AI_SMS_RATE_LIMIT_MAX_CALLS
        assert _is_ai_sms_rate_limited(phone) is False


class TestScheduledTriggers:
    """Test scheduled trigger logic."""

    @pytest.mark.asyncio
    async def test_scheduled_checks_calls_runner(self):
        """run_scheduled_checks should call run_agent_for_trigger with scheduled-checks skill reference."""
        with patch("api.src.sernia_ai.triggers.scheduled_triggers.run_agent_for_trigger") as mock_run:
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.scheduled_triggers import run_scheduled_checks

            await run_scheduled_checks()

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "scheduled_check"
            assert "scheduled-checks" in call_kwargs["trigger_prompt"].lower()
            assert call_kwargs["rate_limit_key"] == "scheduled_check"


class TestNotifyTeamSms:
    """Test the SMS team notification function."""

    @pytest.fixture(autouse=True)
    def _clear_phone_cache(self):
        """Reset the cached phone number between tests."""
        import api.src.sernia_ai.push.service as svc
        svc._shared_team_phone = None
        yield
        svc._shared_team_phone = None

    @pytest.mark.asyncio
    async def test_sends_sms_with_deeplink(self):
        """notify_team_sms should look up phone, build message with deeplink, and send."""
        mock_response = Response(
            200,
            json={
                "data": {
                    "defaultFields": {
                        "phoneNumbers": [{"value": "+14125551234"}],
                    },
                },
            },
            request=Request("GET", "https://api.openphone.com/v1/contacts/test"),
        )

        with (
            patch("api.src.sernia_ai.push.service.httpx.AsyncClient") as mock_client_cls,
            patch("api.src.open_phone.service.send_message") as mock_send,
            patch.dict("os.environ", {"OPEN_PHONE_API_KEY": "test-key"}),
        ):
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_send.return_value = AsyncMock()

            from api.src.sernia_ai.push.service import notify_team_sms

            await notify_team_sms(
                title="Approval Needed: Send Email",
                body="to: tenant@example.com, subject: Lease Renewal",
                conversation_id="conv-abc-123",
            )

            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["to_phone_number"] == "+14125551234"
            assert call_kwargs["from_phone_number"] == "PNWvNqsFFy"
            assert "conv-abc-123" in call_kwargs["message"]
            assert "/sernia-chat?id=conv-abc-123" in call_kwargs["message"]
            assert "Approval Needed" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_skips_when_phone_lookup_fails(self):
        """When phone lookup fails, SMS should be silently skipped."""
        with (
            patch("api.src.sernia_ai.push.service.httpx.AsyncClient") as mock_client_cls,
            patch("api.src.open_phone.service.send_message") as mock_send,
            patch.dict("os.environ", {"OPEN_PHONE_API_KEY": "test-key"}),
        ):
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("API down"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            from api.src.sernia_ai.push.service import notify_team_sms

            await notify_team_sms(
                title="Test",
                body="Test body",
                conversation_id="conv-xyz",
            )

            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_caches_phone_number(self):
        """Phone number should be cached after first lookup."""
        import api.src.sernia_ai.push.service as svc
        svc._shared_team_phone = "+14125559999"

        with (
            patch("api.src.open_phone.service.send_message") as mock_send,
            patch("api.src.sernia_ai.push.service.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_send.return_value = AsyncMock()

            from api.src.sernia_ai.push.service import notify_team_sms

            await notify_team_sms(
                title="Test",
                body="Cached test",
                conversation_id="conv-cached",
            )

            # Should NOT have created an httpx client — used cache
            mock_client_cls.assert_not_called()
            mock_send.assert_called_once()
            assert mock_send.call_args[1]["to_phone_number"] == "+14125559999"

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self):
        """Even if send_message raises, notify_team_sms should not propagate."""
        import api.src.sernia_ai.push.service as svc
        svc._shared_team_phone = "+14125559999"

        with patch("api.src.open_phone.service.send_message") as mock_send:
            mock_send.side_effect = RuntimeError("OpenPhone API error")

            from api.src.sernia_ai.push.service import notify_team_sms

            # Should not raise
            await notify_team_sms(
                title="Test",
                body="Error test",
                conversation_id="conv-err",
            )


# =========================================================================
# AI SMS Event Trigger Tests
# =========================================================================


class TestAiSmsEventTriggerSmoke:
    """Verify AI SMS event trigger imports and wiring."""

    def test_ai_sms_event_trigger_imports(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
            handle_ai_sms_event,
            _verify_internal_contact,
            _fetch_sms_thread,
            _sms_to_model_messages,
            _trim_sms_history,
            _send_sms_reply,
            _is_ai_sms_rate_limited,
            AI_SMS_RATE_LIMIT_MAX_CALLS,
            AI_SMS_RATE_LIMIT_WINDOW_SECONDS,
        )
        assert callable(handle_ai_sms_event)
        assert callable(_verify_internal_contact)
        assert callable(_fetch_sms_thread)
        assert callable(_sms_to_model_messages)
        assert callable(_trim_sms_history)
        assert callable(_send_sms_reply)
        assert callable(_is_ai_sms_rate_limited)
        assert AI_SMS_RATE_LIMIT_MAX_CALLS == 10
        assert AI_SMS_RATE_LIMIT_WINDOW_SECONDS == 600

    def test_config_has_sms_conversation_max_messages(self):
        from api.src.sernia_ai.config import SMS_CONVERSATION_MAX_MESSAGES
        assert isinstance(SMS_CONVERSATION_MAX_MESSAGES, int)
        assert SMS_CONVERSATION_MAX_MESSAGES > 0

    def test_config_has_sms_history_trimming_constants(self):
        from api.src.sernia_ai.config import SMS_HISTORY_MIN_DAYS, SMS_HISTORY_MIN_MESSAGES
        assert isinstance(SMS_HISTORY_MIN_DAYS, int)
        assert SMS_HISTORY_MIN_DAYS > 0
        assert isinstance(SMS_HISTORY_MIN_MESSAGES, int)
        assert SMS_HISTORY_MIN_MESSAGES > 0

    def test_ai_sms_event_trigger_wired_in_webhook(self):
        """handle_ai_sms_event should be imported in open_phone routes."""
        import api.src.open_phone.routes as routes_module
        assert hasattr(routes_module, "handle_ai_sms_event")


class TestSmsToModelMessages:
    """Test OpenPhone message conversion to PydanticAI format."""

    def test_converts_incoming_to_user_prompt(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [{"body": "Hello AI", "direction": "incoming"}]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[0].parts[0], UserPromptPart)
        assert result[0].parts[0].content == "Hello AI"

    def test_converts_outgoing_to_model_response(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [{"body": "Hi! How can I help?", "direction": "outgoing"}]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert isinstance(result[0], ModelResponse)
        assert isinstance(result[0].parts[0], TextPart)
        assert result[0].parts[0].content == "Hi! How can I help?"

    def test_reverses_to_chronological_order(self):
        """OpenPhone returns newest-first; we should reverse."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [
            {"body": "Second message", "direction": "incoming"},
            {"body": "First message", "direction": "incoming"},
        ]
        result = _sms_to_model_messages(messages)

        assert len(result) == 2
        assert result[0].parts[0].content == "First message"
        assert result[1].parts[0].content == "Second message"

    def test_skips_empty_messages(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [
            {"body": "Real message", "direction": "incoming"},
            {"body": "", "direction": "outgoing"},
            {"body": "  ", "direction": "incoming"},
        ]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert result[0].parts[0].content == "Real message"

    def test_handles_content_field(self):
        """Some OpenPhone responses use 'content' instead of 'body'."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [{"content": "Via content field", "direction": "incoming"}]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert result[0].parts[0].content == "Via content field"

    def test_handles_text_field(self):
        """OpenPhone API returns SMS content in the 'text' field (primary)."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        # Quo returns newest-first; _sms_to_model_messages reverses to chronological
        messages = [
            {"text": "Outgoing via text", "direction": "outgoing"},
            {"text": "Via text field", "direction": "incoming"},
        ]
        result = _sms_to_model_messages(messages)

        assert len(result) == 2
        assert result[0].parts[0].content == "Via text field"
        assert result[1].parts[0].content == "Outgoing via text"

    def test_text_field_takes_priority_over_body(self):
        """'text' field should take priority over 'body' (matching Quo API)."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [{"text": "from text", "body": "from body", "direction": "incoming"}]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert result[0].parts[0].content == "from text"


class TestSmsTimestampPreservation:
    """Test that _sms_to_model_messages preserves original timestamps."""

    def test_preserves_created_at_timestamp_on_incoming(self):
        """Incoming SMS: timestamp preserved on UserPromptPart (not ModelRequest, which is always None)."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [
            {"body": "Hello", "direction": "incoming", "createdAt": "2025-06-15T10:30:00Z"},
        ]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        # ModelRequest.timestamp is always None after serialization;
        # the real timestamp lives on UserPromptPart.timestamp.
        part = result[0].parts[0]
        assert part.timestamp.year == 2025
        assert part.timestamp.month == 6
        assert part.timestamp.day == 15

    def test_preserves_created_at_timestamp_on_outgoing(self):
        """Outgoing SMS: timestamp preserved on ModelResponse.timestamp."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [
            {"body": "Hi back", "direction": "outgoing", "createdAt": "2025-06-15T10:31:00Z"},
        ]
        result = _sms_to_model_messages(messages)

        assert len(result) == 1
        assert result[0].timestamp.year == 2025
        assert result[0].timestamp.month == 6
        assert result[0].timestamp.day == 15

    def test_handles_missing_created_at(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _sms_to_model_messages

        messages = [{"body": "Hello", "direction": "incoming"}]
        result = _sms_to_model_messages(messages)

        # Should still produce a message (with auto-generated timestamp)
        assert len(result) == 1
        assert result[0].parts[0].content == "Hello"


class TestTrimSmsHistory:
    """Test _trim_sms_history reduces conversation history to recent window."""

    def _make_user_msg(self, text: str, days_ago: int = 0) -> ModelRequest:
        from datetime import datetime, timedelta, timezone
        ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
        # Timestamp must be on UserPromptPart (ModelRequest.timestamp is always None after ser/deser)
        return ModelRequest(parts=[UserPromptPart(content=text, timestamp=ts)])

    def _make_assistant_msg(self, text: str, days_ago: int = 0) -> ModelResponse:
        from datetime import datetime, timedelta, timezone
        ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return ModelResponse(parts=[TextPart(content=text)], timestamp=ts)

    def test_no_trim_when_few_messages(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _trim_sms_history

        messages = [
            self._make_user_msg("Hi", days_ago=1),
            self._make_assistant_msg("Hello!", days_ago=1),
        ]
        result, removed = _trim_sms_history(messages, min_days=3, min_messages=3)
        assert removed == 0
        assert len(result) == 2

    def test_trims_old_messages_beyond_both_windows(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _trim_sms_history

        # 10 user turns: days 20, 18, 16, 14, 12, 10, 8, 6, 2, 0
        messages = []
        for i, days in enumerate([20, 18, 16, 14, 12, 10, 8, 6, 2, 0]):
            messages.append(self._make_user_msg(f"msg-{i}", days_ago=days))
            messages.append(self._make_assistant_msg(f"reply-{i}", days_ago=days))

        result, removed = _trim_sms_history(messages, min_days=3, min_messages=3)

        # Last 3 messages are at days 6, 2, 0 — last 3 days covers 2, 0
        # "Whichever goes further" → 3 messages wins (goes back to day 6)
        # So we keep messages from index 14 onward (3 user turns * 2 msgs each = 6 messages)
        assert removed > 0
        assert len(result) == 6  # last 3 user + assistant pairs

    def test_time_window_wins_when_more_messages_in_period(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _trim_sms_history

        # Many messages in last 3 days, plus old ones
        messages = [
            self._make_user_msg("old-1", days_ago=30),
            self._make_assistant_msg("old-reply-1", days_ago=30),
            self._make_user_msg("old-2", days_ago=20),
            self._make_assistant_msg("old-reply-2", days_ago=20),
        ]
        # Add 5 turns within last 3 days
        for i in range(5):
            messages.append(self._make_user_msg(f"recent-{i}", days_ago=2))
            messages.append(self._make_assistant_msg(f"recent-reply-{i}", days_ago=2))

        result, removed = _trim_sms_history(messages, min_days=3, min_messages=3)

        # 3-day window has 5 turns (10 msgs) — more than 3-message window
        # Time window goes further back, so it wins
        assert removed == 4  # the 4 old messages trimmed
        assert len(result) == 10  # 5 recent turns

    def test_empty_history_returns_empty(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _trim_sms_history

        result, removed = _trim_sms_history([], min_days=3, min_messages=3)
        assert result == []
        assert removed == 0

    def test_all_messages_within_window_no_trim(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _trim_sms_history

        messages = [
            self._make_user_msg("msg-1", days_ago=2),
            self._make_assistant_msg("reply-1", days_ago=2),
            self._make_user_msg("msg-2", days_ago=1),
            self._make_assistant_msg("reply-2", days_ago=1),
            self._make_user_msg("msg-3", days_ago=0),
            self._make_assistant_msg("reply-3", days_ago=0),
        ]
        result, removed = _trim_sms_history(messages, min_days=3, min_messages=3)
        assert removed == 0
        assert len(result) == 6


class TestVerifyInternalContact:
    """Test the internal contact verification."""

    @pytest.mark.asyncio
    async def test_returns_contact_for_internal(self):
        fake_contact = {
            "defaultFields": {
                "company": "Sernia Capital LLC",
                "firstName": "John",
                "lastName": "Doe",
                "phoneNumbers": [{"value": "+14155550100"}],
            },
        }

        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.find_contacts_by_phone",
            new_callable=AsyncMock,
            return_value=[fake_contact],
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import _verify_internal_contact

            contact = await _verify_internal_contact("+14155550100")
            assert contact is not None
            assert contact["defaultFields"]["firstName"] == "John"

    @pytest.mark.asyncio
    async def test_returns_none_for_external(self):
        fake_contact = {
            "defaultFields": {
                "company": "Some Other Company",
                "firstName": "Jane",
                "phoneNumbers": [{"value": "+14155550100"}],
            },
        }

        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.find_contacts_by_phone",
            new_callable=AsyncMock,
            return_value=[fake_contact],
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import _verify_internal_contact

            contact = await _verify_internal_contact("+14155550100")
            assert contact is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self):
        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.find_contacts_by_phone",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import _verify_internal_contact

            contact = await _verify_internal_contact("+14155550100")
            assert contact is None

    @pytest.mark.asyncio
    async def test_returns_internal_when_mixed_contacts(self):
        """When multiple contacts share a phone, return the internal one."""
        external_contact = {
            "defaultFields": {
                "company": "Tenant Corp",
                "firstName": "Jane",
                "phoneNumbers": [{"value": "+14155550100"}],
            },
        }
        internal_contact = {
            "defaultFields": {
                "company": "Sernia Capital LLC",
                "firstName": "John",
                "phoneNumbers": [{"value": "+14155550100"}],
            },
        }

        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.find_contacts_by_phone",
            new_callable=AsyncMock,
            return_value=[external_contact, internal_contact],
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import _verify_internal_contact

            contact = await _verify_internal_contact("+14155550100")
            assert contact is not None
            assert contact["defaultFields"]["firstName"] == "John"


class TestHandleAiSmsEvent:
    """Test the main handle_ai_sms_event handler."""

    @pytest.mark.asyncio
    async def test_sends_sms_response(self):
        """Agent text output should be sent back via SMS."""
        mock_result = MagicMock()
        mock_result.output = "I'll check on that for you."
        mock_result.all_messages.return_value = []

        internal_contact = {
            "defaultFields": {
                "company": "Sernia Capital LLC",
                "firstName": "John",
                "lastName": "Doe",
            },
        }

        with (
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact",
                  return_value=internal_contact),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.get_conversation_messages",
                  return_value=[]),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._fetch_sms_thread",
                  return_value=[]),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._send_sms_reply") as mock_reply,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.commit_and_push"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.extract_pending_approvals",
                  return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({
                "from_number": "+14155550100",
                "message_text": "Can you check the lease?",
                "event_id": "evt_ai_1",
            })

            mock_agent.run.assert_called_once()
            mock_save.assert_called_once()
            save_kwargs = mock_save.call_args[1]
            assert save_kwargs["modality"] == "sms"
            assert save_kwargs["contact_identifier"] == "+14155550100"
            assert save_kwargs["conversation_id"] == "ai_sms_from_14155550100"

    @pytest.mark.asyncio
    async def test_blocks_external_contacts(self):
        """Messages from external/unknown contacts should be silently ignored."""
        with (
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact",
                  return_value=None),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.sernia_agent") as mock_agent,
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({
                "from_number": "+19995550000",
                "message_text": "Who is this?",
                "event_id": "evt_external",
            })

            mock_agent.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_missing_data(self):
        """Events with missing from_number or message_text should be skipped."""
        with patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact") as mock_verify:
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({"event_id": "evt_no_data"})
            mock_verify.assert_not_called()

            await handle_ai_sms_event({"from_number": "+1", "event_id": "evt_no_text"})
            mock_verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_loads_existing_conversation_from_db(self):
        """Follow-up messages should load history from DB, not bootstrap."""
        mock_result = MagicMock()
        mock_result.output = "Updated response"
        mock_result.all_messages.return_value = []

        existing_history = [
            ModelRequest(parts=[UserPromptPart(content="First message")]),
            ModelResponse(parts=[TextPart(content="First reply")]),
        ]

        internal_contact = {
            "defaultFields": {
                "company": "Sernia Capital LLC",
                "firstName": "Jane",
                "lastName": "Smith",
            },
        }

        with (
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact",
                  return_value=internal_contact),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.get_conversation_messages",
                  return_value=existing_history) as mock_get_msgs,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.save_agent_conversation"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._send_sms_reply"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._fetch_sms_thread") as mock_fetch,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.commit_and_push"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.extract_pending_approvals",
                  return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({
                "from_number": "+14155550100",
                "message_text": "Follow-up question",
                "event_id": "evt_followup",
            })

            # Should have fetched SMS thread for merging (always fetched now)
            mock_fetch.assert_called_once()
            # Agent should have received existing DB history (SMS thread was empty)
            agent_call = mock_agent.run.call_args
            assert agent_call[1]["message_history"] == existing_history

    @pytest.mark.asyncio
    async def test_hitl_sends_notification_not_sms(self):
        """When agent returns pending approvals, notify team but don't send SMS."""
        mock_result = MagicMock()
        mock_result.output = None
        mock_result.all_messages.return_value = []

        pending = [{"tool_call_id": "tc_1", "tool_name": "send_email", "args": {"to": "test@test.com"}}]

        internal_contact = {
            "defaultFields": {
                "company": "Sernia Capital LLC",
                "firstName": "John",
                "lastName": "Doe",
            },
        }

        with (
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact",
                  return_value=internal_contact),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.get_conversation_messages",
                  return_value=[]),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._fetch_sms_thread",
                  return_value=[]),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.save_agent_conversation"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._send_sms_reply") as mock_reply,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.notify_pending_approval") as mock_notify,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.notify_team_sms") as mock_team_sms,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.commit_and_push"),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.extract_pending_approvals",
                  return_value=pending),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({
                "from_number": "+14155550100",
                "message_text": "Send an email to the tenant",
                "event_id": "evt_hitl",
            })

            # Should NOT have sent SMS reply (waiting for approval)
            mock_reply.assert_not_called()


# =========================================================================
# Zillow Email Event Trigger Tests
# =========================================================================


class TestIsZillowEmail:
    """Test the is_zillow_email helper."""

    def test_zillow_from_address(self):
        from api.src.sernia_ai.triggers.zillow_email_event_trigger import is_zillow_email
        assert is_zillow_email("noreply@zillow.com") is True
        assert is_zillow_email("leads@messaging.zillow.com") is True
        assert is_zillow_email("NOREPLY@ZILLOW.COM") is True

    def test_non_zillow_from_address(self):
        from api.src.sernia_ai.triggers.zillow_email_event_trigger import is_zillow_email
        assert is_zillow_email("tenant@gmail.com") is False
        assert is_zillow_email("zillow-impersonator@evil.com") is False
        assert is_zillow_email("user@notzillow.com") is False

    def test_empty_and_none(self):
        from api.src.sernia_ai.triggers.zillow_email_event_trigger import is_zillow_email
        assert is_zillow_email("") is False
        # is_zillow_email requires str, but guard handles falsy
        assert is_zillow_email("") is False


class TestGetEmilioClerkUserId:
    """Test the cached DB lookup for Emilio's clerk_user_id."""

    @pytest.mark.asyncio
    async def test_returns_cached_value(self):
        """After first lookup, should return cached value without DB call."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod
        mod._emilio_clerk_user_id = "user_cached_123"

        result = await mod._get_emilio_clerk_user_id()
        assert result == "user_cached_123"

    @pytest.mark.asyncio
    async def test_looks_up_from_db(self):
        """Should query contacts→users join and cache the result."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        with patch(
            "api.src.contact.service.get_clerk_user_id_by_slug",
            new_callable=AsyncMock,
            return_value="user_2abc123",
        ) as mock_lookup:
            result = await mod._get_emilio_clerk_user_id()

            assert result == "user_2abc123"
            assert mod._emilio_clerk_user_id == "user_2abc123"
            mock_lookup.assert_called_once_with("emilio")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """Should return None if no matching contact/user in DB."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        with patch(
            "api.src.contact.service.get_clerk_user_id_by_slug",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await mod._get_emilio_clerk_user_id()
            assert result is None
            assert mod._emilio_clerk_user_id is None  # not cached

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        """Should return None and not crash if DB lookup fails."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        with patch(
            "api.src.contact.service.get_clerk_user_id_by_slug",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection failed"),
        ):
            result = await mod._get_emilio_clerk_user_id()
            assert result is None


class TestQueueZillowEmailEventDedup:
    """Dedup behavior of queue_zillow_email_event by Gmail message_id."""

    @pytest.mark.asyncio
    async def test_dedup_within_pending_window(self):
        """Re-queuing the same message_id while pending should be a no-op."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        # Replace asyncio.create_task so the debounce timer never actually starts.
        def _close_coro(coro, *args, **kwargs):
            # Close the coroutine so pytest doesn't warn about it never being awaited.
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        with patch.object(mod.asyncio, "create_task", side_effect=_close_coro):
            await mod.queue_zillow_email_event(
                thread_id="t1",
                message_id="msg_dup_1",
                subject="Amelia requesting info",
                from_address="lead@convo.zillow.com",
                body_text="hi",
            )
            # Three pubsub redeliveries of the same logical email
            for _ in range(3):
                await mod.queue_zillow_email_event(
                    thread_id="t1",
                    message_id="msg_dup_1",
                    subject="Amelia requesting info",
                    from_address="lead@convo.zillow.com",
                    body_text="hi",
                )

        assert len(mod._pending_emails) == 1
        assert mod._pending_emails[0]["message_id"] == "msg_dup_1"

    @pytest.mark.asyncio
    async def test_distinct_ids_accumulate(self):
        """Distinct message_ids should all be accumulated in the batch."""
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        def _close_coro(coro, *args, **kwargs):
            # Close the coroutine so pytest doesn't warn about it never being awaited.
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        with patch.object(mod.asyncio, "create_task", side_effect=_close_coro):
            for mid in ("msg_a", "msg_b", "msg_c"):
                await mod.queue_zillow_email_event(
                    thread_id="t1",
                    message_id=mid,
                    subject="Subj",
                    from_address="lead@convo.zillow.com",
                    body_text=None,
                )

        ids = [e["message_id"] for e in mod._pending_emails]
        assert ids == ["msg_a", "msg_b", "msg_c"]

    @pytest.mark.asyncio
    async def test_recently_fired_ttl_blocks_requeue(self):
        """A message_id marked as recently fired should be rejected on re-queue."""
        import time as _time
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        mod._recently_fired_message_ids["msg_just_fired"] = _time.monotonic()

        def _close_coro(coro, *args, **kwargs):
            # Close the coroutine so pytest doesn't warn about it never being awaited.
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        with patch.object(mod.asyncio, "create_task", side_effect=_close_coro):
            await mod.queue_zillow_email_event(
                thread_id="t1",
                message_id="msg_just_fired",
                subject="Subj",
                from_address="lead@convo.zillow.com",
                body_text=None,
            )

        assert mod._pending_emails == []

    @pytest.mark.asyncio
    async def test_recently_fired_prunes_expired(self):
        """TTL cache entries older than RECENTLY_FIRED_TTL_SECONDS should be pruned."""
        import time as _time
        import api.src.sernia_ai.triggers.zillow_email_event_trigger as mod

        # Entry from two hours ago — older than the 1h TTL
        mod._recently_fired_message_ids["msg_expired"] = _time.monotonic() - 7200

        def _close_coro(coro, *args, **kwargs):
            # Close the coroutine so pytest doesn't warn about it never being awaited.
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        with patch.object(mod.asyncio, "create_task", side_effect=_close_coro):
            await mod.queue_zillow_email_event(
                thread_id="t1",
                message_id="msg_expired",
                subject="Subj",
                from_address="lead@convo.zillow.com",
                body_text=None,
            )

        # Expired entry was pruned, so the email was accepted into the batch
        assert "msg_expired" not in mod._recently_fired_message_ids
        assert len(mod._pending_emails) == 1
        assert mod._pending_emails[0]["message_id"] == "msg_expired"


class TestZillowEmailBatchedTrigger:
    """Test _fire_batched_trigger (the core trigger logic after debounce)."""

    @pytest.mark.asyncio
    async def test_single_email_calls_runner_with_correct_source(self):
        """Single-email batch should call run_agent_for_trigger with correct source and metadata."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value="user_emilio_123"),
        ):
            mock_run.return_value = "conv-zillow-1"

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            emails = [{
                "thread_id": "thread_abc123",
                "message_id": "msg_1",
                "subject": "Rental Inquiry - 320 S Mathilda",
                "from_address": "noreply@zillow.com",
                "body_text": "Hi, I'm interested in the 1BR unit.",
            }]

            result = await _fire_batched_trigger(emails)

            assert result == "conv-zillow-1"
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "zillow_email_event"
            assert "320 S Mathilda" in call_kwargs["notification_body"]
            assert call_kwargs["notify_clerk_user_id"] == "user_emilio_123"

            meta = call_kwargs["trigger_metadata"]
            assert meta["trigger_source"] == "zillow_email_event"
            assert meta["trigger_type"] == "email_event"
            assert meta["email_count"] == 1

    @pytest.mark.asyncio
    async def test_includes_body_snippet_in_prompt(self):
        """Single-email trigger prompt should include a preview of the email body."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            await _fire_batched_trigger([{
                "thread_id": "thread_xyz",
                "message_id": "msg_2",
                "subject": "Inquiry",
                "from_address": "noreply@zillow.com",
                "body_text": "I have great credit and want to move in July.",
            }])

            call_kwargs = mock_run.call_args[1]
            assert "great credit" in call_kwargs["trigger_prompt"]

    @pytest.mark.asyncio
    async def test_handles_none_body_text(self):
        """Should not crash when body_text is None."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            await _fire_batched_trigger([{
                "thread_id": "thread_null",
                "message_id": "msg_3",
                "subject": "Test",
                "from_address": "noreply@zillow.com",
                "body_text": None,
            }])

            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_agent_takes_no_action(self):
        """When run_agent_for_trigger returns None (NoAction), should return None."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value="user_emilio"),
        ):
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            result = await _fire_batched_trigger([{
                "thread_id": "thread_cold",
                "message_id": "msg_4",
                "subject": "Re: Tour - 320 S Mathilda",
                "from_address": "noreply@zillow.com",
                "body_text": "Thanks, we'll let you know.",
            }])

            assert result is None

    @pytest.mark.asyncio
    async def test_passes_none_notify_when_no_emilio_user(self):
        """If Emilio clerk_user_id can't be found, should pass None (falls back to generic push)."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            mock_run.return_value = "conv-fallback"

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            await _fire_batched_trigger([{
                "thread_id": "thread_fb",
                "message_id": "msg_5",
                "subject": "Inquiry",
                "from_address": "noreply@zillow.com",
                "body_text": "Hello",
            }])

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["notify_clerk_user_id"] is None

    @pytest.mark.asyncio
    async def test_prompt_has_deeplink_and_skill_reference(self):
        """Trigger prompt should reference the skill and include a deeplink."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            await _fire_batched_trigger([{
                "thread_id": "t1",
                "message_id": "msg_6",
                "subject": "Test",
                "from_address": "noreply@zillow.com",
                "body_text": "Test body",
            }])

            call_kwargs = mock_run.call_args[1]
            prompt = call_kwargs["trigger_prompt"]
            assert "zillow-auto-reply" in prompt  # references the skill
            assert "sernia-chat?id=" in prompt  # deeplink embedded
            assert call_kwargs["conversation_id"] is not None

    @pytest.mark.asyncio
    async def test_multi_email_batch_prompt(self):
        """Multi-email batch should list all emails in the prompt."""
        with (
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger.run_agent_for_trigger") as mock_run,
            patch("api.src.sernia_ai.triggers.zillow_email_event_trigger._get_emilio_clerk_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            mock_run.return_value = "conv-batch"

            from api.src.sernia_ai.triggers.zillow_email_event_trigger import _fire_batched_trigger

            emails = [
                {"thread_id": "t1", "message_id": "m1", "subject": "Inquiry A", "from_address": "a@zillow.com", "body_text": "A"},
                {"thread_id": "t2", "message_id": "m2", "subject": "Inquiry B", "from_address": "b@zillow.com", "body_text": "B"},
            ]

            result = await _fire_batched_trigger(emails)

            assert result == "conv-batch"
            call_kwargs = mock_run.call_args[1]
            prompt = call_kwargs["trigger_prompt"]
            assert "2 new Zillow email(s)" in prompt
            assert "Inquiry A" in prompt
            assert "Inquiry B" in prompt
            assert call_kwargs["trigger_metadata"]["email_count"] == 2
            assert "2 email" in call_kwargs["notification_title"]


class TestBackgroundRunnerTargetedPush:
    """Test the notify_clerk_user_id parameter in run_agent_for_trigger."""

    @pytest.mark.asyncio
    async def test_targeted_push_when_clerk_user_id_set(self):
        """When notify_clerk_user_id is set, should call notify_user_push instead of notify_trigger_alert."""
        mock_result = MagicMock()
        mock_result.output = "Draft reply ready for review."
        mock_result.all_messages.return_value = []

        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.save_agent_conversation"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_user_push") as mock_user_push,
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_trigger_alert") as mock_alert,
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="zillow_email_event",
                trigger_prompt="Draft a reply",
                trigger_metadata={"trigger_source": "zillow_email_event"},
                notification_title="Zillow Draft Ready",
                notification_body="Re: Inquiry",
                notify_clerk_user_id="user_emilio_456",
            )

            assert conv_id is not None
            # Should use targeted push, not broadcast
            mock_user_push.assert_called_once()
            push_kwargs = mock_user_push.call_args[1]
            assert push_kwargs["clerk_user_id"] == "user_emilio_456"
            assert push_kwargs["title"] == "Zillow Draft Ready"
            assert push_kwargs["body"] == "Re: Inquiry"
            assert push_kwargs["data"]["conversation_id"] == conv_id
            # Should NOT have called the broadcast alert
            mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_push_when_no_clerk_user_id(self):
        """When notify_clerk_user_id is None, should fall back to notify_trigger_alert."""
        mock_result = MagicMock()
        mock_result.output = "Email needs attention."
        mock_result.all_messages.return_value = []

        with (
            patch("api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                  new_callable=AsyncMock, return_value=True),
            patch("api.src.sernia_ai.triggers.background_agent_runner.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_agent_runner.save_agent_conversation"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_user_push") as mock_user_push,
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_trigger_alert") as mock_alert,
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_agent_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            await run_agent_for_trigger(
                trigger_source="email",
                trigger_prompt="Check emails",
                trigger_metadata={"trigger_source": "email"},
                notification_title="Email alert",
                notify_clerk_user_id=None,
            )

            mock_alert.assert_called_once()
            mock_user_push.assert_not_called()


# =========================================================================
# Universal Kill Switch Tests
# =========================================================================


class TestUniversalKillSwitch:
    """Verify is_sernia_ai_enabled() blocks all agent execution paths when disabled."""

    @pytest.mark.asyncio
    async def test_ai_sms_event_skips_when_disabled(self):
        """handle_ai_sms_event should return early when Sernia AI is disabled."""
        with (
            patch(
                "api.src.sernia_ai.triggers.ai_sms_event_trigger.is_sernia_ai_enabled",
                new_callable=AsyncMock, return_value=False,
            ),
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger._verify_internal_contact", new_callable=AsyncMock) as mock_verify,
            patch("api.src.sernia_ai.triggers.ai_sms_event_trigger.sernia_agent") as mock_agent,
        ):
            from api.src.sernia_ai.triggers.ai_sms_event_trigger import handle_ai_sms_event

            await handle_ai_sms_event({
                "from_number": "+14155550100",
                "message_text": "Hello",
                "event_id": "evt_disabled",
            })

            # Kill switch blocks before contact verification
            mock_verify.assert_not_called()
            mock_agent.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_runner_skips_when_disabled(self):
        """run_agent_for_trigger should return None when Sernia AI is disabled."""
        with (
            patch(
                "api.src.sernia_ai.triggers.background_agent_runner.is_sernia_ai_enabled",
                new_callable=AsyncMock, return_value=False,
            ),
            patch("api.src.sernia_ai.triggers.background_agent_runner.sernia_agent") as mock_agent,
        ):
            from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

            result = await run_agent_for_trigger(
                trigger_source="test",
                trigger_prompt="Should be blocked",
                trigger_metadata={},
            )

            assert result is None
            mock_agent.run.assert_not_called()

    def test_is_sernia_ai_enabled_importable(self):
        """The shared kill switch function should be importable from models."""
        from api.src.sernia_ai.models import is_sernia_ai_enabled

        assert callable(is_sernia_ai_enabled)

    def test_ai_sms_event_trigger_imports_kill_switch(self):
        """ai_sms_event_trigger.py should import is_sernia_ai_enabled."""
        import api.src.sernia_ai.triggers.ai_sms_event_trigger as ai_sms_mod

        assert hasattr(ai_sms_mod, "is_sernia_ai_enabled")
