"""
Unit tests for the SMS hidden context seeding feature.

Tests _seed_sms_conversation and verifies the context flows correctly.

⚠️  SMS SAFETY: All SMS tests mock the send call. NEVER send
real SMS to external contacts from tests. See CLAUDE.md.
"""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from api.src.sernia_ai.tools.quo_tools import _seed_sms_conversation

# Patch targets — _seed_sms_conversation uses deferred imports
_GET_MSGS = "api.src.ai_demos.models.get_conversation_messages"
_SAVE_CONV = "api.src.ai_demos.models.save_agent_conversation"
_SESSION_FACTORY = "api.src.database.database.AsyncSessionFactory"


@asynccontextmanager
async def _mock_session_factory():
    """Yield a mock async session for patching AsyncSessionFactory."""
    yield MagicMock()


class TestSeedSmsConversation:
    """Tests for _seed_sms_conversation helper."""

    @pytest.mark.asyncio
    async def test_seeds_empty_conversation(self):
        """Seeds context into a brand-new conversation."""
        with (
            patch(_SESSION_FACTORY, side_effect=_mock_session_factory),
            patch(_GET_MSGS, new_callable=AsyncMock, return_value=[]) as mock_get,
            patch(_SAVE_CONV, new_callable=AsyncMock) as mock_save,
        ):
            await _seed_sms_conversation(
                "+14125551234", "Is the faucet fixed?", "Emilio asked to follow up",
            )

            mock_get.assert_called_once()
            assert mock_get.call_args[0][0] == "ai_sms_from_14125551234"
            mock_save.assert_called_once()
            saved_messages = mock_save.call_args[1]["messages"]
            assert len(saved_messages) == 2

            # First message: hidden context as UserPromptPart
            req = saved_messages[0]
            assert isinstance(req, ModelRequest)
            assert "Emilio asked to follow up" in req.parts[0].content
            assert "not visible to SMS recipient" in req.parts[0].content

            # Second message: outbound text as ModelResponse
            resp = saved_messages[1]
            assert isinstance(resp, ModelResponse)
            assert resp.parts[0].content == "Is the faucet fixed?"

            # Metadata
            assert mock_save.call_args[1]["conversation_id"] == "ai_sms_from_14125551234"
            assert mock_save.call_args[1]["modality"] == "sms"
            assert mock_save.call_args[1]["contact_identifier"] == "+14125551234"
            assert mock_save.call_args[1]["metadata"]["seeded_from_tool"] is True

    @pytest.mark.asyncio
    async def test_appends_to_existing_conversation(self):
        """Seeds context onto an existing conversation history."""
        existing = [
            ModelRequest(parts=[UserPromptPart(content="Hi there")]),
            ModelResponse(parts=[TextPart(content="Hello!")]),
        ]

        with (
            patch(_SESSION_FACTORY, side_effect=_mock_session_factory),
            patch(_GET_MSGS, new_callable=AsyncMock, return_value=existing),
            patch(_SAVE_CONV, new_callable=AsyncMock) as mock_save,
        ):
            await _seed_sms_conversation(
                "+14125551234", "Any update?", "Following up on lease",
            )

            saved_messages = mock_save.call_args[1]["messages"]
            # 2 existing + 2 seed = 4
            assert len(saved_messages) == 4
            # Existing preserved at the start
            assert saved_messages[0].parts[0].content == "Hi there"
            assert saved_messages[1].parts[0].content == "Hello!"
            # Seed appended
            assert "Following up on lease" in saved_messages[2].parts[0].content
            assert saved_messages[3].parts[0].content == "Any update?"

    @pytest.mark.asyncio
    async def test_conversation_id_strips_non_digits(self):
        """Phone number formatting is stripped to digits only."""
        with (
            patch(_SESSION_FACTORY, side_effect=_mock_session_factory),
            patch(_GET_MSGS, new_callable=AsyncMock, return_value=[]) as mock_get,
            patch(_SAVE_CONV, new_callable=AsyncMock),
        ):
            await _seed_sms_conversation(
                "+1 (412) 555-1234", "Hello", "context",
            )
            mock_get.assert_called_once()
            assert mock_get.call_args[0][0] == "ai_sms_from_14125551234"
