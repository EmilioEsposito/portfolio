"""FastAPI routes that showcase a Pydantic AI powered founder assistant."""

from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from typing import Iterable

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

router = APIRouter(prefix="/pydantic-ai", tags=["pydantic-ai"])


class ExecutionWindow(BaseModel):
    """Represents the build cadence suggested for the idea."""

    sprint_weeks: int = Field(..., ge=2, le=52, description="Length of the focused build sprint")
    cadence: str = Field(..., description="How the core team works through the sprint")
    estimated_cost: float = Field(..., ge=0, description="Estimated investment in USD")
    runway_months: float = Field(..., ge=0, description="How many months of runway the plan assumes")
    notes: str = Field(..., description="Rationale behind the projection")


class OpportunityBlueprint(BaseModel):
    """Curated snapshot of the product opportunity."""

    working_title: str
    north_star: str
    elevator_pitch: str
    audience: list[str]
    signature_experiences: list[str]
    launch_milestones: list[str]
    success_metrics: list[str]
    execution_window: ExecutionWindow


class BlueprintSeed(BaseModel):
    """Information the agent collects before sketching the blueprint."""

    idea: str = Field(..., description="The user's product idea or problem statement")
    audience: list[str] = Field(default_factory=list, description="Audience segments mentioned by the user")
    differentiators: list[str] = Field(default_factory=list, description="Reasons the product will stand out")
    success_definition: str | None = Field(
        default=None,
        description="What success looks like for the user if explicitly mentioned.",
    )


class DeliveryInputs(BaseModel):
    """Parameters used to project timeline and investment."""

    monthly_budget: float | None = Field(default=None, ge=0)
    team_size: int | None = Field(default=None, ge=1, le=50)
    target_launch_weeks: int | None = Field(default=None, ge=2, le=52)
    assets: list[str] = Field(default_factory=list)


def _build_model() -> OpenAIModel | FunctionModel:
    """Return the model that powers the agent, falling back to a local simulation."""

    if os.getenv("OPENAI_API_KEY"):
        model_name = os.getenv("PYDANTIC_AI_MODEL", "gpt-4o-mini")
        return OpenAIModel(model_name)

    return _offline_model()


def _offline_model() -> FunctionModel:
    """Deterministic FunctionModel used when no provider keys are available."""

    async def _function(messages: list[ModelMessage], _info) -> ModelResponse:
        blueprint = _build_blueprint(BlueprintSeed(idea=_extract_user_text(messages)))
        summary = _format_blueprint_summary(blueprint)
        return ModelResponse(parts=[TextPart(content=summary)], model_name="offline-blueprint")

    async def _stream(messages: list[ModelMessage], _info):
        blueprint = _build_blueprint(BlueprintSeed(idea=_extract_user_text(messages)))
        summary = _format_blueprint_summary(blueprint)
        for sentence in summary.split(" "):
            yield sentence + " "

    # Provide both sync and streaming behaviour for compatibility.
    return FunctionModel(function=_function, stream_function=_stream, model_name="offline-blueprint")


def _extract_user_text(messages: Iterable[ModelMessage]) -> str:
    """Return the latest user utterance from the conversation."""

    last_user_text: str | None = None
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    last_user_text = part.content
    return (last_user_text or "an experimental AI copilot").strip()


def _format_blueprint_summary(blueprint: OpportunityBlueprint) -> str:
    """Create a concise textual summary for the offline model."""

    headline = f"{blueprint.working_title}: {blueprint.north_star}"
    experiences = ", ".join(blueprint.signature_experiences[:3])
    metric = blueprint.success_metrics[0]
    cadence = blueprint.execution_window.cadence
    cost = f"${blueprint.execution_window.estimated_cost:,.0f}"
    return (
        f"{headline}. Key experience pillars: {experiences}. We measure progress through {metric}. "
        f"Plan: {cadence} with an estimated investment of {cost}."
    )


@lru_cache(maxsize=1)
def get_portfolio_agent() -> Agent[None]:
    """Create the reusable agent instance."""

    model = _build_model()
    agent = Agent(
        model=model,
        system_prompt=(
            "You are Portfolio Forge, a pragmatic founder assistant."
            " Collect the user's idea, target audience, differentiators, and definitions of success."
            " Always call `sketch_blueprint` once you understand the idea to craft a launch blueprint."
            " When the user provides numbers about time, budget, or team, call `estimate_delivery` to ground the plan."
            " After tools run, narrate the highlights conversationally and suggest actionable next steps."
        ),
    )

    @agent.tool
    async def sketch_blueprint(_ctx: RunContext[None], seed: BlueprintSeed) -> OpportunityBlueprint:
        return _build_blueprint(seed)

    @agent.tool
    async def estimate_delivery(_ctx: RunContext[None], inputs: DeliveryInputs) -> ExecutionWindow:
        return _estimate_execution(inputs)

    return agent


def _build_blueprint(seed: BlueprintSeed) -> OpportunityBlueprint:
    """Generate a deterministic blueprint that the model can elaborate on."""

    idea = seed.idea.strip() or "a resilient AI product" 
    title = _title_from_idea(idea)
    personas = seed.audience or _derive_personas(idea)
    differentiators = seed.differentiators or _derive_differentiators(idea)
    experiences = _derive_experiences(idea, differentiators)
    milestones = _derive_milestones(experiences, seed.success_definition)
    metrics = _derive_metrics(personas, title)
    execution = _estimate_execution(DeliveryInputs(target_launch_weeks=12, assets=differentiators))

    north_star = _build_north_star(idea, personas)
    elevator = _build_elevator_pitch(idea, personas, differentiators, experiences)

    return OpportunityBlueprint(
        working_title=title,
        north_star=north_star,
        elevator_pitch=elevator,
        audience=personas,
        signature_experiences=experiences,
        launch_milestones=milestones,
        success_metrics=metrics,
        execution_window=execution,
    )


def _estimate_execution(inputs: DeliveryInputs) -> ExecutionWindow:
    """Estimate a build runway using lightweight heuristics."""

    team = inputs.team_size or 3
    sprint_weeks = max(6, min(inputs.target_launch_weeks or 10, 26))
    monthly_burn = inputs.monthly_budget or (team * 9500)
    estimated_cost = monthly_burn * (sprint_weeks / 4)
    runway_months = max(2.0, round((estimated_cost / monthly_burn) + 0.5, 1))
    cadence = f"{team}-person crew shipping in {math.ceil(sprint_weeks / 2)} two-week sprints"

    asset_note = " + leveraging existing assets" if inputs.assets else ""
    notes = (
        f"Assumes {team} dedicated builders{asset_note}."
        f" Burn is estimated at ${monthly_burn:,.0f} per month."
    )

    return ExecutionWindow(
        sprint_weeks=sprint_weeks,
        cadence=cadence,
        estimated_cost=round(estimated_cost, 2),
        runway_months=runway_months,
        notes=notes,
    )


def _title_from_idea(idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", idea)
    if not words:
        return "Portfolio Blueprint"
    if len(words) == 1:
        return words[0].title() + " Studio"
    first = " ".join(words[:4])
    return first.title()


def _derive_personas(idea: str) -> list[str]:
    text = idea.lower()
    personas: list[str] = []
    mapping = {
        "founder": "Early-stage founders shipping fast",
        "realtor": "Real-estate operators hungry for automation",
        "student": "Students building their first portfolio projects",
        "engineer": "Full-stack engineers who crave rapid validation",
        "designer": "Product designers obsessed with crafted workflows",
        "community": "Community leads balancing growth and trust",
        "agent": "Operators experimenting with AI copilots",
    }
    for keyword, label in mapping.items():
        if keyword in text:
            personas.append(label)
    if not personas:
        personas.append("Indie builders validating a new concept")
    return personas[:4]


def _derive_differentiators(idea: str) -> list[str]:
    base = [
        "Founder-led discovery rituals",
        "Telemetry that respects user intent",
    ]
    text = idea.lower()
    if "agent" in text or "automation" in text:
        base.append("Human-in-the-loop agent handoffs")
    if "community" in text:
        base.append("Peer accountability loops")
    if "journal" in text or "reflection" in text:
        base.append("Moment-to-moment mood capture")
    return base[:4]


def _derive_experiences(idea: str, differentiators: list[str]) -> list[str]:
    text = idea.lower()
    experiences = [
        "Zero-to-one discovery workshop with founder playbooks",
        "Progress pulse dashboard that surfaces momentum trends",
    ]
    if "community" in text:
        experiences.append("Member signals that trigger curated check-ins")
    if "journal" in text or "wellness" in text:
        experiences.append("Daily reflection prompts tuned to sentiment shifts")
    if "sales" in text or "pipeline" in text:
        experiences.append("Deal-flow prioritization assistant")
    if "education" in text or "course" in text:
        experiences.append("Micro-lesson generator with real-world practice missions")
    for diff in differentiators:
        if diff not in experiences:
            experiences.append(diff)
    return experiences[:5]


def _derive_milestones(experiences: list[str], success_definition: str | None) -> list[str]:
    goal = success_definition or "prove the riskiest assumption with real users"
    return [
        f"Weeks 1-2: Founder interviews + sharpen thesis to {goal}.",
        f"Weeks 3-4: Prototype {experiences[0].lower()} with instrumentation.",
        f"Weeks 5-6: Run a concierge pilot around {experiences[1].lower()}.",
        "Weeks 7-8: Launch invite-only beta and gather decision-quality feedback.",
    ]


def _derive_metrics(personas: list[str], title: str) -> list[str]:
    primary_persona = personas[0] if personas else "early adopters"
    return [
        f"Onboard the first 30 {primary_persona} into the {title} beta.",
        "Hit 45% weekly product activation by week eight.",
        "Collect 10 narrative case studies that prove the value arc.",
    ]


def _build_north_star(idea: str, personas: list[str]) -> str:
    persona_text = personas[0] if personas else "founders"
    return f"Help {persona_text.lower()} turn {idea.lower()} into measurable traction."


def _build_elevator_pitch(
    idea: str, personas: list[str], differentiators: list[str], experiences: list[str]
) -> str:
    persona_text = personas[0] if personas else "ambitious builders"
    differentiator = differentiators[0] if differentiators else "fast learning loops"
    highlight = experiences[0] if experiences else "evidence-based experiments"
    return (
        f"Designed for {persona_text.lower()}, it transforms the vision of {idea.lower()}"
        f" into traction by combining {differentiator.lower()} and {highlight.lower()}."
    )


@router.post("/portfolio")
async def run_portfolio_chat(request: Request):
    """Stream responses from the Portfolio Forge agent."""

    agent = get_portfolio_agent()
    return await VercelAIAdapter.dispatch_request(request, agent=agent)
