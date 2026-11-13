"""A person's verdict on one output of the lead agent, and where verdicts accumulate.

"Label" and "annotation" mean the same row here: a verdict, the reviewer's note
and, sometimes, the reply they would have sent. The review page downloads a
file of them. ``add_annotations`` appends those to one store that only grows,
with the output each one judged copied in beside it, so a verdict can be read
years later without the run it came from.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


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
    # Set when the label joins the store. A verdict holds for the rules in
    # force when it was given, so the date says which rules those were.
    annotated_at: datetime | None = None
    # The action and words that were judged, copied from the run: what a
    # regression check or an LLM judge needs to pair with the verdict.
    output: dict | None = None
    # A short hash of the instructions the judged run was made with. Criteria
    # move as a reviewer reads more outputs, so a verdict is read against the
    # rules of its day. Empty on rows stored before this was recorded; the
    # run's ``.policy.md`` snapshot gives it.
    policy: str = ""


def judged(decision: dict) -> dict:
    """The part of an output a reviewer judges: the action and its words."""
    return {
        "action": decision["action"],
        "reply_body": decision.get("reply_body"),
        "question": decision.get("question"),
    }


def output_hash(decision: dict) -> str:
    """Identifies one output: the action and the words a reviewer judges."""
    return hashlib.sha256(json.dumps(list(judged(decision).values())).encode()).hexdigest()[:12]


def read_labels(path: Path) -> list[Label]:
    """Labels from the store (``.jsonl``), a file the review page downloaded,
    or a plain list of them."""
    if path.suffix == ".jsonl":
        return [Label.model_validate_json(line) for line in path.read_text().splitlines() if line]
    content = json.loads(path.read_text())
    rows = content["labels"] if isinstance(content, dict) else content
    # A download says when it was exported; a bare list only has its file time.
    exported = content.get("exported_at") if isinstance(content, dict) else None
    saved_at = (
        datetime.fromisoformat(exported.replace("Z", "+00:00"))
        if exported
        else datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    )
    labels = [Label.model_validate(row) for row in rows]
    return [
        label if label.annotated_at else label.model_copy(update={"annotated_at": saved_at})
        for label in labels
    ]


def latest_per_scenario(labels: list[Label]) -> list[Label]:
    """The most recent verdict on each scenario, in the order first seen."""
    latest: dict[str, Label] = {}
    for label in labels:
        current = latest.get(label.scenario_id)
        if current is None or (label.annotated_at or datetime.min.replace(tzinfo=UTC)) >= (
            current.annotated_at or datetime.min.replace(tzinfo=UTC)
        ):
            latest[label.scenario_id] = label
    return list(latest.values())


def latest_per_output(labels: list[Label]) -> dict[tuple[str, str], Label]:
    """The most recent verdict on each output, by scenario and output hash.

    A verdict belongs to one output. A changed verdict is a later row, so the
    later one is the reviewer's current view of that output.
    """
    latest: dict[tuple[str, str], Label] = {}
    floor = datetime.min.replace(tzinfo=UTC)
    for label in labels:
        if not label.verdict:
            continue
        key = (label.scenario_id, label.output_hash)
        current = latest.get(key)
        if current is None or (label.annotated_at or floor) >= (current.annotated_at or floor):
            latest[key] = label
    return latest


def _same_verdict(label: Label) -> tuple[str, str, str | None, str, str]:
    return (
        label.scenario_id,
        label.output_hash,
        label.verdict,
        label.note,
        label.corrected_reply,
    )


def add_annotations(store: Path, labels: list[Label], runs_dir: Path) -> dict[str, int]:
    """Append new verdicts to the store. Nothing in it is ever rewritten.

    A row is new unless the same verdict, note and corrected reply on the same
    output is already there, so adding a download twice changes nothing, and a
    changed verdict becomes a later row. Rows without a verdict are skipped.
    """
    known = {_same_verdict(label) for label in read_labels(store)} if store.exists() else set()
    outputs: dict[str, dict[tuple[str, str], dict]] = {}
    counts = {"added": 0, "already there": 0, "no verdict": 0, "output not found": 0}
    lines: list[str] = []
    for label in labels:
        if label.verdict is None:
            counts["no verdict"] += 1
            continue
        if _same_verdict(label) in known:
            counts["already there"] += 1
            continue
        if label.output is None and label.run:
            if label.run not in outputs:
                run_file = runs_dir / f"{label.run}.jsonl"
                rows = (
                    [json.loads(line) for line in run_file.read_text().splitlines() if line]
                    if run_file.exists()
                    else []
                )
                outputs[label.run] = {
                    (row["case_id"], output_hash(row["decision"])): judged(row["decision"])
                    for row in rows
                }
            found = outputs[label.run].get((label.scenario_id, label.output_hash))
            if found is None:
                counts["output not found"] += 1
            label = label.model_copy(update={"output": found})
        snapshot = runs_dir / f"{label.run}.policy.md"
        if label.run and not label.policy and snapshot.exists():
            policy = hashlib.sha256(snapshot.read_bytes()).hexdigest()[:8]
            label = label.model_copy(update={"policy": policy})
        known.add(_same_verdict(label))
        lines.append(label.model_dump_json() + "\n")
        counts["added"] += 1
    if lines:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a") as out:
            out.writelines(lines)
    return counts
