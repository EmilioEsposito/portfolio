"""The reply decision: one model call, no tools, structured output.

``decide(case)`` is the unit the evals measure. Its instructions are
``policy.md`` in this package, so the policy in force for any run is the
commit the code was at.
"""

from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from api.src.sernia_ai.config import MAIN_AGENT_MODEL
from api.src.sernia_ai.model_config import (
    DEFAULT_THINKING_EFFORT,
    build_openrouter_settings,
    resolve_model,
)
from api.src.sernia_ai.zillow_leads.case import LeadCase, render_case

POLICY_PATH = Path(__file__).with_name("policy.md")


class LeadFacts(BaseModel):
    """What the thread says about the lead. Empty where it does not say."""

    name: str | None = None
    phone: str | None = None
    unit: str | None = Field(
        None, description="The unit they asked about, as the listing names it."
    )
    credit_score: int | None = None
    pets: str | None = Field(None, description="Pets as stated, e.g. 'one cat', 'dog', 'none'.")
    move_in: str | None = Field(None, description="Desired move-in date or timing, as stated.")


class TourState(BaseModel):
    """Where tour scheduling stands after this message."""

    status: Literal["none", "proposed", "confirmed", "rescheduled", "cancelled"] = "none"
    start: datetime | None = Field(
        None, description="The agreed time, with UTC offset. Only when confirmed or rescheduled."
    )
    virtual: bool = False


class LeadDecision(BaseModel):
    """What to do about a lead thread right now."""

    lead: LeadFacts
    tour: TourState
    action: Literal["reply", "no_reply", "escalate"]
    reason: str = Field(description="One or two sentences on why this action.")
    reply_body: str | None = Field(
        None, description="The full plain-text email, signature included. Only when replying."
    )
    offered_slots: list[datetime] = Field(
        default_factory=list,
        description="Every tour time the reply offers, with UTC offset, copied from the case.",
    )
    team_notes: list[str] = Field(
        default_factory=list, description="Things a person must do by hand."
    )
    question: str | None = Field(
        None, description="What you need to know to proceed. Only when escalating."
    )

    @model_validator(mode="after")
    def _action_has_its_payload(self) -> "LeadDecision":
        if self.action == "reply" and not (self.reply_body or "").strip():
            raise ValueError("action 'reply' needs reply_body")
        if self.action != "reply" and self.reply_body:
            raise ValueError(f"action '{self.action}' must not carry reply_body")
        if self.action == "escalate" and not (self.question or "").strip():
            raise ValueError("action 'escalate' needs question")
        return self


def load_policy() -> str:
    return POLICY_PATH.read_text()


@cache
def decider() -> Agent[None, LeadDecision]:
    """The decision agent. Built on first use: constructing the model needs credentials."""
    return Agent(
        resolve_model(MAIN_AGENT_MODEL),
        output_type=LeadDecision,
        instructions=load_policy(),
        name="zillow_lead_decider",
    )


def decision_settings() -> ModelSettings:
    return build_openrouter_settings(DEFAULT_THINKING_EFFORT)


async def decide(case: LeadCase) -> LeadDecision:
    """Decide what to do about a lead thread. No side effects."""
    result = await decider().run(render_case(case), model_settings=decision_settings())
    return result.output
