"""Model selection, independent errors, shared inputs, and single dispatch."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.models.test import TestModel

from api.src.open_phone import escalate
from api.src.open_phone import escalation_assessment as assessment
from api.src.open_phone.escalation_context import EscalationHistory, HistoryMessage


@pytest.mark.asyncio
async def test_default_luna_receives_computed_context(monkeypatch):
    monkeypatch.delenv("ESCALATION_MODEL_MODE", raising=False)
    history = EscalationHistory(
        status="available",
        messages=[
            HistoryMessage(
                timestamp="2026-09-18T00:30:00-04:00",
                direction="outgoing",
                text="I understand water is pouring through the ceiling",
                same_sender=False,
            )
        ],
    )
    fetch = AsyncMock(return_value=history)
    monkeypatch.setattr(escalate, "fetch_escalation_history", fetch)
    luna = AsyncMock(return_value=assessment.ShouldEscalate(should_escalate=False, reason="Aware"))
    jev = AsyncMock(side_effect=AssertionError("Default must not invoke Jev"))
    monkeypatch.setattr(assessment, "assess_luna", luna)
    monkeypatch.setattr(assessment, "assess_jev", jev)
    result = await escalate.ai_assess_for_escalation(
        {"event_timestamp": "2026-09-18T05:00:00Z", "message_text": "Still leaking"}
    )
    assert result[0] is False
    state = luna.call_args.args[0]
    assert state["prior_messages"][0]["minutes_before_current_message"] == 30
    assert state["prior_messages"][0]["within_previous_30_minutes"] is True
    fetch.assert_awaited_once()
    jev.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "luna,jev,expected",
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
        (None, True, True),
        (True, None, True),
        (None, False, False),
        (False, None, False),
        (None, None, False),
    ],
)
async def test_either_truth_table_and_errors(monkeypatch, luna, jev, expected):
    def fake(value):
        return (
            AsyncMock(side_effect=RuntimeError("provider failed"))
            if value is None
            else AsyncMock(
                return_value=assessment.ShouldEscalate(should_escalate=value, reason="verdict")
            )
        )

    monkeypatch.setattr(assessment, "assess_luna", fake(luna))
    monkeypatch.setattr(assessment, "assess_jev", fake(jev))
    result = await assessment.assess_state({}, mode="either", max_retries=0)
    assert result.should_escalate is expected
    assert [r.should_escalate for r in result.assessments] == [luna, jev]
    assert [r.error_type is not None for r in result.assessments] == [luna is None, jev is None]


@pytest.mark.asyncio
async def test_both_models_start_concurrently_with_identical_state(monkeypatch):
    started = set()
    ready = asyncio.Event()
    state = {"message_text": "new damage"}

    async def fake(name, payload):
        assert payload is state
        started.add(name)
        if len(started) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=0.5)
        return assessment.ShouldEscalate(should_escalate=True, reason=name)

    monkeypatch.setattr(assessment, "assess_luna", lambda payload: fake("luna", payload))
    monkeypatch.setattr(assessment, "assess_jev", lambda payload: fake("jev", payload))
    result = await assessment.assess_state(state, mode="either", max_retries=0)
    assert result.should_escalate and all(r.error_type is None for r in result.assessments)


@pytest.mark.asyncio
async def test_timeout_of_one_model_preserves_other_positive(monkeypatch):
    async def slow(state):
        await asyncio.sleep(1)

    monkeypatch.setattr(assessment, "ESCALATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(assessment, "assess_jev", slow)
    monkeypatch.setattr(
        assessment,
        "assess_luna",
        AsyncMock(
            return_value=assessment.ShouldEscalate(should_escalate=True, reason="new danger")
        ),
    )
    result = await assessment.assess_state({}, mode="either", max_retries=0)
    assert result.should_escalate
    assert result.assessments[1].error_type == "TimeoutError"


@pytest.mark.asyncio
async def test_luna_structured_output_contract():
    with assessment.luna_agent().override(
        model=TestModel(custom_output_args={"should_escalate": True, "reason": "ceiling collapse"})
    ):
        result = await assessment.assess_luna({"message_text": "part of ceiling fell"})
    assert result.should_escalate and result.reason == "ceiling collapse"


@pytest.mark.asyncio
async def test_either_dispatches_only_once(monkeypatch):
    monkeypatch.setenv("ESCALATION_MODEL_MODE", "either")
    monkeypatch.setattr(
        escalate,
        "fetch_escalation_history",
        AsyncMock(return_value=EscalationHistory(status="unavailable")),
    )
    for name in ("assess_luna", "assess_jev"):
        monkeypatch.setattr(
            assessment,
            name,
            AsyncMock(
                return_value=assessment.ShouldEscalate(should_escalate=True, reason="urgent")
            ),
        )
    dispatch = AsyncMock(return_value=1)
    monkeypatch.setattr(escalate, "trigger_twilio_escalation", dispatch)
    await escalate.analyze_for_twilio_escalation(
        {
            "event_timestamp": "2026-09-18T05:00:00Z",
            "message_text": "Fire",
            "from_number": "+15555550111",
        },
        escalate_to_numbers=["+15555550222"],
    )
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_mode_does_not_silently_enable_both():
    with pytest.raises(ValueError, match="ESCALATION_MODEL_MODE"):
        await assessment.assess_state({}, mode="typo")
