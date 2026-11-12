"""The Zillow lead agent: reads one case, returns one outcome.

Why this is its own agent and not a run of the general Sernia agent:

A lead's email is text written by a stranger, and the model reads it. Anything
in it can be worded as an instruction to the model (prompt injection). The
general agent can read company email, files and contacts and can send
messages, so letting it read lead email puts three things in one place:
private data, untrusted text, and a way to send something out. With those
three together, the only safeguard is a person approving every send.

This agent is built so that approval can one day be removed for routine
replies. It has no tools. Its whole input is the case: listings, booking
links, tour times and the thread, none of it private. Its whole output is one
of three fixed outcomes. Code, not the model, decides who a reply goes to and
does the sending (see ``handle.py``). Whatever a lead writes, the most it can
change is which outcome is chosen and the words of a reply that goes back to
that same lead.

Keep it that way. Do not give this agent tools, and do not add anything
private to the case. If it needs to know something new, have code put it in
the case.

``respond(case)`` is also the unit the evals measure, so an experiment runs
exactly what production runs. Its instructions are three files in this
package, joined in order, so the instructions in force for any run are the
commit the code was at:

- ``role.md``: who this agent is and what it is given. Lead agent only.
- ``policy.md``: the rules for answering a lead. The general agent reads the
  same file as a skill (see ``skill.py``), so keep it free of anything that is
  true only for this agent.
- ``record.md``: how to fill in the output fields. Lead agent only.
"""

from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.settings import ModelSettings

from api.src.sernia_ai.model_config import (
    DEFAULT_THINKING_EFFORT,
    build_openrouter_settings,
    resolve_model,
)
from api.src.sernia_ai.zillow_leads.case import LeadCase, render_case

ROLE_PATH = Path(__file__).with_name("role.md")
POLICY_PATH = Path(__file__).with_name("policy.md")
RECORD_PATH = Path(__file__).with_name("record.md")
# This agent has its own model setting, so it can change, and be evaluated,
# without changing the model the general agent runs on.
LEAD_AGENT_MODEL = "openrouter:openai/gpt-6-luna"


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


class _Outcome(BaseModel):
    """What every outcome records, whatever was decided."""

    model_config = ConfigDict(extra="forbid")

    lead: LeadFacts
    tour: TourState
    reason: str = Field(description="One or two sentences on why this outcome.")
    team_notes: list[str] = Field(
        default_factory=list, description="Things a person must do by hand."
    )


class Reply(_Outcome):
    """Send the lead an email."""

    action: Literal["reply"] = "reply"
    reply_body: str = Field(
        min_length=1, description="The full plain-text email, signature included."
    )
    offered_slots: list[datetime] = Field(
        default_factory=list,
        description="Every tour time the reply offers, with UTC offset, copied from the case.",
    )


class NoReply(_Outcome):
    """Send nothing. Nothing is owed right now."""

    action: Literal["no_reply"] = "no_reply"


class Escalate(_Outcome):
    """Hand the thread to a person."""

    action: Literal["escalate"] = "escalate"
    question: str = Field(min_length=1, description="What you need to know to proceed.")


# Three separate types, not one type with optional fields: a reply cannot
# exist without its text, and an escalation cannot exist without its question.
LeadOutcome = Annotated[Reply | NoReply | Escalate, Field(discriminator="action")]


def load_policy() -> str:
    """The rules for answering a lead: the part both agents share."""
    return POLICY_PATH.read_text()


def load_instructions() -> str:
    """Everything the lead agent is told: its role, the policy, what to record."""
    return "\n".join(path.read_text() for path in (ROLE_PATH, POLICY_PATH, RECORD_PATH))


@cache
def lead_agent() -> Agent[None, Reply | NoReply | Escalate]:
    """The agent. Built on first use: constructing the model needs credentials.

    No tools, by design: see the module docstring before adding one.
    """
    return Agent(
        resolve_model(LEAD_AGENT_MODEL),
        output_type=[
            ToolOutput(Reply, name="reply"),
            ToolOutput(NoReply, name="no_reply"),
            ToolOutput(Escalate, name="escalate"),
        ],
        instructions=load_instructions(),
        name="zillow_lead_agent",
    )


def agent_settings() -> ModelSettings:
    return build_openrouter_settings(DEFAULT_THINKING_EFFORT)


async def respond(case: LeadCase) -> Reply | NoReply | Escalate:
    """Read the case and return the outcome. No side effects."""
    result = await lead_agent().run(render_case(case), model_settings=agent_settings())
    return result.output
