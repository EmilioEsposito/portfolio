"""The two pages a person reads: one to label a run, one to read the dataset.

Each is a single self-contained HTML file: a template from ``templates/`` with
the data written into it. Both hold applicant emails, so they are written to
``evals/data``, which Git ignores.
"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from api.src.sernia_ai.zillow_leads.case import OperatingData, render_case
from api.src.sernia_ai.zillow_leads.evals.labels import Label, output_hash
from api.src.sernia_ai.zillow_leads.evals.scenarios import SPLITS, Scenario, case_for, in_split

TEMPLATES = Path(__file__).with_name("templates")
REVIEW_TEMPLATE = TEMPLATES / "review.html"
DATASET_TEMPLATE = TEMPLATES / "dataset.html"


def _recorded(decision: dict) -> str:
    """The lead facts and tour state the model recorded, on one line."""
    lead = [f"{name}: {value}" for name, value in decision["lead"].items() if value]
    tour = decision["tour"]
    state = f"tour: {tour['status']}" + (f" at {tour['start']}" if tour.get("start") else "")
    return " · ".join([*lead, state])


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
                "hash": output_hash(decision),
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
                name: {row["case_id"]: output_hash(row["decision"]) for row in old_rows}
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
