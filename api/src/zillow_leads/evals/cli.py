"""Commands for the lead agent evals. Usage is in the package docstring."""

import argparse
import asyncio
import functools
import hashlib
import http.server
import json
import os
import random
import shutil
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path

from api.src.utils.openrouter_models import resolve_model
from api.src.zillow_leads.agent import (
    LEAD_AGENT_MODEL,
    lead_agent,
    load_instructions,
    respond,
)
from api.src.zillow_leads.case import ListingData, render_case
from api.src.zillow_leads.evals.checks import build_dataset
from api.src.zillow_leads.evals.judge import (
    JUDGE_MODEL,
    agreement,
    fingerprint,
    half,
    judge,
    judge_agent,
    load_judge_prompt,
)
from api.src.zillow_leads.evals.labels import (
    add_annotations,
    latest_per_output,
    latest_per_scenario,
    output_hash,
    read_labels,
)
from api.src.zillow_leads.evals.pages import dataset_page, review_page
from api.src.zillow_leads.evals.scenarios import (
    SPLITS,
    CaseEdit,
    Scenario,
    assign_splits,
    case_for,
    edit_fingerprint,
    edit_run_with,
    in_split,
    read_case_edits,
    read_scenarios,
    scenarios_from_thread,
)
from api.src.zillow_leads.thread import find_lead_thread_ids, load_lead_thread

# Leads reach us through Zillow's relay addresses.
LEAD_QUERY = "from:convo.zillow.com"
# The whole package: the prompts and the code that builds the case.
PACKAGE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).with_name("data")
# One fixed port. The review page keeps labels in the browser per address, so
# a port that moved would leave them behind.
SERVE_PORT = 8377
# The season file of listings, tour times and booking links. --operating is its
# old name, kept so existing commands and notes still run.
LISTINGS_HELP = "the season's listing data file (old name: --operating)"


def _fingerprint(path: Path) -> str:
    """A short hash of a file, so a run records exactly which version it read."""
    return f"{path.name}@{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"


def _policy_commit() -> str:
    """The commit the package is at, with '+dirty' when it has unsaved edits."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(PACKAGE_DIR)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return f"{commit}+dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _policy_snapshot(run_file: Path) -> Path:
    return run_file.with_suffix(".policy.md")


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


def not_in_runs(scenarios: list[Scenario], run_files: list[Path]) -> list[Scenario]:
    """Scenarios no earlier run has produced an output for: a fresh sample."""
    seen = {
        json.loads(line)["case_id"]
        for run_file in run_files
        for line in run_file.read_text().splitlines()
        if line
    }
    return [scenario for scenario in scenarios if scenario.scenario_id not in seen]


def in_run(scenarios: list[Scenario], run_file: Path) -> list[Scenario]:
    """The scenarios an earlier run covered, in that run's order: the same sample again."""
    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    order = [json.loads(line)["case_id"] for line in run_file.read_text().splitlines() if line]
    return [by_id[case_id] for case_id in order if case_id in by_id]


def carry_over(
    earlier: list[dict], edits: dict[str, CaseEdit]
) -> tuple[list[str], dict[str, dict]]:
    """Split an earlier run into the cases to run again and the rows to keep.

    A case is run again when its hand edit is not the one the earlier row was
    run with: added, changed or removed since. Every other row is kept as it
    was, marked with the run it came from. A row run with an edit before
    fingerprints were stored cannot be compared, so it is run again.
    """

    def recorded(row: dict) -> str | None:
        if "case_edit_hash" in row:
            return row["case_edit_hash"]
        return None if row.get("case_edit") else ""

    again = [
        row["case_id"]
        for row in earlier
        if recorded(row) != edit_fingerprint(edits.get(row["case_id"]))
    ]
    kept = {row["case_id"]: row for row in earlier if row["case_id"] not in again}
    return again, kept


async def run(args: argparse.Namespace) -> None:
    import logfire

    from api.src.utils.logfire_config import ensure_logfire_configured

    ensure_logfire_configured(mode="prod", service_name="zillow-evals", environment="local")
    logfire.instrument_pydantic_ai()

    listing_data = ListingData.model_validate_json(args.listings.read_text())
    scenarios = read_scenarios(args.scenarios)
    edits = read_case_edits(args.case_edits) if args.case_edits else {}
    order: list[str] = []
    kept: dict[str, dict] = {}
    if args.carry:
        # The kept rows stand for this run only if the model would be told the same.
        if _policy_snapshot(args.carry).read_text() != load_instructions():
            raise SystemExit(f"The instructions have changed since {args.carry.name}: run it all.")
        earlier = [json.loads(line) for line in args.carry.read_text().splitlines() if line]
        order = [row["case_id"] for row in earlier]
        again, kept = carry_over(earlier, edits)
        kept = {case_id: {"carried_from": args.carry.stem, **row} for case_id, row in kept.items()}
        scenarios = [item for item in scenarios if item.scenario_id in again]
        print(f"from {args.carry.stem}: running {len(scenarios)} again, keeping {len(kept)}")
    if args.cases_from:
        scenarios = in_run(scenarios, args.cases_from)
        print(f"same cases as {args.cases_from.stem}: {len(scenarios)}")
    if args.splits:
        scenarios = in_split(scenarios, json.loads(args.splits.read_text()), args.split)
        print(f"split: {args.split}  scenarios in it: {len(scenarios)}")
    if args.exclude_run:
        scenarios = not_in_runs(scenarios, args.exclude_run)
        print(f"not yet run: {len(scenarios)}")
    if args.sample and args.sample < len(scenarios):
        scenarios = random.Random(args.seed).sample(scenarios, args.sample)

    policy_commit = _policy_commit()
    model = args.model or LEAD_AGENT_MODEL
    # --model tries another model on the same cases without editing the code.
    with lead_agent().override(model=resolve_model(model)):
        report = await build_dataset(scenarios, listing_data, edits).evaluate(
            respond,
            name=args.name,
            max_concurrency=args.concurrency,
            progress=False,
            metadata={
                "policy_commit": policy_commit,
                "model": model,
                # Key kept from before the rename, so old and new runs compare.
                "operating": _fingerprint(args.listings),
                "split": args.split if args.splits else "unsplit",
            },
        )

    if scenarios and not report.cases:
        # Every call failed (a spent key, a wrong model name). Say so and write
        # nothing: an empty run file would look like a run with no decisions.
        first = report.failures[0].error_message if report.failures else "no error recorded"
        raise SystemExit(f"No case produced an output; nothing written. First error: {first[:300]}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # The instructions exactly as they were for this run, to read beside its outputs.
    _policy_snapshot(args.out).write_text(load_instructions())
    rows = dict(kept)
    for case in report.cases:
        rows[case.name] = {
            "case_id": case.name,
            "policy_commit": policy_commit,
            "model": model,
            "decision": case.output.model_dump(mode="json"),
            "checks": {name: result.value for name, result in case.assertions.items()},
            "trace_id": case.trace_id,
            # Whether this case was run with hand-set tour times (see CaseEdit).
            "case_edit": case.name in edits,
            # Which edit, so a later change to the edits file can be noticed.
            "case_edit_hash": edit_fingerprint(edits.get(case.name)),
        }
    with args.out.open("w") as out:
        # A carried run keeps the earlier run's order, so the page reads the same.
        for case_id in order or rows:
            if case_id in rows:
                out.write(json.dumps(rows[case_id]) + "\n")

    actions = Counter(row["decision"]["action"] for row in rows.values())
    failed_checks = Counter(
        name
        for case in report.cases
        for name, result in case.assertions.items()
        if not result.value
    )
    print(f"model: {model}")
    print(f"cases run: {len(report.cases)}  errors: {len(report.failures)}  kept: {len(kept)}")
    print(f"actions: {dict(actions)}")
    print(f"failed checks in the cases run: {dict(failed_checks)}")
    print(f"experiment trace: {report.trace_id}")
    logfire.force_flush()


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
    listing_data = ListingData.model_validate_json(args.listings.read_text())
    rows = [json.loads(line) for line in args.run.read_text().splitlines() if line]
    snapshot = _policy_snapshot(args.run)
    policy = snapshot.read_text() if snapshot.exists() else ""
    previous = {
        run_file.stem: [json.loads(line) for line in run_file.read_text().splitlines() if line]
        for run_file in args.previous
    }
    try:
        page = review_page(
            args.run.stem,
            read_scenarios(args.scenarios),
            listing_data,
            rows,
            policy,
            # The store holds every verdict ever given; the page starts from the latest.
            labels=latest_per_scenario(read_labels(args.labels)) if args.labels else None,
            previous=previous,
            edits=read_case_edits(args.case_edits) if args.case_edits else None,
        )
    except ValueError as error:  # a row whose hand edit cannot be matched
        raise SystemExit(str(error)) from None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"decisions to review: {len(rows)}  page: {args.out}")


async def collect(args: argparse.Namespace) -> None:
    """Add a labels file the review page downloaded to the annotations store."""
    counts = add_annotations(args.store, read_labels(args.labels), args.runs)
    # Keep the download as it was, beside the store.
    kept = args.store.parent / "downloads" / args.labels.name
    if args.labels.resolve() != kept.resolve():
        kept.parent.mkdir(parents=True, exist_ok=True)
        if kept.exists() and kept.read_bytes() != args.labels.read_bytes():
            kept = kept.with_stem(f"{kept.stem}-{_fingerprint(args.labels).split('@')[1]}")
        shutil.copy2(args.labels, kept)
    print("  ".join(f"{name}: {count}" for name, count in counts.items()))
    print(f"annotations in the store: {len(read_labels(args.store)) if args.store.exists() else 0}")


def outputs_to_judge(
    runs: dict[str, list[dict]], labelled: set[tuple[str, str]] | None
) -> list[tuple[str, dict]]:
    """Each distinct output in the runs, once, as ``(run name, row)``.

    With ``labelled``, only the outputs a person has given a verdict on: that
    is the set a judge is measured against.
    """
    seen: set[tuple[str, str, str | bool]] = set()
    picked: list[tuple[str, dict]] = []
    for name, rows in runs.items():
        for row in rows:
            output = (row["case_id"], output_hash(row["decision"]))
            key = (*output, row.get("case_edit_hash") or bool(row.get("case_edit")))
            if key in seen or (labelled is not None and output not in labelled):
                continue
            seen.add(key)
            picked.append((name, row))
    return picked


async def judge_runs(args: argparse.Namespace) -> None:
    """Grade outputs with the LLM judge, and compare with a person's verdicts."""
    import logfire

    from api.src.utils.logfire_config import ensure_logfire_configured

    ensure_logfire_configured(mode="prod", service_name="zillow-evals", environment="local")
    logfire.instrument_pydantic_ai()

    listing_data = ListingData.model_validate_json(args.listings.read_text())
    by_id = {item.scenario_id: item for item in read_scenarios(args.scenarios)}
    edits = read_case_edits(args.case_edits) if args.case_edits else {}
    verdicts = latest_per_output(read_labels(args.labels)) if args.labels else {}
    runs = {
        run_file.stem: [json.loads(line) for line in run_file.read_text().splitlines() if line]
        for run_file in args.run
    }
    picked = outputs_to_judge(runs, set(verdicts) if args.only_labelled else None)
    try:  # before any model call: every case must be rebuilt as it was run
        for _, row in picked:
            edit_run_with(row, edits)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    # The standard is the instructions as they are now, whatever the run had.
    instructions = load_instructions()
    model = args.model or JUDGE_MODEL
    stamp = {
        "judge_model": model,
        "judge_prompt": fingerprint(load_judge_prompt()),
        "instructions": fingerprint(instructions),
    }
    limit = asyncio.Semaphore(args.concurrency)

    async def one(name: str, row: dict) -> dict | None:
        scenario = by_id[row["case_id"]]
        case_text = render_case(case_for(scenario, listing_data, edit_run_with(row, edits)))
        async with limit:
            try:
                judgement = await judge(instructions, case_text, row["decision"])
            except Exception as error:  # noqa: BLE001 - one bad call must not lose the rest
                print(f"judge error on {row['case_id']}: {type(error).__name__}")
                return None
        return {
            "case_id": row["case_id"],
            "run": name,
            "output_hash": output_hash(row["decision"]),
            "action": row["decision"]["action"],
            **judgement.model_dump(),
            **stamp,
        }

    with judge_agent().override(model=resolve_model(model)):
        done = await asyncio.gather(*(one(name, row) for name, row in picked))
    judged = [row for row in done if row]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row) + "\n" for row in judged))

    print(f"judge: {model}  outputs judged: {len(judged)} of {len(picked)}")
    print(f"verdicts: {dict(Counter(row['verdict'] for row in judged))}")
    print(f"policy gaps raised: {sum(bool(row['policy_gap']) for row in judged)}")
    pairs: dict[str, list[tuple[str, str]]] = {"all": [], "judge-dev": [], "judge-test": []}
    for row in judged:
        label = verdicts.get((row["case_id"], row["output_hash"]))
        if label:
            pair = (label.verdict or "", row["verdict"])
            pairs["all"].append(pair)
            pairs[half(by_id[row["case_id"]].thread.thread_id)].append(pair)
    for name, group in pairs.items():
        if group:
            print(f"agreement, {name}: {agreement(group)}")
    print(f"judgements: {args.out}")
    logfire.force_flush()


class _PageHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # A page is rewritten by every `review` or `browse`: never show a stale one.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # request lines would put thread ids in the terminal


def page_server(directory: Path, port: int) -> http.server.ThreadingHTTPServer:
    """Serves the pages to this machine only: they hold applicants' emails."""
    handler = functools.partial(_PageHandler, directory=str(directory))
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


async def serve(args: argparse.Namespace) -> None:
    if not args.data.is_dir():
        raise SystemExit(
            f"{args.data} is not a folder. See evals/README.md, 'Where the data lives'."
        )
    server = page_server(args.data, args.port)
    base = f"http://localhost:{args.port}"
    print(f"review:  {base}/review.html")
    print(f"dataset: {base}/dataset.html")
    try:
        await asyncio.to_thread(server.serve_forever)
    finally:
        server.server_close()


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
    run_parser.add_argument(
        "--listings", "--operating", dest="listings", type=Path, required=True, help=LISTINGS_HELP
    )
    run_parser.add_argument("--splits", type=Path, help="Split file; without it every scenario")
    run_parser.add_argument("--split", choices=SPLITS, default="dev")
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--sample", type=int, default=0)
    run_parser.add_argument("--seed", type=int, default=0)
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.add_argument("--model", help="Model to run on instead of the configured one")
    run_parser.add_argument(
        "--exclude-run",
        type=Path,
        action="append",
        default=[],
        help="Leave out scenarios this earlier run already covered; repeat for several",
    )
    run_parser.add_argument(
        "--cases-from", type=Path, help="Run exactly the cases an earlier run covered"
    )
    run_parser.add_argument("--case-edits", type=Path, help="Hand-set tour times, by scenario")
    run_parser.add_argument(
        "--carry",
        type=Path,
        help="An earlier run: run again only its cases that have a new edit, keep its other rows",
    )
    run_parser.set_defaults(handler=run)

    review_parser = commands.add_parser("review", help="Write a page for labelling a run")
    review_parser.add_argument("--scenarios", type=Path, required=True)
    review_parser.add_argument(
        "--listings", "--operating", dest="listings", type=Path, required=True, help=LISTINGS_HELP
    )
    review_parser.add_argument("--run", type=Path, required=True)
    review_parser.add_argument("--out", type=Path, required=True)
    review_parser.add_argument("--labels", type=Path, help="Labels to start from")
    review_parser.add_argument("--case-edits", type=Path, help="Hand-set tour times, by scenario")
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

    collect_parser = commands.add_parser(
        "collect", help="Add downloaded labels to the annotations store"
    )
    collect_parser.add_argument("--labels", type=Path, required=True)
    collect_parser.add_argument(
        "--store", type=Path, default=DATA_DIR / "annotations" / "annotations.jsonl"
    )
    collect_parser.add_argument("--runs", type=Path, default=DATA_DIR / "runs")
    collect_parser.set_defaults(handler=collect)

    judge_parser = commands.add_parser("judge", help="Grade outputs with the LLM judge")
    judge_parser.add_argument("--scenarios", type=Path, required=True)
    judge_parser.add_argument(
        "--listings", "--operating", dest="listings", type=Path, required=True, help=LISTINGS_HELP
    )
    judge_parser.add_argument("--run", type=Path, action="append", required=True)
    judge_parser.add_argument("--case-edits", type=Path, help="Hand-set tour times, by scenario")
    judge_parser.add_argument("--labels", type=Path, help="The annotations store, to compare with")
    judge_parser.add_argument(
        "--only-labelled", action="store_true", help="Judge only outputs a person has graded"
    )
    judge_parser.add_argument("--out", type=Path, required=True)
    judge_parser.add_argument("--concurrency", type=int, default=6)
    judge_parser.add_argument("--model", help="Model to judge with instead of the configured one")
    judge_parser.set_defaults(handler=judge_runs)

    serve_parser = commands.add_parser("serve", help="Serve the pages on this machine only")
    serve_parser.add_argument("--data", type=Path, default=DATA_DIR)
    serve_parser.add_argument("--port", type=int, default=SERVE_PORT)
    serve_parser.set_defaults(handler=serve)

    args = parser.parse_args()
    asyncio.run(args.handler(args))
