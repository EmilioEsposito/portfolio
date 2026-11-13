"""The annotations store: verdicts accumulate, nothing is rewritten (no network)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from api.src.sernia_ai.zillow_leads.evals.cli import not_in_runs
from api.src.sernia_ai.zillow_leads.evals.labels import (
    Label,
    add_annotations,
    latest_per_output,
    latest_per_scenario,
    output_hash,
    read_labels,
)

DECISION = {"action": "reply", "reply_body": "Sure, see you then.", "question": None}
OTHER = {"action": "escalate", "reply_body": None, "question": "Which unit?"}


def _run(runs: Path, name: str, rows: dict[str, dict]) -> None:
    runs.mkdir(exist_ok=True)
    (runs / f"{name}.jsonl").write_text(
        "".join(json.dumps({"case_id": case, "decision": d}) + "\n" for case, d in rows.items())
    )


def _download(path: Path, run: str, rows: list[dict], exported_at: str) -> Path:
    path.write_text(json.dumps({"run": run, "exported_at": exported_at, "labels": rows}))
    return path


def _row(scenario: str, decision: dict, verdict: str | None, run: str, note: str = "") -> dict:
    return {
        "scenario_id": scenario,
        "verdict": verdict,
        "note": note,
        "action": decision["action"],
        "output_hash": output_hash(decision),
        "run": run,
    }


class TestStore:
    def test_a_download_is_added_once_with_the_output_it_judged(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION, "T-2": OTHER})
        download = _download(
            tmp_path / "labels.json",
            "experiment-5",
            [
                _row("T-1", DECISION, "pass", "experiment-5"),
                _row("T-2", OTHER, "fail", "experiment-5", note="should have replied"),
                _row("T-3", DECISION, None, "experiment-5"),  # skipped by the reviewer
            ],
            "2026-11-12T02:48:40.204Z",
        )
        store = tmp_path / "annotations" / "annotations.jsonl"

        first = add_annotations(store, read_labels(download), tmp_path / "runs")
        again = add_annotations(store, read_labels(download), tmp_path / "runs")

        assert first == {"added": 2, "already there": 0, "no verdict": 1, "output not found": 0}
        assert again["added"] == 0 and again["already there"] == 2
        stored = read_labels(store)
        assert [label.scenario_id for label in stored] == ["T-1", "T-2"]
        assert stored[1].output == OTHER
        assert stored[0].annotated_at == datetime(2026, 11, 12, 2, 48, 40, 204000, tzinfo=UTC)

    def test_a_changed_verdict_is_a_later_row_and_the_first_stays(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION})
        store = tmp_path / "annotations.jsonl"
        early = _download(
            tmp_path / "a.json",
            "experiment-5",
            [_row("T-1", DECISION, "pass", "experiment-5")],
            "2026-11-12T00:00:00Z",
        )
        late = _download(
            tmp_path / "b.json",
            "experiment-5",
            [_row("T-1", DECISION, "fail", "experiment-5", note="on reflection, too curt")],
            "2026-11-13T00:00:00Z",
        )

        add_annotations(store, read_labels(early), tmp_path / "runs")
        first_line = store.read_text()
        add_annotations(store, read_labels(late), tmp_path / "runs")

        assert store.read_text().startswith(first_line)
        assert [label.verdict for label in read_labels(store)] == ["pass", "fail"]
        assert [label.verdict for label in latest_per_scenario(read_labels(store))] == ["fail"]

    def test_a_verdict_changed_back_is_kept_as_the_latest(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION})
        store = tmp_path / "annotations.jsonl"
        downloads = [
            _download(
                tmp_path / f"{day}.json",
                "experiment-5",
                [_row("T-1", DECISION, verdict, "experiment-5")],
                f"2026-11-{day}T00:00:00Z",
            )
            for day, verdict in (("12", "pass"), ("13", "fail"), ("14", "pass"))
        ]

        for download in downloads:
            add_annotations(store, read_labels(download), tmp_path / "runs")

        stored = read_labels(store)
        assert [label.verdict for label in stored] == ["pass", "fail", "pass"]
        assert latest_per_output(stored)[("T-1", output_hash(DECISION))].verdict == "pass"
        assert [label.verdict for label in latest_per_scenario(stored)] == ["pass"]

    def test_an_old_download_added_again_changes_nothing(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION})
        store = tmp_path / "annotations.jsonl"
        early = _download(
            tmp_path / "a.json",
            "experiment-5",
            [_row("T-1", DECISION, "pass", "experiment-5")],
            "2026-11-12T00:00:00Z",
        )
        late = _download(
            tmp_path / "b.json",
            "experiment-5",
            [_row("T-1", DECISION, "fail", "experiment-5")],
            "2026-11-13T00:00:00Z",
        )
        add_annotations(store, read_labels(early), tmp_path / "runs")
        add_annotations(store, read_labels(late), tmp_path / "runs")

        again = add_annotations(store, read_labels(early), tmp_path / "runs")

        assert again["added"] == 0 and again["already there"] == 1
        assert [label.verdict for label in read_labels(store)] == ["pass", "fail"]

    def test_the_current_verdict_exported_again_later_is_not_a_new_row(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION})
        store = tmp_path / "annotations.jsonl"
        rows = [_row("T-1", DECISION, "pass", "experiment-5")]
        first = _download(tmp_path / "a.json", "experiment-5", rows, "2026-11-12T00:00:00Z")
        second = _download(tmp_path / "b.json", "experiment-5", rows, "2026-11-13T00:00:00Z")
        add_annotations(store, read_labels(first), tmp_path / "runs")

        again = add_annotations(store, read_labels(second), tmp_path / "runs")

        assert again["added"] == 0 and len(read_labels(store)) == 1

    def test_a_verdict_records_the_policy_its_run_was_made_with(self, tmp_path: Path):
        _run(tmp_path / "runs", "experiment-5", {"T-1": DECISION})
        (tmp_path / "runs" / "experiment-5.policy.md").write_text("Reply when owed.")
        _run(tmp_path / "runs", "experiment-6", {"T-1": OTHER})
        (tmp_path / "runs" / "experiment-6.policy.md").write_text("Reply when owed. Greet first.")
        store = tmp_path / "annotations.jsonl"
        labels = [
            Label(**_row("T-1", DECISION, "pass", "experiment-5")),
            Label(**_row("T-1", OTHER, "fail", "experiment-6")),
        ]

        add_annotations(store, labels, tmp_path / "runs")

        first, second = read_labels(store)
        assert len(first.policy) == 8 and first.policy != second.policy

    def test_a_verdict_whose_run_is_gone_is_kept_without_its_output(self, tmp_path: Path):
        store = tmp_path / "annotations.jsonl"
        label = Label(scenario_id="T-9", verdict="fail", output_hash="abc", run="lost-run")

        counts = add_annotations(store, [label], tmp_path / "runs")

        assert counts["added"] == 1 and counts["output not found"] == 1
        assert read_labels(store)[0].output is None


class TestFreshSample:
    def test_scenarios_an_earlier_run_covered_are_left_out(self, tmp_path: Path):
        class _Scenario:
            def __init__(self, scenario_id: str) -> None:
                self.scenario_id = scenario_id

        _run(tmp_path, "experiment-1", {"T-1": DECISION})
        _run(tmp_path, "experiment-2", {"T-2": OTHER})
        scenarios = [_Scenario("T-1"), _Scenario("T-2"), _Scenario("T-3")]

        fresh = not_in_runs(
            scenarios, [tmp_path / "experiment-1.jsonl", tmp_path / "experiment-2.jsonl"]
        )

        assert [scenario.scenario_id for scenario in fresh] == ["T-3"]
