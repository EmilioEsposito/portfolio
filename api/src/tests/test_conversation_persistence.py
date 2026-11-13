"""Persistence contract for agent conversations: metadata survives re-saves
and per-run cost comes from what the gateway actually billed."""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage

from api.src.agent_conversations.models import (
    AgentConversation,
    extract_run_usage,
    save_agent_conversation,
)
from api.src.database.database import AsyncSessionFactory

MESSAGES = [ModelRequest(parts=[UserPromptPart(content="hi")])]


@pytest_asyncio.fixture
async def conv_id():
    cid = f"test_persist_{uuid.uuid4()}"
    yield cid
    async with AsyncSessionFactory() as s:
        row = await s.get(AgentConversation, cid)
        if row is not None:
            await s.delete(row)
            await s.commit()


async def _save(cid: str, **kwargs) -> None:
    async with AsyncSessionFactory() as s:
        await save_agent_conversation(s, cid, "sernia", MESSAGES, **kwargs)


async def _metadata(cid: str) -> dict | None:
    async with AsyncSessionFactory() as s:
        row = await s.get(AgentConversation, cid)
        return row.metadata_


@pytest.mark.asyncio
async def test_save_without_metadata_keeps_trigger_metadata(conv_id):
    """A web-chat turn or approval on a trigger-created conversation must not
    erase the SMS routing data the next approval's reply depends on."""
    trigger = {
        "trigger_source": "ai_sms_group",
        "trigger_phone": "+15550001111",
        "trigger_group_participants": ["+15550001111", "+15550002222"],
    }
    await _save(conv_id, metadata=trigger, modality="sms")
    await _save(conv_id, clerk_user_id="user_approver", estimated_tokens=10, cost_last_run=0.01)

    assert await _metadata(conv_id) == trigger


@pytest.mark.asyncio
async def test_new_metadata_merges_over_existing(conv_id):
    await _save(conv_id, metadata={"trigger_source": "ai_sms", "trigger_phone": "+15550001111"})
    await _save(conv_id, metadata={"trigger_source": "ai_sms", "seeded_from_tool": True})

    assert await _metadata(conv_id) == {
        "trigger_source": "ai_sms",
        "trigger_phone": "+15550001111",
        "seeded_from_tool": True,
    }


@pytest.mark.asyncio
async def test_failure_flags_do_not_outlive_the_failed_save(conv_id):
    await _save(conv_id, metadata={"trigger_source": "zillow_email_event"})
    await _save(conv_id, metadata={"partial": True, "error": True, "llm_error": True})
    assert (await _metadata(conv_id))["error"] is True
    assert (await _metadata(conv_id))["trigger_source"] == "zillow_email_event"

    await _save(conv_id)  # the retry succeeded
    assert await _metadata(conv_id) == {"trigger_source": "zillow_email_event"}


def _result(new_messages: list, *, input_tokens: int = 1000, output_tokens: int = 100):
    usage = RunUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(
        usage=usage,  # a property on AgentRunResult
        new_messages=lambda: new_messages,
        all_messages=lambda: new_messages,
    )


def test_cost_uses_gateway_reported_amount():
    """OpenRouter slugs have no genai-prices entry; the billed amount on each
    response is the only cost signal, summed across the run's requests."""
    responses = [
        ModelResponse(
            parts=[TextPart("a")],
            model_name="openai/gpt-5.6-luna",
            provider_details={"cost": 0.0125},
        ),
        ModelResponse(
            parts=[TextPart("b")],
            model_name="openai/gpt-5.6-luna",
            provider_details={"cost": 0.0075},
        ),
    ]
    tokens, cost = extract_run_usage(_result(responses))
    assert tokens == 1100
    assert cost == pytest.approx(0.02)


def test_cost_counts_the_byok_upstream_charge():
    """BYOK: OpenRouter's fee is $0 (so pydantic-ai stores no `cost`) and the
    real spend arrives as `upstream_inference_cost`. Production shape."""
    responses = [
        ModelResponse(
            parts=[TextPart("a")],
            model_name="openai/gpt-6-luna",
            provider_details={"upstream_inference_cost": 0.00652035, "is_byok": True},
        ),
        ModelResponse(
            parts=[TextPart("b")],
            model_name="openai/gpt-6-luna",
            provider_details={"cost": 0.0001, "upstream_inference_cost": 0.002, "is_byok": True},
        ),
    ]
    _, cost = extract_run_usage(_result(responses))
    assert cost == pytest.approx(0.00862)  # rounded to 6 dp, like stored costs


def test_cost_falls_back_to_price_table_when_gateway_reports_none():
    responses = [ModelResponse(parts=[TextPart("a")], model_name="gpt-4o-mini")]
    _, cost = extract_run_usage(_result(responses, input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost is not None and cost > 0


def test_usage_extraction_never_raises():
    assert extract_run_usage(SimpleNamespace()) == (None, None)
