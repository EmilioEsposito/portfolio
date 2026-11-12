"""Evals for the reply decision: export real scenarios, run them, score them.

    uv run python -m api.src.sernia_ai.zillow_leads.evals export \
        --after 2026-04-01 --before 2026-08-01 --out <private>/scenarios.jsonl
    uv run python -m api.src.sernia_ai.zillow_leads.evals split \
        --scenarios <private>/scenarios.jsonl --out <private>/splits.json
    uv run python -m api.src.sernia_ai.zillow_leads.evals run \
        --scenarios <private>/scenarios.jsonl --operating <private>/operating.json \
        --splits <private>/splits.json --split dev \
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
fail. ``browse`` writes a page for reading the train and dev splits, with
links into the review pages. Both pages hold applicant emails too.
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from api.src.sernia_ai.model_config import resolve_model
from api.src.sernia_ai.zillow_leads.agent import (
    LEAD_AGENT_MODEL,
    Escalate,
    NoReply,
    Reply,
    lead_agent,
    load_instructions,
    respond,
)
from api.src.sernia_ai.zillow_leads.case import (
    LeadCase,
    OperatingData,
    build_case,
    decision_points,
    render_case,
)
from api.src.sernia_ai.zillow_leads.thread import (
    LeadThread,
    find_lead_thread_ids,
    load_lead_thread,
)

# Leads reach us through Zillow's relay addresses.
LEAD_QUERY = "from:convo.zillow.com"
SIGNATURE = "Sernia Capital Team"
LINK = re.compile(r"https?://[^\s<>\"')\]]+")
REVIEW_TEMPLATE = Path(__file__).with_name("review.html")
DATASET_TEMPLATE = Path(__file__).with_name("dataset.html")


class Scenario(BaseModel):
    """A moment in a real thread where a decision was owed, and what happened next.

    It holds only the conversation. Listings, schedules and booking links
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
    # Optional: the reply the reviewer would have sent. One good answer, not
    # the only one, so it is an example to learn from, never an exact target.
    corrected_reply: str = ""
    action: str = ""  # what the model did in the run that was reviewed
    # Which output this verdict judged. The model words the same decision
    # differently on every run, so a label applies to one output, and a later
    # run shows it as an earlier verdict to confirm, not as settled.
    output_hash: str = ""
    run: str = ""


# Each thread belongs to one split, for good. Whole threads, because a later
# scenario in a thread contains the earlier ones.
#   train: worked examples that may be pasted into a prompt as few-shot
#          examples: a case paired with the reply a person would send (for
#          the policy), or a reply paired with its verdict (for an LLM judge).
#          Nothing is trained; the name is inherited from machine learning
#   dev:   run, read, label and change things against these, as often as needed
#   test:  not looked at until a version is thought finished, then run once
SPLIT_SHARES = {"train": 0.15, "test": 0.40}  # dev is the remainder
SPLITS = ("train", "dev", "test")


def assign_splits(thread_ids: list[str], *, seen: set[str], seed: int = 0) -> dict[str, str]:
    """Assign every thread to train, dev or test.

    ``seen`` are threads whose scenarios have already been run and looked at.
    They can only be dev: a test set is worth something only if nothing about
    it has influenced a change.
    """
    threads = sorted(set(thread_ids))
    unseen = [thread for thread in threads if thread not in seen]
    random.Random(seed).shuffle(unseen)
    train = round(len(threads) * SPLIT_SHARES["train"])
    test = round(len(threads) * SPLIT_SHARES["test"])
    splits = {thread: "dev" for thread in threads if thread in seen}
    for position, thread in enumerate(unseen):
        splits[thread] = (
            "train" if position < train else "test" if position < train + test else "dev"
        )
    return splits


def in_split(scenarios: list[Scenario], splits: dict[str, str], split: str) -> list[Scenario]:
    return [item for item in scenarios if splits.get(item.thread.thread_id) == split]


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


def _bare(text: str) -> str:
    """Links without scheme, ``www.`` or trailing punctuation, so the same
    address written two ways compares equal."""
    return re.sub(r"https?://(www\.)?", "", text.lower()).rstrip("/.,")


@dataclass
class ReplyChecks(Evaluator[LeadCase, Reply | NoReply | Escalate, dict]):
    """Rules about a reply that have exact answers. Skipped when there is no reply."""

    def evaluate(
        self, ctx: EvaluatorContext[LeadCase, Reply | NoReply | Escalate, dict]
    ) -> dict[str, bool]:
        outcome, case = ctx.output, ctx.inputs
        if not isinstance(outcome, Reply):
            return {}
        body = outcome.reply_body
        allowed = {slot.start for slot in case.offerable_slots} | {
            tour.start for tour in case.booked_tours
        }
        staff = {slot.agent.split()[0].lower() for slot in case.offerable_slots if slot.agent}
        words = {word.strip(".,!?:;()").lower() for word in body.split()}
        # Every link the model was given: in the case, or in its instructions.
        given = _bare(
            " ".join(
                [load_instructions()]
                + [f"{listing.booking_url} {listing.facts}" for listing in case.listings]
            )
        )
        return {
            # A time without a UTC offset cannot be compared, so it fails.
            "offered_slots_allowed": all(
                slot.tzinfo is not None and slot in allowed for slot in outcome.offered_slots
            ),
            "no_em_dash": "—" not in body,
            "has_signature": SIGNATURE in body,
            "no_staff_names": not (staff & words),
            # A link the case did not supply was written from memory or invented.
            "links_allowed": all(
                re.search(rf"(?<![\w.-]){re.escape(_bare(link))}(?![\w/-])", given)
                for link in LINK.findall(body)
            ),
        }


def build_dataset(
    scenarios: list[Scenario], operating: OperatingData
) -> Dataset[LeadCase, Reply | NoReply | Escalate, dict]:
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


async def split(args: argparse.Namespace) -> None:
    if args.out.exists():
        raise SystemExit(f"{args.out} exists. A split is made once; delete it to start over.")
    scenarios = read_scenarios(args.scenarios)
    by_id = {item.scenario_id: item.thread.thread_id for item in scenarios}
    seen = {
        by_id[json.loads(line)["case_id"]]
        for run_file in args.seen_run
        for line in run_file.read_text().splitlines()
        if line
    }
    splits = assign_splits(list(by_id.values()), seen=seen, seed=args.seed)
    args.out.write_text(json.dumps(splits, indent=1, sort_keys=True))
    for name in SPLITS:
        threads = sum(value == name for value in splits.values())
        print(f"{name}: {threads} threads, {len(in_split(scenarios, splits, name))} scenarios")


async def run(args: argparse.Namespace) -> None:
    import logfire

    from api.src.utils.logfire_config import ensure_logfire_configured

    ensure_logfire_configured(mode="prod", service_name="zillow-evals", environment="local")
    logfire.instrument_pydantic_ai()

    operating = OperatingData.model_validate_json(args.operating.read_text())
    scenarios = read_scenarios(args.scenarios)
    if args.splits:
        scenarios = in_split(scenarios, json.loads(args.splits.read_text()), args.split)
        print(f"split: {args.split}  scenarios in it: {len(scenarios)}")
    if args.sample and args.sample < len(scenarios):
        scenarios = random.Random(args.seed).sample(scenarios, args.sample)

    policy_commit = _policy_commit()
    model = args.model or LEAD_AGENT_MODEL
    # --model tries another model on the same cases without editing the code.
    with lead_agent().override(model=resolve_model(model)):
        report = await build_dataset(scenarios, operating).evaluate(
            respond,
            name=args.name,
            max_concurrency=args.concurrency,
            progress=False,
            metadata={
                "policy_commit": policy_commit,
                "model": model,
                "operating": _fingerprint(args.operating),
                "split": args.split if args.splits else "unsplit",
            },
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # The instructions exactly as they were for this run, to read beside its outputs.
    _policy_snapshot(args.out).write_text(load_instructions())
    with args.out.open("w") as out:
        for case in report.cases:
            row = {
                "case_id": case.name,
                "policy_commit": policy_commit,
                "model": model,
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
    print(f"model: {model}")
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


def _policy_snapshot(run_file: Path) -> Path:
    return run_file.with_suffix(".policy.md")


def _output_hash(decision: dict) -> str:
    """Identifies one output: the action and the words a reviewer judges."""
    judged = [decision["action"], decision.get("reply_body"), decision.get("question")]
    return hashlib.sha256(json.dumps(judged).encode()).hexdigest()[:12]


def read_labels(path: Path) -> list[Label]:
    """Labels from a file the review page downloaded, or a plain list of them."""
    content = json.loads(path.read_text())
    rows = content["labels"] if isinstance(content, dict) else content
    return [Label.model_validate(row) for row in rows]


def review_page(
    run_name: str,
    scenarios: list[Scenario],
    operating: OperatingData,
    rows: list[dict],
    policy: str = "",
    *,
    labels: list[Label] | None = None,
    previous: dict[str, list[dict]] | None = None,
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
                "hash": _output_hash(decision),
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
    data = json.dumps(
        {
            "run": run_name,
            "policy_commit": policy_commit,
            "model": rows[0].get("model", "") if rows else "",
            "policy": policy,
            "cases": cases,
            # Labels made before this page was generated, and the outputs of
            # earlier runs, so the page can tell which labels still apply.
            "seed_labels": [label.model_dump() for label in labels or []],
            "previous": {
                name: {row["case_id"]: _output_hash(row["decision"]) for row in old_rows}
                for name, old_rows in (previous or {}).items()
            },
            "previous_actions": {
                name: {row["case_id"]: row["decision"]["action"] for row in old_rows}
                for name, old_rows in (previous or {}).items()
            },
        }
    )
    # The data sits inside a <script> element: keep an email from closing it.
    return REVIEW_TEMPLATE.read_text().replace("__REVIEW_DATA__", data.replace("</", "<\\/"))


def _when(moment, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return f"{local:%a %b} {local.day}, {local:%I:%M %p}".replace(", 0", ", ")


def dataset_page(
    scenarios: list[Scenario],
    splits: dict[str, str],
    runs: dict[str, list[dict]],
    review_hrefs: dict[str, str],
    *,
    timezone: str = "America/New_York",
) -> str:
    """One self-contained HTML page for reading the dataset.

    Train and dev test cases are shown in full. The test split appears as a
    count only: none of its content is written into the page, so reading the
    dataset cannot leak it into a later change.
    """
    zone = ZoneInfo(timezone)
    in_runs: dict[str, list[dict]] = {}
    for name, rows in runs.items():
        for row in rows:
            in_runs.setdefault(row["case_id"], []).append(
                {
                    "run": name,
                    "action": row["decision"]["action"],
                    "href": f"{review_hrefs[name]}#card-{row['case_id']}",
                }
            )
    totals = {
        name: {
            "threads": sum(value == name for value in splits.values()),
            "cases": len(in_split(scenarios, splits, name)),
        }
        for name in SPLITS
    }
    cases = []
    for scenario in scenarios:
        split_name = splits.get(scenario.thread.thread_id)
        if split_name not in ("train", "dev"):
            continue
        messages = scenario.thread.messages
        start = len(messages)
        while start > 0 and not messages[start - 1].from_sernia:
            start -= 1
        cases.append(
            {
                "id": scenario.scenario_id,
                "split": split_name,
                "when": _when(messages[-1].sent_at, zone),
                "sent_at": messages[-1].sent_at.isoformat(),
                "messages": len(messages),
                "first": start == 0,
                "lead_message": "\n\n".join(m.body or "(empty)" for m in messages[start:]),
                "earlier": [
                    {
                        "who": "Us" if m.from_sernia else "Lead",
                        "when": _when(m.sent_at, zone),
                        "body": m.body or "(empty)",
                    }
                    for m in messages[:start]
                ],
                "actual_reply": scenario.actual_reply,
                "actual_delay": scenario.actual_reply_delay_minutes,
                "runs": in_runs.get(scenario.scenario_id, []),
            }
        )
    cases.sort(key=lambda case: case["sent_at"])
    data = json.dumps(
        {
            "totals": totals,
            "cases": cases,
            "reviews": [
                {"run": name, "href": review_hrefs[name], "count": len(rows)}
                for name, rows in runs.items()
            ],
        }
    )
    return DATASET_TEMPLATE.read_text().replace("__DATASET_DATA__", data.replace("</", "<\\/"))


async def browse(args: argparse.Namespace) -> None:
    scenarios = read_scenarios(args.scenarios)
    splits = json.loads(args.splits.read_text())
    runs, hrefs = {}, {}
    for run_file in args.run:
        runs[run_file.stem] = [
            json.loads(line) for line in run_file.read_text().splitlines() if line
        ]
        # A review page sits beside its run with the same name, unless told otherwise.
        hrefs[run_file.stem] = os.path.relpath(
            args.review or run_file.with_suffix(".html"), args.out.parent
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dataset_page(scenarios, splits, runs, hrefs))
    readable = len(in_split(scenarios, splits, "train")) + len(in_split(scenarios, splits, "dev"))
    print(f"readable test cases: {readable}  sealed: {len(in_split(scenarios, splits, 'test'))}")
    print(f"page: {args.out}")


async def review(args: argparse.Namespace) -> None:
    operating = OperatingData.model_validate_json(args.operating.read_text())
    rows = [json.loads(line) for line in args.run.read_text().splitlines() if line]
    snapshot = _policy_snapshot(args.run)
    policy = snapshot.read_text() if snapshot.exists() else ""
    previous = {
        run_file.stem: [json.loads(line) for line in run_file.read_text().splitlines() if line]
        for run_file in args.previous
    }
    page = review_page(
        args.run.stem,
        read_scenarios(args.scenarios),
        operating,
        rows,
        policy,
        labels=read_labels(args.labels) if args.labels else None,
        previous=previous,
    )
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

    split_parser = commands.add_parser("split", help="Assign threads to train, dev, test, once")
    split_parser.add_argument("--scenarios", type=Path, required=True)
    split_parser.add_argument("--out", type=Path, required=True)
    split_parser.add_argument("--seen-run", type=Path, action="append", default=[])
    split_parser.add_argument("--seed", type=int, default=0)
    split_parser.set_defaults(handler=split)

    run_parser = commands.add_parser("run", help="Run the decision over stored scenarios")
    run_parser.add_argument("--scenarios", type=Path, required=True)
    run_parser.add_argument("--operating", type=Path, required=True)
    run_parser.add_argument("--splits", type=Path, help="Split file; without it every scenario")
    run_parser.add_argument("--split", choices=SPLITS, default="dev")
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--sample", type=int, default=0)
    run_parser.add_argument("--seed", type=int, default=0)
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.add_argument("--model", help="Model to run on instead of the configured one")
    run_parser.set_defaults(handler=run)

    review_parser = commands.add_parser("review", help="Write a page for labelling a run")
    review_parser.add_argument("--scenarios", type=Path, required=True)
    review_parser.add_argument("--operating", type=Path, required=True)
    review_parser.add_argument("--run", type=Path, required=True)
    review_parser.add_argument("--out", type=Path, required=True)
    review_parser.add_argument("--labels", type=Path, help="Labels to start from")
    review_parser.add_argument(
        "--previous", type=Path, action="append", default=[], help="An earlier run's output"
    )
    review_parser.set_defaults(handler=review)

    browse_parser = commands.add_parser("browse", help="Write a page for reading the dataset")
    browse_parser.add_argument("--scenarios", type=Path, required=True)
    browse_parser.add_argument("--splits", type=Path, required=True)
    browse_parser.add_argument("--run", type=Path, action="append", default=[])
    browse_parser.add_argument("--review", type=Path, help="Review page to link to")
    browse_parser.add_argument("--out", type=Path, required=True)
    browse_parser.set_defaults(handler=browse)

    args = parser.parse_args()
    asyncio.run(args.handler(args))


if __name__ == "__main__":
    main()
