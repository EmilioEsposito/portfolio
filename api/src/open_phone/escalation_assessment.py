"""Side-effect-free classifiers and an explicit, single-decision combination rule.

luna (default): Luna alone decides.
jev: Jev alone decides (experiments).
either: run both on identical state; any successful positive verdict wins.
Errors are recorded separately and never become positive votes. No SMS or calls here.
"""

import asyncio
import hashlib
import json
from functools import cache
from time import perf_counter
from typing import Literal
from uuid import uuid4

import logfire
from openai.types.chat import ChatCompletion
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RequestUsage

from api.src.open_phone.escalation_policy import ESCALATION_QUESTION, ESCALATION_TIMEOUT_SECONDS
from api.src.open_phone.typesafe_openrouter import (
    OpenRouterDecisionResponse,
    create_jev_client,
)
from api.src.sernia_ai.model_config import SerniaOpenRouterModel, build_openrouter_settings

LUNA_MODEL = "openai/gpt-5.6-luna"
ModelName = Literal["luna", "jev"]


class ShouldEscalate(BaseModel):
    """Structured verdict returned by the escalation-assessment agent."""

    should_escalate: bool
    reason: str


class InferenceResult(ShouldEscalate):
    """Provider metadata outside the LLM output schema; None means not reported."""

    provider_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reported_cost: float | None = None
    probabilities: dict[str, float] | None = None
    confidence: float | None = None


class ModelAssessment(InferenceResult):
    model: ModelName
    should_escalate: bool | None  # None means failure, never a negative model prediction.
    reason: str
    error_type: str | None = None
    latency_seconds: float = 0
    attempts: int = 0


class EscalationDecision(BaseModel):
    should_escalate: bool
    reason: str
    assessments: list[ModelAssessment]


class EscalationLunaModel(SerniaOpenRouterModel):
    """Preserve explicit gateway token counts for this otherwise-unrecognized model.

    The pinned SDK's pricing-based usage extractor loses Luna's standard chat
    token totals. Read the actual response fields; do not estimate usage or cost.
    This adapter is limited to the escalation classifier's non-streaming calls.
    """

    def _map_usage(self, response: ChatCompletion) -> RequestUsage:
        usage = super()._map_usage(response)
        usage.details["token_counts_reported"] = int(response.usage is not None)
        if raw := response.usage:
            usage.input_tokens = raw.prompt_tokens
            usage.output_tokens = raw.completion_tokens
            if raw.prompt_tokens_details and raw.prompt_tokens_details.cached_tokens is not None:
                usage.cache_read_tokens = raw.prompt_tokens_details.cached_tokens
        return usage


@cache
def luna_agent() -> Agent:
    settings = build_openrouter_settings("low")
    settings["timeout"] = ESCALATION_TIMEOUT_SECONDS
    return Agent(
        EscalationLunaModel(LUNA_MODEL),
        output_type=ShouldEscalate,
        instructions=ESCALATION_QUESTION.instructions,
        model_settings=settings,
        retries=0,  # Retry ownership belongs to assess_model.
        name="escalation_assessor",
    )


async def assess_luna(state: dict) -> InferenceResult:
    result = await luna_agent().run(json.dumps(state))
    response = [m for m in result.all_messages() if m.kind == "response"][-1]
    usage = result.usage
    return InferenceResult(
        **result.output.model_dump(),
        provider_model=response.model_name,
        input_tokens=usage.input_tokens if usage.details.get("token_counts_reported") else None,
        output_tokens=usage.output_tokens if usage.details.get("token_counts_reported") else None,
        reported_cost=(response.provider_details or {}).get("cost"),
    )


async def assess_jev(state: dict) -> InferenceResult:
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
    return InferenceResult(
        provider_model=result.model,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        reported_cost=result.usage.cost,
        probabilities=verdict.probabilities,
        confidence=verdict.confidence,
        should_escalate=verdict.choice == "escalate",
        reason=f"Jev decision: {verdict.choice}; P(escalate)={verdict.probabilities['escalate']:.3f}; "
        f"confidence={verdict.confidence:.3f} (decision summary, not an explanation)",
    )


async def assess_model(
    model: ModelName, state: dict, max_retries: int, *, assessment_id: str = ""
) -> ModelAssessment:
    """One model span per sample, separate retry spans, no errors counted as negatives."""
    run = assess_luna if model == "luna" else assess_jev
    started = perf_counter()
    error_type = "NoAttempt"
    result = None
    with logfire.span(
        "Escalation model assessment", model=model, assessment_id=assessment_id
    ) as model_span:
        for attempt in range(max_retries + 1):
            with logfire.span(
                "Escalation model attempt", model=model, attempt=attempt + 1
            ) as attempt_span:
                try:
                    async with asyncio.timeout(ESCALATION_TIMEOUT_SECONDS):
                        verdict = await run(state)
                    result = ModelAssessment(
                        model=model,
                        **verdict.model_dump(),
                        latency_seconds=perf_counter() - started,
                        attempts=attempt + 1,
                    )
                    attempt_span.set_attribute("escalation.status", "ok")
                    break
                except Exception as exc:
                    error_type = type(exc).__name__
                    attempt_span.set_attribute("escalation.status", "error")
                    attempt_span.set_attribute("escalation.error_type", error_type)
                    logfire.warn(
                        "Escalation model attempt failed",
                        model=model,
                        attempt=attempt + 1,
                        error_type=error_type,
                    )
        if result is None:
            result = ModelAssessment(
                model=model,
                should_escalate=None,
                reason="AI assessment failed",
                error_type=error_type,
                latency_seconds=perf_counter() - started,
                attempts=max_retries + 1,
            )
        model_span.set_attribute("escalation.output", result.model_dump())
        model_span.set_attribute("escalation.status", "error" if result.error_type else "ok")
        model_span.set_attribute("escalation.verdict", result.should_escalate)
        model_span.set_attribute("escalation.latency_seconds", result.latency_seconds)
        # Use a separate namespace: native Luna spans and the Jev details log
        # already own operation.cost, so dashboards must not count it twice.
        model_span.set_attribute(
            "escalation.usage",
            {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "reported_cost": result.reported_cost,
                "scope": "successful_attempt" if not result.error_type else "unavailable",
            },
        )
        return result


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
    state: dict,
    *,
    mode: str = "luna",
    max_retries: int = 1,
    event_id: str | None = None,
    sample_kind: Literal["production", "verification", "eval"] = "production",
) -> EscalationDecision:
    """Build one result. In either mode wait for both bounded assessments to complete."""
    if mode not in {"luna", "jev", "either"}:
        raise ValueError("ESCALATION_MODEL_MODE must be luna, jev, or either")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    models: list[ModelName] = ["luna", "jev"] if mode == "either" else [mode]
    assessment_id = str(uuid4())
    question = {
        "instructions": ESCALATION_QUESTION.instructions,
        "criteria": ESCALATION_QUESTION.criteria,
    }
    with logfire.span("Escalation assessment", mode=mode, assessment_id=assessment_id) as span:
        span.set_attribute("escalation.event_id", event_id)
        span.set_attribute("escalation.sample_kind", sample_kind)
        span.set_attribute(
            "escalation.input_sha256",
            hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
        )
        span.set_attribute(
            "escalation.policy_sha256",
            hashlib.sha256(json.dumps(question, sort_keys=True).encode()).hexdigest(),
        )
        span.set_attribute("escalation.input", state)
        span.set_attribute(
            "escalation.question",
            {
                "instructions": ESCALATION_QUESTION.instructions,
                "criteria": ESCALATION_QUESTION.criteria,
            },
        )
        assessments = await asyncio.gather(
            *(
                assess_model(model, state, max_retries, assessment_id=assessment_id)
                for model in models
            )
        )
        decision = combine_assessments(assessments)
        span.set_attribute("escalation.result", decision.model_dump())
        by_model = {r.model: r for r in assessments}
        comparable = mode == "either" and all(r.should_escalate is not None for r in assessments)
        span.set_attribute("escalation.comparable", comparable)
        span.set_attribute(
            "escalation.agreement",
            assessments[0].should_escalate == assessments[1].should_escalate
            if comparable
            else None,
        )
        span.set_attribute(
            "escalation.error_count", sum(r.error_type is not None for r in assessments)
        )
        for model in ("luna", "jev"):
            span.set_attribute(
                f"escalation.{model}_verdict",
                by_model[model].should_escalate if model in by_model else None,
            )
        span.set_attribute("escalation.final_verdict", decision.should_escalate)
        return decision
