"""An LLM judge: a second model that grades a decision against the instructions.

The agent's instructions are the standard. The judge is told how to apply
them (``prompts/judge.md``): grade on what the case contained, and report a
gap in the instructions as a question for the owner instead of guessing.

A judge is only as good as its agreement with a person. ``agreement`` measures
that against the annotations store, as two rates, because most decisions pass
and a judge that passed everything would agree most of the time.
"""

import functools
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from api.src.utils.openrouter_models import resolve_model

# Stronger than the model that writes the replies: reading for mistakes is
# harder than making them, and the judge runs offline, where cost matters less.
JUDGE_MODEL = "openrouter:openai/gpt-6.1-sol"
JUDGE_PROMPT = Path(__file__).with_name("prompts") / "judge.md"


class Failure(BaseModel):
    rule: str = Field(description="The shortest part of the instructions that was broken, quoted")
    evidence: str = Field(description="The part of the decision that breaks it, quoted")


class Judgement(BaseModel):
    verdict: Literal["pass", "fail"]
    failures: list[Failure] = Field(default_factory=list)
    summary: str = Field(description="One or two sentences a busy person can act on")
    policy_gap: str = Field(
        default="",
        description="A question for the owner when the instructions do not cover this; else empty",
    )

    @model_validator(mode="after")
    def _failures_match_the_verdict(self) -> "Judgement":
        if (self.verdict == "fail") != bool(self.failures):
            raise ValueError("verdict is 'fail' exactly when failures is not empty")
        return self


def load_judge_prompt() -> str:
    return JUDGE_PROMPT.read_text()


@functools.cache
def judge_agent() -> Agent[None, Judgement]:
    """Built on first use: constructing the model needs credentials. No tools."""
    return Agent(
        resolve_model(JUDGE_MODEL),
        output_type=Judgement,
        instructions=load_judge_prompt(),
        name="zillow_lead_judge",
    )


def judge_input(instructions: str, case_text: str, decision: dict) -> str:
    """What the judge reads: the agent's instructions, its case, its decision."""
    return "\n\n".join(
        [
            "# The instructions the agent had",
            instructions.strip(),
            "# The case the agent was given",
            case_text.strip(),
            "# The decision the agent returned",
            json.dumps(decision, indent=1),
        ]
    )


async def judge(instructions: str, case_text: str, decision: dict) -> Judgement:
    result = await judge_agent().run(judge_input(instructions, case_text, decision))
    return result.output


def fingerprint(text: str) -> str:
    """A short hash, so a judgement records which instructions and prompt made it."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def agreement(pairs: list[tuple[str, str]]) -> dict[str, float | int | None]:
    """Compare ``(person's verdict, judge's verdict)`` pairs.

    Two rates, not one accuracy: of the outputs the person passed, how many
    the judge passes; and of the ones the person failed, how many it fails. A
    rate is ``None`` when the person gave no verdict of that kind.
    """
    passed = [judged for person, judged in pairs if person == "pass"]
    failed = [judged for person, judged in pairs if person == "fail"]
    return {
        "person passed": len(passed),
        "judge passed those": passed.count("pass"),
        "true positive rate": round(passed.count("pass") / len(passed), 2) if passed else None,
        "person failed": len(failed),
        "judge failed those": failed.count("fail"),
        "true negative rate": round(failed.count("fail") / len(failed), 2) if failed else None,
    }


def half(thread_id: str) -> Literal["judge-dev", "judge-test"]:
    """Which half of the labelled outputs a thread's verdicts belong to.

    The judge's prompt may be tuned against judge-dev. Its rates on judge-test
    are then the ones to believe. Fixed by the thread id, so it never moves.
    """
    return (
        "judge-dev" if int(hashlib.sha256(thread_id.encode()).hexdigest(), 16) % 2 else "judge-test"
    )
