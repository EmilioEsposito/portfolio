"""Evals for the reply decision: export real scenarios, run them, score them.

    uv run python -m api.src.sernia_ai.zillow_leads.evals export \
        --after 2026-04-01 --before 2026-08-01 --out <private>/scenarios.jsonl
    uv run python -m api.src.sernia_ai.zillow_leads.evals run \
        --scenarios <private>/scenarios.jsonl --operating <private>/operating.json \
        --name first-round --sample 30 --out <private>/runs/first-round.jsonl
    uv run python -m api.src.sernia_ai.zillow_leads.evals review \
        --scenarios <private>/scenarios.jsonl --operating <private>/operating.json \
        --run <private>/runs/first-round.jsonl --out <private>/runs/first-round.html

A scenario is a moment in a real thread; operating data is what was true of
the business then. They are stored apart and joined into a case at run time,
so listings and schedules can change shape without touching the scenarios or
the labels people have put on them.

Scenarios hold applicants' emails. Keep every path above outside the
repository; nothing here prints their content, only counts.

``run`` sends the experiment to Logfire when a token is configured. ``review``
writes one self-contained page for a person to mark each decision pass or
fail; it holds applicant emails too.
"""

import argparse
import asyncio
import hashlib
import json
import random
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from api.src.sernia_ai.config import MAIN_AGENT_MODEL
from api.src.sernia_ai.zillow_leads.case import (
    LeadCase,
    OperatingData,
    build_case,
    decision_points,
    render_case,
)
from api.src.sernia_ai.zillow_leads.decision import LeadDecision, decide
from api.src.sernia_ai.zillow_leads.thread import (
    LeadThread,
    find_lead_thread_ids,
    load_lead_thread,
)

# Leads reach us through Zillow's relay addresses.
LEAD_QUERY = "from:convo.zillow.com"
SIGNATURE = "Sernia Capital Team"
REVIEW_TEMPLATE = Path(__file__).with_name("review.html")


class Scenario(BaseModel):
    """A moment in a real thread where a decision was owed, and what happened next.

    It holds only the conversation. Listings, schedules and standard answers
    come from operating data when the scenario is run.

    ``actual_reply`` is context for the person reviewing. It is never shown to
    the model and is not treated as the right answer.
    """

    thread: LeadThread  # cut at the decision point: the last message is the lead's
    actual_reply: str | None = None
    actual_reply_delay_minutes: int | None = None

    @property
    def scenario_id(self) -> str:
        return f"{self.thread.thread_id}-{len(self.thread.messages) - 1}"


class Label(BaseModel):
    """A person's verdict on one decision. Tied to the scenario, not to the
    case or the run, so it stays meaningful when operating data or the policy
    changes: the note says what a good response to this conversation needs."""

    scenario_id: str
    verdict: Literal["pass", "fail"] | None = None
    note: str = ""
    action: str = ""  # what the model did in the run that was reviewed


def scenarios_from_thread(thread: LeadThread) -> list[Scenario]:
    """One scenario per decision point in a past thread."""
    scenarios: list[Scenario] = []
    for index in decision_points(thread):
        lead_message = thread.messages[index]
        reply = thread.messages[index + 1] if index + 1 < len(thread.messages) else None
        scenarios.append(
            Scenario(
                thread=thread.model_copy(update={"messages": thread.messages[: index + 1]}),
                actual_reply=reply.body if reply else None,
                actual_reply_delay_minutes=(
                    int((reply.sent_at - lead_message.sent_at).total_seconds() // 60)
                    if reply
                    else None
                ),
            )
        )
    return scenarios


def case_for(scenario: Scenario, operating: OperatingData) -> LeadCase:
    """The scenario as a case: the conversation plus what was true at that moment."""
    return build_case(scenario.thread, operating, upto=len(scenario.thread.messages) - 1)


@dataclass
class ReplyChecks(Evaluator[LeadCase, LeadDecision, dict]):
    """Rules about a reply that have exact answers. Skipped when there is no reply."""

    def evaluate(self, ctx: EvaluatorContext[LeadCase, LeadDecision, dict]) -> dict[str, bool]:
        decision, case = ctx.output, ctx.inputs
        if decision.action != "reply" or not decision.reply_body:
            return {}
        body = decision.reply_body
        allowed = {slot.start for slot in case.offerable_slots} | {
            tour.start for tour in case.booked_tours
        }
        staff = {slot.agent.split()[0].lower() for slot in case.offerable_slots if slot.agent}
        words = {word.strip(".,!?:;()").lower() for word in body.split()}
        return {
            # A time without a UTC offset cannot be compared, so it fails.
            "offered_slots_allowed": all(
                slot.tzinfo is not None and slot in allowed for slot in decision.offered_slots
            ),
            "no_em_dash": "—" not in body,
            "has_signature": SIGNATURE in body,
            "no_staff_names": not (staff & words),
        }


def build_dataset(
    scenarios: list[Scenario], operating: OperatingData
) -> Dataset[LeadCase, LeadDecision, dict]:
    return Dataset(
        name="zillow-lead-replies",
        cases=[
            Case(
                name=scenario.scenario_id,
                inputs=case_for(scenario, operating),
                metadata={
                    "actual_reply": scenario.actual_reply,
                    "actual_reply_delay_minutes": scenario.actual_reply_delay_minutes,
                },
            )
            for scenario in scenarios
        ],
        evaluators=[ReplyChecks()],
    )


def read_scenarios(path: Path) -> list[Scenario]:
    return [Scenario.model_validate_json(line) for line in path.read_text().splitlines() if line]


def _fingerprint(path: Path) -> str:
    """A short hash of a file, so a run records exactly which version it read."""
    return f"{path.name}@{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"


def _policy_commit() -> str:
    """The commit the policy file is at, with '+dirty' when it has unsaved edits."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(Path(__file__).parent)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return f"{commit}+dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def export(args: argparse.Namespace) -> None:
    query = f"{args.query} after:{args.after:%Y/%m/%d} before:{args.before:%Y/%m/%d}"
    thread_ids = await find_lead_thread_ids(query)
    scenarios: list[Scenario] = []
    for thread_id in thread_ids:
        thread = await load_lead_thread(thread_id)
        if thread is not None:
            scenarios += scenarios_from_thread(thread)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(item.model_dump_json() + "\n" for item in scenarios))
    answered = sum(item.actual_reply is not None for item in scenarios)
    print(
        f"threads: {len(thread_ids)}  scenarios: {len(scenarios)}  answered at the time: {answered}"
    )


async def run(args: argparse.Namespace) -> None:
    import logfire

    from api.src.utils.logfire_config import ensure_logfire_configured

    ensure_logfire_configured(mode="prod", service_name="zillow-evals", environment="local")
    logfire.instrument_pydantic_ai()

    operating = OperatingData.model_validate_json(args.operating.read_text())
    scenarios = read_scenarios(args.scenarios)
    if args.sample and args.sample < len(scenarios):
        scenarios = random.Random(args.seed).sample(scenarios, args.sample)

    policy_commit = _policy_commit()
    report = await build_dataset(scenarios, operating).evaluate(
        decide,
        name=args.name,
        max_concurrency=args.concurrency,
        progress=False,
        metadata={
            "policy_commit": policy_commit,
            "model": MAIN_AGENT_MODEL,
            "operating": _fingerprint(args.operating),
        },
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as out:
        for case in report.cases:
            row = {
                "case_id": case.name,
                "policy_commit": policy_commit,
                "decision": case.output.model_dump(mode="json"),
                "checks": {name: result.value for name, result in case.assertions.items()},
                "trace_id": case.trace_id,
            }
            out.write(json.dumps(row) + "\n")

    actions = Counter(case.output.action for case in report.cases)
    failed_checks = Counter(
        name
        for case in report.cases
        for name, result in case.assertions.items()
        if not result.value
    )
    print(f"cases: {len(report.cases)}  errors: {len(report.failures)}")
    print(f"actions: {dict(actions)}")
    print(f"failed checks: {dict(failed_checks)}")
    print(f"experiment trace: {report.trace_id}")
    logfire.force_flush()


def _recorded(decision: dict) -> str:
    """The lead facts and tour state the model recorded, on one line."""
    lead = [f"{name}: {value}" for name, value in decision["lead"].items() if value]
    tour = decision["tour"]
    state = f"tour: {tour['status']}" + (f" at {tour['start']}" if tour.get("start") else "")
    return " · ".join([*lead, state])


def review_page(
    run_name: str, scenarios: list[Scenario], operating: OperatingData, rows: list[dict]
) -> str:
    """One self-contained HTML page for labelling a run's decisions.

    Labels are kept in the browser and downloaded as a file of ``Label`` rows.
    """
    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    cases = []
    for row in rows:
        scenario = by_id[row["case_id"]]
        decision = row["decision"]
        burst: list[str] = []
        for message in reversed(scenario.thread.messages):
            if message.from_sernia:
                break
            burst.insert(0, message.body or "(empty)")
        cases.append(
            {
                "id": scenario.scenario_id,
                "action": decision["action"],
                "reason": decision["reason"],
                "reply": decision.get("reply_body"),
                "question": decision.get("question"),
                "team_notes": decision.get("team_notes", []),
                "offered": len(decision.get("offered_slots", [])),
                "recorded": _recorded(decision),
                "failed_checks": [name for name, ok in row.get("checks", {}).items() if not ok],
                "lead_message": "\n\n".join(burst),
                "case_text": render_case(case_for(scenario, operating)),
                "actual_reply": scenario.actual_reply,
                "actual_delay": scenario.actual_reply_delay_minutes,
            }
        )
    policy_commit = rows[0].get("policy_commit", "unknown") if rows else "unknown"
    data = json.dumps({"run": run_name, "policy_commit": policy_commit, "cases": cases})
    # The data sits inside a <script> element: keep an email from closing it.
    return REVIEW_TEMPLATE.read_text().replace("__REVIEW_DATA__", data.replace("</", "<\\/"))


async def review(args: argparse.Namespace) -> None:
    operating = OperatingData.model_validate_json(args.operating.read_text())
    rows = [json.loads(line) for line in args.run.read_text().splitlines() if line]
    page = review_page(args.run.stem, read_scenarios(args.scenarios), operating, rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"decisions to review: {len(rows)}  page: {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(required=True)

    export_parser = commands.add_parser("export", help="Cut past threads into scenarios")
    export_parser.add_argument("--after", type=date.fromisoformat, required=True)
    export_parser.add_argument("--before", type=date.fromisoformat, required=True)
    export_parser.add_argument("--out", type=Path, required=True)
    export_parser.add_argument("--query", default=LEAD_QUERY)
    export_parser.set_defaults(handler=export)

    run_parser = commands.add_parser("run", help="Run the decision over stored scenarios")
    run_parser.add_argument("--scenarios", type=Path, required=True)
    run_parser.add_argument("--operating", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--sample", type=int, default=0)
    run_parser.add_argument("--seed", type=int, default=0)
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.set_defaults(handler=run)

    review_parser = commands.add_parser("review", help="Write a page for labelling a run")
    review_parser.add_argument("--scenarios", type=Path, required=True)
    review_parser.add_argument("--operating", type=Path, required=True)
    review_parser.add_argument("--run", type=Path, required=True)
    review_parser.add_argument("--out", type=Path, required=True)
    review_parser.set_defaults(handler=review)

    args = parser.parse_args()
    asyncio.run(args.handler(args))


if __name__ == "__main__":
    main()
