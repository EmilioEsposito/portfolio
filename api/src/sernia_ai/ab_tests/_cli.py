"""Shared argparse wiring for A/B experiment scripts.

Each experiment module imports `build_parser()` and `resolve_variants()` so the
CLI surface (`--experiment-name`, `--variant`, `--with-tools`, etc.) is
identical across experiments without duplication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from api.src.sernia_ai.ab_tests._core import DEFAULT_VARIANTS


def build_parser(description: str, *, default_experiment_prefix: str) -> argparse.ArgumentParser:
    """Create a parser with the standard A/B flags.

    `default_experiment_prefix` is used to build a sensible default
    `--experiment-name` value (timestamped) per experiment module.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--experiment-name",
        default=f"{default_experiment_prefix}-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S')}",
        help="Tag applied (via metadata + Dataset name) to every run in the experiment.",
    )
    parser.add_argument(
        "--variant",
        action="append",
        default=None,
        help="Variant as name=model_string. Repeat for multiple. Default: sonnet-4-6 + gpt-5.4.",
    )
    parser.add_argument(
        "--with-tools",
        action="store_true",
        help="Include sernia's full production toolsets. Default off: toolsets=[] to prevent side effects.",
    )
    parser.add_argument(
        "--thinking",
        choices=("minimal", "low", "medium", "high", "xhigh"),
        default="medium",
        help="Unified reasoning-effort level for both variants.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Max concurrent case evaluations per variant. Default: unlimited.",
    )
    return parser


def resolve_variants(values: list[str] | None) -> dict[str, str]:
    """Parse `--variant name=model_string` into a dict, or return defaults."""
    if not values:
        return DEFAULT_VARIANTS
    result: dict[str, str] = {}
    for v in values:
        if "=" not in v:
            raise SystemExit(f"--variant must be name=model_string, got: {v!r}")
        name, _, model = v.partition("=")
        result[name.strip()] = model.strip()
    return result
