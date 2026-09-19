"""Bounded inference-only Luna/Jev eval. Never invoke a webhook or dispatch.

uv run python -m scripts.eval_escalation_context --output /private/path/run.json
Labels are synthetic policy expectations, not human adjudications.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent

from api.src.open_phone.escalation_context import add_history_timing
from api.src.open_phone.escalation_policy import ESCALATION_QUESTION
from api.src.open_phone.typesafe_openrouter import OpenRouterDecisionResponse, create_jev_client
from api.src.sernia_ai.model_config import build_openrouter_settings, resolve_model


class ShouldEscalate(BaseModel):
    """Structured verdict returned by the escalation-assessment agent."""

    should_escalate: bool
    reason: str


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--timing", choices=("raw", "computed", "both"), default="computed")
    parser.add_argument("--model", choices=("Luna", "Jev", "both"), default="both")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("api/src/tests/eval_data/escalation_history_cases.json"),
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a fresh output path to preserve prior results.")
    logfire.configure(send_to_logfire=False, console=False)
    source = args.dataset
    cases = json.loads(source.read_text())
    variants = ("raw", "computed") if args.timing == "both" else (args.timing,)
    cases = [
        {
            **case,
            "timing": variant,
            "state": add_history_timing(case["state"]) if variant == "computed" else case["state"],
        }
        for case in cases
        for variant in variants
    ]
    settings = build_openrouter_settings("low")
    settings["timeout"] = 30.0
    agent = Agent(
        resolve_model("openrouter:openai/gpt-5.6-luna"),
        output_type=ShouldEscalate,
        instructions=ESCALATION_QUESTION.instructions,
        model_settings=settings,
        retries=0,
    )
    report = {
        "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "instructions": ESCALATION_QUESTION.instructions,
        "criteria": ESCALATION_QUESTION.criteria,
        "label_source": "synthetic_policy_expectation",
        "repeats": args.repeats,
        "timing_variants": variants,
        "models": {"Luna": "openai/gpt-5.6-luna", "Jev": "typesafe/jev-1.13"},
        "luna_reasoning": "low",
        "timeout_seconds": 30,
        "retries": 0,
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeats):
        for case in cases:
            for model in ("Luna", "Jev") if args.model == "both" else (args.model,):
                started = perf_counter()
                row = {
                    "case_id": case["case_id"],
                    "repeat": repeat,
                    "model": model,
                    "timing": case["timing"],
                    "expected": case["expected"],
                }
                try:
                    async with asyncio.timeout(30):
                        if model == "Luna":
                            result = await agent.run(json.dumps(case["state"]))
                            output = result.output.model_dump()
                            response = [m for m in result.all_messages() if m.kind == "response"][
                                -1
                            ]
                            cost = (response.provider_details or {}).get("cost")
                            verdict = result.output.should_escalate
                        else:
                            async with create_jev_client() as client:
                                result = await client.system_one(
                                    state=case["state"],
                                    questions={"escalation": ESCALATION_QUESTION},
                                    response_model=OpenRouterDecisionResponse,
                                )
                            decision = result.choices["escalation"]
                            if decision.choice not in ESCALATION_QUESTION.criteria:
                                raise ValueError("Unknown decision")
                            output = decision.model_dump()
                            verdict = decision.choice == "escalate"
                            cost = result.usage.cost
                    row.update(
                        status="ok",
                        output=output,
                        verdict=verdict,
                        passed=verdict == case["expected"],
                        cost=cost,
                    )
                except Exception as exc:
                    row.update(status="error", error_type=type(exc).__name__)
                row["seconds"] = perf_counter() - started
                report["results"].append(row)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                args.output.chmod(0o600)
                print(json.dumps(row), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
