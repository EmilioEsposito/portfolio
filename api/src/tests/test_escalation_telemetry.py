"""Assert the exported comparison fields and actual parent/child trace structure."""

import json
from unittest.mock import AsyncMock

import pytest
from logfire.testing import capfire as capfire

from api.src.open_phone import escalation_assessment as assessment


@pytest.mark.asyncio
async def test_paired_verdicts_usage_and_correlation_are_exported(capfire, monkeypatch):
    for model, vote, cost in [("luna", True, 0.001), ("jev", False, 0.0001)]:
        monkeypatch.setattr(
            assessment,
            f"assess_{model}",
            AsyncMock(
                return_value=assessment.InferenceResult(
                    should_escalate=vote,
                    reason="synthetic decision",
                    provider_model=model,
                    input_tokens=123,
                    output_tokens=12,
                    reported_cost=cost,
                )
            ),
        )
    decision = await assessment.assess_state(
        {"message_text": "synthetic"},
        mode="either",
        event_id="synthetic-event",
        sample_kind="verification",
    )
    spans = [
        s
        for s in capfire.exporter.exported_spans
        if s.attributes.get("logfire.span_type") == "span"
    ]
    root = next(s for s in spans if s.name == "Escalation assessment")
    models = [s for s in spans if s.name == "Escalation model assessment"]
    assert len(models) == 2
    assert {s.attributes["model"] for s in models} == {"luna", "jev"}
    assert all(s.parent.span_id == root.context.span_id for s in models)
    assert all(s.attributes["assessment_id"] == root.attributes["assessment_id"] for s in models)
    assert root.attributes["escalation.sample_kind"] == "verification"
    assert root.attributes["escalation.event_id"] == "synthetic-event"
    assert root.attributes["escalation.comparable"] is True
    assert root.attributes["escalation.agreement"] is False
    assert root.attributes["escalation.luna_verdict"] is True
    assert root.attributes["escalation.jev_verdict"] is False
    assert root.attributes["escalation.final_verdict"] is True
    assert root.attributes["escalation.error_count"] == 0
    assert len(root.attributes["escalation.input_sha256"]) == 64
    assert len(root.attributes["escalation.policy_sha256"]) == 64
    for span in models:
        output = json.loads(span.attributes["escalation.output"])
        usage = json.loads(span.attributes["escalation.usage"])
        assert output["attempts"] == 1
        assert output["latency_seconds"] >= 0
        assert usage["input_tokens"] == 123 and usage["reported_cost"] > 0
        assert "operation.cost" not in span.attributes  # no double-counted native provider cost
    assert decision.should_escalate


@pytest.mark.asyncio
async def test_failed_model_is_not_a_disagreement(capfire, monkeypatch):
    monkeypatch.setattr(
        assessment,
        "assess_luna",
        AsyncMock(return_value=assessment.ShouldEscalate(should_escalate=True, reason="danger")),
    )
    monkeypatch.setattr(assessment, "assess_jev", AsyncMock(side_effect=TimeoutError()))
    result = await assessment.assess_state({}, mode="either", max_retries=1)
    spans = [
        s
        for s in capfire.exporter.exported_spans
        if s.attributes.get("logfire.span_type") == "span"
    ]
    root = next(s for s in spans if s.name == "Escalation assessment")
    jev = next(
        s
        for s in spans
        if s.name == "Escalation model assessment" and s.attributes["model"] == "jev"
    )
    assert root.attributes["escalation.comparable"] is False
    assert root.attributes.get("escalation.agreement") not in (True, False)
    assert root.attributes["escalation.error_count"] == 1
    assert root.attributes["escalation.final_verdict"] is True
    assert jev.attributes["escalation.status"] == "error"
    output = json.loads(jev.attributes["escalation.output"])
    assert output["should_escalate"] is None
    assert output["attempts"] == 2 and output["reported_cost"] is None
    assert result.should_escalate


def test_luna_adapter_keeps_explicit_gateway_token_counts():
    from pydantic_ai.models.openrouter import _OpenRouterChatCompletion

    response = _OpenRouterChatCompletion.model_validate(
        {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 0,
            "model": assessment.LUNA_MODEL,
            "provider": "OpenAI",
            "choices": [],
            "usage": {
                "prompt_tokens": 1234,
                "completion_tokens": 56,
                "total_tokens": 1290,
                "cost": 0.0007,
                "prompt_tokens_details": {"cached_tokens": 1000},
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        }
    )
    usage = assessment.EscalationLunaModel(assessment.LUNA_MODEL)._map_usage(response)
    assert usage.details["token_counts_reported"] == 1
    assert usage.input_tokens == 1234
    assert usage.output_tokens == 56
    assert usage.cache_read_tokens == 1000
    assert usage.details["reasoning_tokens"] == 12


def test_luna_adapter_marks_missing_usage_unknown():
    from pydantic_ai.models.openrouter import _OpenRouterChatCompletion

    response = _OpenRouterChatCompletion.model_validate(
        {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 0,
            "model": assessment.LUNA_MODEL,
            "provider": "OpenAI",
            "choices": [],
        }
    )
    usage = assessment.EscalationLunaModel(assessment.LUNA_MODEL)._map_usage(response)
    assert usage.details["token_counts_reported"] == 0
