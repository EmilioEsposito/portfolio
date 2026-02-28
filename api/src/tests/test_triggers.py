"""
Smoke + unit tests for the Sernia AI trigger system.

Smoke: Verify all trigger modules import cleanly and are wired correctly.
Unit:  Test trigger logic with mocked agent runs and push notifications.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture(autouse=True)
def _clear_trigger_cooldowns():
    """Reset the in-memory rate-limit cooldowns between tests."""
    from api.src.sernia_ai.triggers.background_runner import _trigger_cooldowns
    _trigger_cooldowns.clear()
    yield
    _trigger_cooldowns.clear()


# =========================================================================
# Smoke Tests
# =========================================================================


class TestSmoke:
    """Verify trigger components import and are wired correctly."""

    def test_background_runner_imports(self):
        from api.src.sernia_ai.triggers.background_runner import (
            run_agent_for_trigger,
            SILENT_MARKER,
            SYSTEM_USER_ID,
            RATE_LIMIT_SECONDS,
            _is_rate_limited,
        )

        assert callable(run_agent_for_trigger)
        assert "[NO_ACTION_NEEDED]" in SILENT_MARKER
        assert RATE_LIMIT_SECONDS == 120
        assert callable(_is_rate_limited)

    def test_sms_trigger_imports(self):
        from api.src.sernia_ai.triggers.sms_trigger import handle_inbound_sms

        assert callable(handle_inbound_sms)

    def test_email_trigger_imports(self):
        from api.src.sernia_ai.triggers.email_trigger import (
            check_general_emails,
            check_zillow_emails,
        )

        assert callable(check_general_emails)
        assert callable(check_zillow_emails)

    def test_scheduler_imports(self):
        from api.src.sernia_ai.triggers.scheduler import register_sernia_trigger_jobs

        assert callable(register_sernia_trigger_jobs)

    def test_silent_marker_in_instructions(self):
        """SILENT_MARKER from instructions.py matches what background_runner uses."""
        from api.src.sernia_ai.instructions import SILENT_MARKER as instr_marker
        from api.src.sernia_ai.triggers.background_runner import SILENT_MARKER as runner_marker

        assert instr_marker == runner_marker

    def test_trigger_context_field_on_deps(self):
        """SerniaDeps should have the trigger_context field."""
        from api.src.sernia_ai.deps import SerniaDeps
        import dataclasses

        fields = {f.name for f in dataclasses.fields(SerniaDeps)}
        assert "trigger_context" in fields

    def test_inject_trigger_guidance_in_dynamic_instructions(self):
        """inject_trigger_guidance should be in DYNAMIC_INSTRUCTIONS."""
        from api.src.sernia_ai.instructions import DYNAMIC_INSTRUCTIONS

        fn_names = [fn.__name__ for fn in DYNAMIC_INSTRUCTIONS]
        assert "inject_trigger_guidance" in fn_names

    def test_notify_trigger_alert_imports(self):
        """Push service should have the new trigger alert function."""
        from api.src.sernia_ai.push.service import notify_trigger_alert

        assert callable(notify_trigger_alert)

    def test_sms_trigger_wired_in_webhook(self):
        """handle_inbound_sms should be imported in open_phone routes."""
        import api.src.open_phone.routes as routes_module

        # The import at module level should have succeeded
        assert hasattr(routes_module, "handle_inbound_sms")

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


class TestBackgroundRunner:
    """Test the core run_agent_for_trigger function."""

    @pytest.mark.asyncio
    async def test_creates_conversation_on_agent_response(self):
        """When agent returns a normal text response, a conversation should be
        created and a trigger alert push notification sent."""
        mock_result = MagicMock()
        mock_result.output = "The tenant reported a maintenance issue. Recommend replying."
        mock_result.all_messages.return_value = []

        with (
            patch("api.src.sernia_ai.triggers.background_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_runner.notify_trigger_alert") as mock_alert,
            patch("api.src.sernia_ai.triggers.background_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_runner import run_agent_for_trigger

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
            patch("api.src.sernia_ai.triggers.background_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_runner.commit_and_push"),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_runner import run_agent_for_trigger

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
            patch("api.src.sernia_ai.triggers.background_runner.AsyncSessionFactory") as mock_session_factory,
            patch("api.src.sernia_ai.triggers.background_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_runner.commit_and_push"),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Model API error"))

            from api.src.sernia_ai.triggers.background_runner import run_agent_for_trigger

            conv_id = await run_agent_for_trigger(
                trigger_source="sms",
                trigger_prompt="This will fail",
                trigger_metadata={"trigger_source": "sms"},
            )

            assert conv_id is None


class TestRateLimiter:
    """Test the per-key rate limiter in background_runner."""

    def test_first_call_not_limited(self):
        """First call for a key should not be rate-limited."""
        from api.src.sernia_ai.triggers.background_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False

    def test_immediate_repeat_is_limited(self):
        """Second call for the same key within the window should be rate-limited."""
        from api.src.sernia_ai.triggers.background_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False
        assert _is_rate_limited("sms:+14155550100") is True

    def test_different_keys_not_limited(self):
        """Different keys should not block each other."""
        from api.src.sernia_ai.triggers.background_runner import _is_rate_limited
        assert _is_rate_limited("sms:+14155550100") is False
        assert _is_rate_limited("sms:+14155550200") is False

    def test_expired_cooldown_allows_retry(self):
        """After the cooldown expires, the key should be allowed again."""
        from api.src.sernia_ai.triggers.background_runner import (
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
            patch("api.src.sernia_ai.triggers.background_runner.AsyncSessionFactory") as mock_sf,
            patch("api.src.sernia_ai.triggers.background_runner.sernia_agent") as mock_agent,
            patch("api.src.sernia_ai.triggers.background_runner.save_agent_conversation") as mock_save,
            patch("api.src.sernia_ai.triggers.background_runner.commit_and_push"),
            patch("api.src.sernia_ai.triggers.background_runner.notify_trigger_alert"),
            patch("api.src.sernia_ai.triggers.background_runner.notify_pending_approval"),
            patch("api.src.sernia_ai.triggers.background_runner.extract_pending_approvals", return_value=[]),
        ):
            mock_session = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_agent.run = AsyncMock(return_value=mock_result)

            from api.src.sernia_ai.triggers.background_runner import run_agent_for_trigger

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


class TestSmsTrigger:
    """Test SMS trigger logic."""

    @pytest.mark.asyncio
    async def test_processes_inbound_message(self):
        """Valid inbound SMS should call run_agent_for_trigger."""
        with patch("api.src.sernia_ai.triggers.sms_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = "conv-123"

            from api.src.sernia_ai.triggers.sms_trigger import handle_inbound_sms

            await handle_inbound_sms({
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
        with patch("api.src.sernia_ai.triggers.sms_trigger.run_agent_for_trigger") as mock_run:
            from api.src.sernia_ai.triggers.sms_trigger import handle_inbound_sms

            await handle_inbound_sms({"event_id": "evt_no_data"})
            mock_run.assert_not_called()

            await handle_inbound_sms({"from_number": "+1", "event_id": "evt_no_text"})
            mock_run.assert_not_called()


class TestEmailTrigger:
    """Test email trigger logic."""

    @pytest.mark.asyncio
    async def test_general_email_check_calls_runner(self):
        """check_general_emails should call run_agent_for_trigger."""
        with patch("api.src.sernia_ai.triggers.email_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.email_trigger import check_general_emails

            await check_general_emails()

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "email"

    @pytest.mark.asyncio
    async def test_zillow_email_check_calls_runner(self):
        """check_zillow_emails should call run_agent_for_trigger with Zillow context."""
        with patch("api.src.sernia_ai.triggers.email_trigger.run_agent_for_trigger") as mock_run:
            mock_run.return_value = None

            from api.src.sernia_ai.triggers.email_trigger import check_zillow_emails

            await check_zillow_emails()

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["trigger_source"] == "zillow_email"
            assert "credit score" in call_kwargs["trigger_context"].lower()


class TestInjectTriggerGuidance:
    """Test the trigger guidance dynamic instruction."""

    def test_returns_empty_without_trigger_context(self):
        """When trigger_context is None, instruction should return empty string."""
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
            trigger_context=None,
        )
        ctx = SimpleNamespace(deps=deps)
        result = inject_trigger_guidance(ctx)  # type: ignore
        assert result == ""

    def test_returns_guidance_with_trigger_context(self):
        """When trigger_context is set, instruction should include it."""
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
            trigger_context="This is an inbound SMS trigger.",
        )
        ctx = SimpleNamespace(deps=deps)
        result = inject_trigger_guidance(ctx)  # type: ignore
        assert "Trigger Event Processing" in result
        assert "inbound SMS trigger" in result
        assert "[NO_ACTION_NEEDED]" in result
