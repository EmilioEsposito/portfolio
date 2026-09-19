"""Side-effect-free classifiers and an explicit, single-decision combination rule.

luna (default): Luna alone decides.
jev: Jev alone decides (experiments).
either: run both on identical state; any successful positive verdict wins.
Errors are recorded separately and never become positive votes. No SMS or calls here.
"""

import asyncio
import json
from functools import cache
from typing import Literal

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent

from api.src.open_phone.escalation_policy import ESCALATION_QUESTION, ESCALATION_TIMEOUT_SECONDS
from api.src.open_phone.typesafe_openrouter import (
    OpenRouterDecisionResponse,
    create_jev_client,
)
from api.src.sernia_ai.model_config import build_openrouter_settings, resolve_model

LUNA_MODEL = "openai/gpt-5.6-luna"
ModelName = Literal["luna", "jev"]


class ShouldEscalate(BaseModel):
    """Structured verdict returned by the escalation-assessment agent."""

    should_escalate: bool
    reason: str


class ModelAssessment(BaseModel):
    model: ModelName
    should_escalate: bool | None  # None means failure, never a negative model prediction.
    reason: str
    error_type: str | None = None


class EscalationDecision(BaseModel):
    should_escalate: bool
    reason: str
    assessments: list[ModelAssessment]


@cache
def luna_agent() -> Agent:
    settings = build_openrouter_settings("low")
    settings["timeout"] = ESCALATION_TIMEOUT_SECONDS
    return Agent(
        resolve_model(f"openrouter:{LUNA_MODEL}"),
        output_type=ShouldEscalate,
        instructions=ESCALATION_QUESTION.instructions,
        model_settings=settings,
        retries=0,  # Retry ownership belongs to assess_model.
        name="escalation_assessor",
    )


async def assess_luna(state: dict) -> ShouldEscalate:
    result = await luna_agent().run(json.dumps(state))
    return result.output


async def assess_jev(state: dict) -> ShouldEscalate:
    async with create_jev_client(timeout=ESCALATION_TIMEOUT_SECONDS) as client:
        result = await client.system_one(
            state=state,
            questions={"escalation": ESCALATION_QUESTION},
            response_model=OpenRouterDecisionResponse,
        )
    verdict = result.choices["escalation"]
    if verdict.choice not in ESCALATION_QUESTION.criteria:
        raise ValueError("Jev returned an unknown escalation choice")
    logfire.info(
        "Jev decision details",
        model=result.model,
        **{
            "operation.cost": result.usage.cost,
            "gen_ai.usage.input_tokens": result.usage.input_tokens,
            "gen_ai.usage.output_tokens": result.usage.output_tokens,
        },
        probabilities=verdict.probabilities,
        confidence=verdict.confidence,
    )
    return ShouldEscalate(
        should_escalate=verdict.choice == "escalate",
        reason=f"Jev decision: {verdict.choice}; P(escalate)={verdict.probabilities['escalate']:.3f}; "
        f"confidence={verdict.confidence:.3f} (decision summary, not an explanation)",
    )


async def assess_model(model: ModelName, state: dict, max_retries: int) -> ModelAssessment:
    """Bound each provider attempt and preserve failures independently of verdicts."""
    run = assess_luna if model == "luna" else assess_jev
    error_type = "NoAttempt"
    for attempt in range(max_retries + 1):
        try:
            with logfire.span("Escalation model assessment", model=model, attempt=attempt):
                async with asyncio.timeout(ESCALATION_TIMEOUT_SECONDS):
                    verdict = await run(state)
                return ModelAssessment(model=model, **verdict.model_dump())
        except Exception as exc:
            error_type = type(exc).__name__
            logfire.warn(
                "Escalation model attempt failed",
                model=model,
                attempt=attempt,
                error_type=error_type,
            )
    return ModelAssessment(
        model=model, should_escalate=None, reason="AI assessment failed", error_type=error_type
    )


def combine_assessments(assessments: list[ModelAssessment]) -> EscalationDecision:
    """OR successful votes, retaining errors; downstream dispatch consumes this once."""
    positives = [r for r in assessments if r.should_escalate is True]
    details = "; ".join(f"{r.model}: {r.reason}" for r in assessments)
    if all(r.should_escalate is None for r in assessments):
        details = "AI assessment failed: " + details
    return EscalationDecision(
        should_escalate=bool(positives), reason=details, assessments=assessments
    )


async def assess_state(
    state: dict, *, mode: str = "luna", max_retries: int = 1
) -> EscalationDecision:
    """Build one result. In either mode wait for both bounded assessments to complete."""
    if mode not in {"luna", "jev", "either"}:
        raise ValueError("ESCALATION_MODEL_MODE must be luna, jev, or either")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    models: list[ModelName] = ["luna", "jev"] if mode == "either" else [mode]
    with logfire.span("Escalation assessment", mode=mode) as span:
        span.set_attribute("escalation.input", state)
        span.set_attribute(
            "escalation.question",
            {
                "instructions": ESCALATION_QUESTION.instructions,
                "criteria": ESCALATION_QUESTION.criteria,
            },
        )
        assessments = await asyncio.gather(
            *(assess_model(model, state, max_retries) for model in models)
        )
        decision = combine_assessments(assessments)
        span.set_attribute("escalation.result", decision.model_dump())
        return decision
