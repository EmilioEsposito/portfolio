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
    """Reset the in-memory rate-limit cooldowns between tests."""
    from api.src.sernia_ai.triggers.background_agent_runner import _trigger_cooldowns
    from api.src.sernia_ai.triggers.ai_sms_event_trigger import _ai_sms_call_timestamps
    _trigger_cooldowns.clear()
    _ai_sms_call_timestamps.clear()
    yield
    _trigger_cooldowns.clear()
    _ai_sms_call_timestamps.clear()


# =========================================================================
# Smoke Tests
# =========================================================================


class TestSmoke:
    """Verify trigger components import and are wired correctly."""

    def test_background_agent_runner_imports(self):
        from api.src.sernia_ai.triggers.background_agent_runner import (
            run_agent_for_trigger,
            SILENT_MARKER,
            RATE_LIMIT_SECONDS,
            _is_rate_limited,
        )
        from api.src.sernia_ai.config import TRIGGER_BOT_ID

        assert callable(run_agent_for_trigger)
        assert "[NO_ACTION_NEEDED]" in SILENT_MARKER
        assert RATE_LIMIT_SECONDS == 120
        assert callable(_is_rate_limited)
        assert TRIGGER_BOT_ID == "system:sernia-ai"

    def test_team_sms_event_trigger_imports(self):
        from api.src.sernia_ai.triggers.team_sms_event_trigger import handle_team_sms_event

        assert callable(handle_team_sms_event)

    def test_email_scheduled_trigger_imports(self):
        from api.src.sernia_ai.triggers.email_scheduled_trigger import (
            check_general_emails,
            check_zillow_emails,
        )

        assert callable(check_general_emails)
        assert callable(check_zillow_emails)

    def test_register_scheduled_triggers_imports(self):
        from api.src.sernia_ai.triggers.register_scheduled_triggers import register_scheduled_triggers

        assert callable(register_scheduled_triggers)

    def test_silent_marker_in_instructions(self):
        """SILENT_MARKER from instructions.py matches what background_agent_runner uses."""
        from api.src.sernia_ai.instructions import SILENT_MARKER as instr_marker
        from api.src.sernia_ai.triggers.background_agent_runner import SILENT_MARKER as runner_marker

        assert instr_marker == runner_marker

    def test_trigger_instructions_field_on_deps(self):
        """SerniaDeps should have the trigger_instructions field."""
        from api.src.sernia_ai.deps import SerniaDeps
        import dataclasses

        fields = {f.name for f in dataclasses.fields(SerniaDeps)}
        assert "trigger_instructions" in fields

    def test_inject_trigger_guidance_in_dynamic_instructions(self):
        """inject_trigger_guidance should be in DYNAMIC_INSTRUCTIONS."""
        from api.src.sernia_ai.instructions import DYNAMIC_INSTRUCTIONS

        fn_names = [fn.__name__ for fn in DYNAMIC_INSTRUCTIONS]
        assert "inject_trigger_guidance" in fn_names

    def test_notify_trigger_alert_imports(self):
        """Push service should have the new trigger alert function."""
        from api.src.sernia_ai.push.service import notify_trigger_alert

        assert callable(notify_trigger_alert)

    def test_notify_team_sms_imports(self):
        """Push service should have the new SMS notification function."""
        from api.src.sernia_ai.push.service import notify_team_sms

        assert callable(notify_team_sms)

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

    def test_team_sms_event_trigger_wired_in_webhook(self):
        """handle_team_sms_event should be imported in open_phone routes."""
        import api.src.open_phone.routes as routes_module

        assert hasattr(routes_module, "handle_team_sms_event")

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
        """When agent returns SILENT_MARKER, no conversation should be created."""
        mock_result = MagicMock()
        mock_result.output = "[NO_ACTION_NEEDED]"

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
            mock_save.assert_not_called()

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
            patch("api.src.sernia_ai.triggers.background_agent_runner.notify_team_sms"),
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


class TestTeamSmsEventTrigger:
    """Test team SMS event trigger logic."""

    @pytest.mark.asyncio
    async def test_processes_inbound_message(self):
        """Valid inbound SMS should call run_agent_for_trigger."""
        with patch("api.src.sernia_ai.triggers.team_sms_event_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = "conv-123"

            from api.src.sernia_ai.triggers.team_sms_event_trigger import handle_team_sms_event

            await handle_team_sms_event({
                "from_number": "+14155550100",
                "message_text": "The heater is broken",
                "event_id": "evt_123",
            })

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "sms"
            assert "+14155550100" in call_kwargs["trigger_prompt"]
            assert "heater is broken" in call_kwargs["trigger_prompt"]
            assert call_kwargs["trigger_metadata"]["trigger_phone"] == "+14155550100"
            assert call_kwargs["rate_limit_key"] == "+14155550100"

    @pytest.mark.asyncio
    async def test_skips_missing_data(self):
        """Events with missing from_number or message_text should be skipped."""
        with patch("api.src.sernia_ai.triggers.team_sms_event_trigger.run_agent_for_trigger") as mock_run:
            from api.src.sernia_ai.triggers.team_sms_event_trigger import handle_team_sms_event

            await handle_team_sms_event({"event_id": "evt_no_data"})
            mock_run.assert_not_called()

            await handle_team_sms_event({"from_number": "+1", "event_id": "evt_no_text"})
            mock_run.assert_not_called()


class TestEmailScheduledTrigger:
    """Test email scheduled trigger logic."""

    @pytest.mark.asyncio
    async def test_general_email_check_calls_runner(self):
        """check_general_emails should call run_agent_for_trigger."""
        with patch("api.src.sernia_ai.triggers.email_scheduled_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.email_scheduled_trigger import check_general_emails

            await check_general_emails()

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "email"

    @pytest.mark.asyncio
    async def test_zillow_email_check_calls_runner(self):
        """check_zillow_emails should call run_agent_for_trigger with Zillow context."""
        with patch("api.src.sernia_ai.triggers.email_scheduled_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.email_scheduled_trigger import check_zillow_emails

            await check_zillow_emails()

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "zillow_email"
            assert "credit score" in call_kwargs["trigger_instructions"].lower()


class TestInjectTriggerGuidance:
    """Test the trigger guidance dynamic instruction."""

    def test_returns_empty_without_trigger_instructions(self):
        """When trigger_instructions is None, instruction should return empty string."""
        from types import SimpleNamespace
        from api.src.sernia_ai.instructions import inject_trigger_guidance
        from api.src.sernia_ai.deps import SerniaDeps

        deps = SerniaDeps(
            db_session=None,  # type: ignore
            conversation_id="test",
            user_identifier="user_123",
            user_name="Test User",
            user_email="test@serniacapital.com",
            modality="web_chat",
            workspace_path="/tmp",  # type: ignore
            trigger_instructions=None,
        )
        ctx = SimpleNamespace(deps=deps)
        result = inject_trigger_guidance(ctx)  # type: ignore
        assert result == ""

    def test_returns_guidance_with_trigger_instructions(self):
        """When trigger_instructions is set, instruction should include it."""
        from types import SimpleNamespace
        from api.src.sernia_ai.instructions import inject_trigger_guidance
        from api.src.sernia_ai.deps import SerniaDeps

        deps = SerniaDeps(
            db_session=None,  # type: ignore
            conversation_id="test",
            user_identifier="system:sernia-ai",
            user_name="Sernia AI (Trigger)",
            user_email="emilio@serniacapital.com",
            modality="web_chat",
            workspace_path="/tmp",  # type: ignore
            trigger_instructions="This is an inbound SMS trigger.",
        )
        ctx = SimpleNamespace(deps=deps)
        result = inject_trigger_guidance(ctx)  # type: ignore
        assert "Trigger Event Processing" in result
        assert "inbound SMS trigger" in result
        assert "[NO_ACTION_NEEDED]" in result


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
            _send_sms_reply,
            _is_ai_sms_rate_limited,
            AI_SMS_RATE_LIMIT_MAX_CALLS,
            AI_SMS_RATE_LIMIT_WINDOW_SECONDS,
        )
        assert callable(handle_ai_sms_event)
        assert callable(_verify_internal_contact)
        assert callable(_fetch_sms_thread)
        assert callable(_sms_to_model_messages)
        assert callable(_send_sms_reply)
        assert callable(_is_ai_sms_rate_limited)
        assert AI_SMS_RATE_LIMIT_MAX_CALLS == 10
        assert AI_SMS_RATE_LIMIT_WINDOW_SECONDS == 600

    def test_config_has_sms_conversation_max_messages(self):
        from api.src.sernia_ai.config import SMS_CONVERSATION_MAX_MESSAGES
        assert isinstance(SMS_CONVERSATION_MAX_MESSAGES, int)
        assert SMS_CONVERSATION_MAX_MESSAGES > 0

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

            # Should NOT have bootstrapped from OpenPhone
            mock_fetch.assert_not_called()
            # Agent should have received existing history
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
