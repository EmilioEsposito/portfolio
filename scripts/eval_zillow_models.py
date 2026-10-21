"""Inference-only comparison of the two legacy Zillow functions on synthetic labels.

Run: uv run python -m scripts.eval_zillow_models --output /private/path/report.json
No scheduler, webhook, production emails, or downstream actions are executed.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from scripts.dev_config import resolve

MODELS = ("openai/gpt-4o-mini", "openai/gpt-6-luna")
DATASET = Path("api/src/tests/eval_data/zillow_thread_cases.json")
SOURCE = Path("api/src/zillow_email/service.py")


def normalize(value: object, field: str) -> object:
    """Ignore phone formatting; all other extracted fields must match exactly."""
    if field == "lead_phone_number" and isinstance(value, str):
        return "".join(c for c in value if c.isdigit())
    return value


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=(1, 2), default=2)
    parser.add_argument("--limit", type=int, choices=range(1, 13), default=12)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a fresh output path to preserve earlier evidence.")

    # Use the service configuration resolver, without copying unrelated credentials.
    env, _ = resolve("api", "auto", dict(os.environ))
    key = env.get("PORTFOLIO_OPENROUTER_API_KEY")
    if not key:
        parser.error("PORTFOLIO_OPENROUTER_API_KEY is not configured.")
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        LOGFIRE_SEND_TO_LOGFIRE="false",
        BACKGROUND_AUTOMATION_ENABLED="false",
        DATABASE_URL="postgresql://unused:unused@localhost/unused",
        DATABASE_URL_UNPOOLED="postgresql://unused:unused@localhost/unused",
        CLERK_SECRET_KEY="unused",
        PORTFOLIO_OPENROUTER_API_KEY=key,
    )
    from openai import OpenAI

    from api.src.utils.logfire_config import ensure_logfire_configured

    ensure_logfire_configured(mode="test")
    import logfire

    from api.src.zillow_email import service

    logfire.configure(send_to_logfire=False, console=False)
    assert not logfire.DEFAULT_LOGFIRE_INSTANCE.config.send_to_logfire

    cases = json.loads(DATASET.read_text())[: args.limit]
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "label_source": "synthetic_policy_expectation; not production replay or human adjudication",
        "cases": [c["case_id"] for c in cases],
        "models": MODELS,
        "settings": "Existing prompts, preprocessing, schemas and SDK parse; only model overridden",
        "timeout_seconds": 30,
        "retries": 0,
        "reported_spend_ceiling_usd": 0.50,
        "concurrency": 1,
        "repeats": args.repeats,
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch(mode=0o600, exist_ok=False)

    with OpenAI(
        api_key=key, base_url="https://openrouter.ai/api/v1", timeout=30, max_retries=0
    ) as client:
        for repeat in range(args.repeats):
            for case in cases:
                # Alternate model order to reduce systematic warm-cache/order bias.
                for model in MODELS if repeat % 2 == 0 else reversed(MODELS):
                    for task in ("assessment", "collection"):
                        row = dict(case_id=case["case_id"], repeat=repeat, model=model, task=task)
                        responses = []

                        def parse(
                            _model: str = model, _responses: list = responses, **kwargs: Any
                        ) -> Any:
                            response = client.chat.completions.parse(**{**kwargs, "model": _model})
                            _responses.append(response)
                            return response

                        proxy = SimpleNamespace(
                            chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
                        )
                        messages = [
                            service.EmailMessageDetail(
                                **m,
                                subject=case["subject"],
                                to_address="all@serniacapital.com",
                                email_day_of_week_et_str=datetime.fromisoformat(
                                    m["email_timestamp_et"]
                                ).strftime("%A"),
                            )
                            for m in case["messages"]
                        ]
                        started = perf_counter()
                        try:
                            with ExitStack() as stack:
                                stack.enter_context(
                                    patch.object(service, "openrouter_client", return_value=proxy)
                                )
                                for name in (
                                    "AsyncSessionFactory",
                                    "send_message",
                                    "upsert_openphone_contact",
                                    "get_calendar_service",
                                    "create_calendar_event",
                                ):
                                    stack.enter_context(
                                        patch.object(
                                            service,
                                            name,
                                            side_effect=AssertionError("Action blocked"),
                                        )
                                    )
                                if task == "assessment":
                                    reply, reason, scheduled = await service.ai_assess_thread(
                                        case["case_id"], messages
                                    )
                                    output = dict(
                                        should_reply=reply,
                                        reason=reason,
                                        appointment_scheduled=scheduled,
                                    )
                                    expected = case["expected_assessment"]
                                else:
                                    info, _ = await service.ai_collect_thread_info(
                                        case["case_id"], messages
                                    )
                                    output = info.model_dump()
                                    expected = case["expected_collection"]
                            mismatches = {
                                k: {"expected": v, "actual": output[k]}
                                for k, v in expected.items()
                                if normalize(output[k], k) != normalize(v, k)
                            }
                            usage = responses[0].usage.model_dump() if responses[0].usage else {}
                            row.update(
                                status="ok",
                                output=output,
                                mismatches=mismatches,
                                passed=not mismatches,
                                usage=usage,
                                reported_cost=usage.get("cost"),
                                response_model=responses[0].model,
                            )
                        except Exception as exc:
                            row.update(status="error", error_type=type(exc).__name__)
                        row["seconds"] = perf_counter() - started
                        report["results"].append(row)
                        args.output.write_text(json.dumps(report, indent=2) + "\n")
                        print(
                            json.dumps(
                                {k: v for k, v in row.items() if k not in ("output", "usage")}
                            ),
                            flush=True,
                        )
                        if row["status"] == "error":
                            raise SystemExit(
                                "Inference failed; evidence saved. Resolve before continuing."
                            )
                        if sum(r.get("reported_cost") or 0 for r in report["results"]) >= 0.50:
                            raise SystemExit("Reported spend ceiling reached; evidence saved.")

    summary = {}
    for model in MODELS:
        rows = [r for r in report["results"] if r["model"] == model]
        summary[model] = {
            "successful": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "median_seconds": statistics.median(r["seconds"] for r in rows),
            "reported_cost_coverage": sum(r["reported_cost"] is not None for r in rows),
            "reported_cost": sum(r["reported_cost"] or 0 for r in rows),
            "by_task": {
                task: {
                    "attempted": sum(r["task"] == task for r in rows),
                    "passed": sum(r["passed"] for r in rows if r["task"] == task),
                }
                for task in ("assessment", "collection")
            },
        }
    report["summary"] = summary
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
