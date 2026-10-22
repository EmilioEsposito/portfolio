"""Synthetic rules only: no real contact identities or operator context."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from api.src.open_phone import escalate
from api.src.open_phone import escalation_delivery as delivery
from api.src.open_phone.escalation_context import EscalationHistory
from api.src.open_phone.escalation_suppression import VARIABLE, should_suppress

NUMBERS = ["+15555550101", "+15555550102"]
RULE = {
    "from_numbers": NUMBERS,
    "match_terms": ["routine topic", "known concern"],
    "bypass_terms": ["separate emergency", "active flooding"],
}


@pytest.fixture(autouse=True)
def private_rules(monkeypatch):
    monkeypatch.setenv(VARIABLE, json.dumps([RULE]))


def event(text="Routine topic again, URGENT!", number=NUMBERS[0]):
    return {
        "from_number": number,
        "message_text": text,
        "event_timestamp": "2026-10-22T05:00:00Z",
        "event_id": "synthetic:" + str(uuid4()),
    }


@pytest.mark.parametrize("number", [*NUMBERS, "(555) 555-0101", "1-555-555-0102"])
def test_both_numbers_and_formatting(number):
    assert should_suppress(event("ROUTINE-topic again", number))


@pytest.mark.parametrize(
    "text,number",
    [
        ("Routine topic", "+15555550103"),
        ("Routine topic", ""),
        ("Routine topic", "5555550101 ext 2"),
        ("Routine topic", "+15555550101,+15555550102"),
        ("Unrelated urgent event", NUMBERS[0]),
        ("Routine topical discussion", NUMBERS[0]),
        ("Routine topic AND active flooding", NUMBERS[0]),
        ("Known concern but a separate emergency", NUMBERS[1]),
        (None, NUMBERS[0]),
    ],
)
def test_preserves_other_senders_topics_and_bypass(text, number):
    assert not should_suppress(event(text, number))


def test_history_does_not_trigger_suppression():
    incoming = event("Something else happened")
    incoming["prior_messages"] = [{"text": "routine topic"}]
    assert not should_suppress(incoming)


@pytest.mark.parametrize("value", ["", "[]"])
def test_disabled(value, monkeypatch):
    monkeypatch.setenv(VARIABLE, value)
    assert not should_suppress(event())
    monkeypatch.delenv(VARIABLE)
    assert not should_suppress(event())


@pytest.mark.parametrize("value", ["private-broken-json", "{}", "[{}]", '[{"secret":"private"}]'])
def test_invalid_config_has_fixed_diagnostic(value, monkeypatch):
    monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(ValueError) as exc:
        should_suppress(event())
    assert str(exc.value) == f"Invalid {VARIABLE}; values withheld"


def test_bypass_wins_across_overlapping_rules(monkeypatch):
    second = {**RULE, "bypass_terms": ["different hazard"]}
    monkeypatch.setenv(VARIABLE, json.dumps([RULE, second]))
    assert not should_suppress(event("routine topic and different hazard"))


@pytest.mark.asyncio
async def test_direct_path_never_calls_model_history_or_twilio(monkeypatch):
    history, model, send = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(escalate, "fetch_escalation_history", history)
    monkeypatch.setattr(escalate, "assess_state", model)
    monkeypatch.setattr(escalate, "trigger_twilio_escalation", send)
    for number in NUMBERS:
        assert await escalate.analyze_for_twilio_escalation(event(number=number)) == 0
    history.assert_not_awaited()
    model.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_reaches_normal_assessment(monkeypatch):
    from api.src.open_phone.escalation_assessment import EscalationDecision

    history = AsyncMock(return_value=EscalationHistory(status="unavailable"))
    model = AsyncMock(
        return_value=EscalationDecision(should_escalate=True, reason="urgent", assessments=[])
    )
    monkeypatch.setattr(escalate, "fetch_escalation_history", history)
    monkeypatch.setattr(escalate, "assess_state", model)
    assert (await escalate.ai_assess_for_escalation(event("routine topic and active flooding")))[0]
    model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("number", NUMBERS)
async def test_durable_path_skips_without_delivery_and_stays_skipped(monkeypatch, number):
    from api.src.database.database import AsyncSessionFactory
    from api.src.open_phone.models import EscalationRequest

    history, send = AsyncMock(), AsyncMock()
    monkeypatch.setattr(escalate, "fetch_escalation_history", history)
    monkeypatch.setattr(delivery, "process_deliveries", send)
    async with AsyncSessionFactory() as session, session.begin():
        request_id = await delivery.enqueue_assessment(session, event(number=number))
    try:
        await delivery.process_request(request_id)
        await delivery.process_request(request_id)
        async with AsyncSessionFactory() as session:
            row = await session.get(EscalationRequest, request_id)
            assert row.state == "skipped"
            assert row.attempts == 1
        assert (await delivery.report(request_id)).recipients == {}
        history.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        async with AsyncSessionFactory() as session, session.begin():
            await session.execute(
                delete(EscalationRequest).where(EscalationRequest.id == request_id)
            )
