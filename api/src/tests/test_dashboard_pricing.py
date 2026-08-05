"""Guardrail: the LLM-cost dashboard's hard-coded rates must match genai-prices.

`logfire/dashboards/llm-cost/queries/cost_by_token_type.sql` (the v1 panel)
prices each token bucket with `case model like '...' then <rate>` branches. Those
rates are a hand-maintained copy of what genai-prices knows, and they had
silently rotted: `gpt-5.6-luna` was priced at $1.00/$6.00 per MTok when OpenAI
actually charges $0.20/$1.20, overstating Sernia AI's reported spend ~5x, and
`claude-opus` had no branch at all (so an Opus run costed as $0).

This test re-derives every rate from genai-prices and fails on any mismatch, so
the next price change or model addition can't quietly skew the dashboard. When
it fails, either fix the SQL rate or bump `genai-prices` — whichever is stale.

No API keys needed — genai-prices ships an offline price snapshot.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SQL_PATH = (
    Path(__file__).resolve().parents[3]
    / "logfire"
    / "dashboards"
    / "llm-cost"
    / "queries"
    / "cost_by_token_type.sql"
)

# SQL bucket alias -> the genai-prices ModelPrice field it should mirror.
_BUCKET_TO_PRICE_FIELD = {
    "cost_input_non_cached": "input_mtok",
    "cost_cache_input": "cache_read_mtok",
    "cost_cache_write": "cache_write_mtok",
    "cost_output": "output_mtok",
}

# `model like '<pattern>'` -> a concrete model id genai-prices can price.
# A pattern covers a whole family at one rate, so the representative is the
# version we actually run (or the newest, for families we don't).
_REPRESENTATIVE_MODEL = {
    "claude-sonnet%": "claude-sonnet-4-6",
    "claude-haiku%": "claude-haiku-4-5-20251001",
    "claude-opus%": "claude-opus-4-7",
    "gpt-4o-mini%": "gpt-4o-mini",
    "gpt-4o%": "gpt-4o",
    "gpt-4.1-mini%": "gpt-4.1-mini",
    "gpt-4.1%": "gpt-4.1",
    "gpt-5.4-nano%": "gpt-5.4-nano",
    "gpt-5.4-mini%": "gpt-5.4-mini",
    "gpt-5.4%": "gpt-5.4",
    "gpt-5.6-luna%": "gpt-5.6-luna",
    "gpt-5.6-terra%": "gpt-5.6-terra",
    "gpt-5.6-sol%": "gpt-5.6-sol",
    # OpenRouter routes — same upstream model, OpenRouter's own rate.
    "openai/gpt-5.6-luna%": "gpt-5.6-luna",
    "openai/gpt-5.6-terra%": "gpt-5.6-terra",
    "openai/gpt-5.6-sol%": "gpt-5.6-sol",
}

# OpenRouter currently bills a flat 50% of OpenAI list across every bucket.
# Verified against real billed amounts (see test_openrouter_integration.py).
# If OpenRouter ends that discount this test fails and the SQL needs updating.
_OPENROUTER_DISCOUNT = 0.5

_BUCKET_RE = re.compile(r"\(case(?P<body>.*?)end\)\s*/\s*1e6\s+as\s+(?P<alias>\w+)", re.DOTALL)
_BRANCH_RE = re.compile(r"when\s+model\s+like\s+'(?P<pattern>[^']+)'\s+then\s+(?P<rate>[\d.]+)")


def _parse_sql_rates() -> dict[str, dict[str, float]]:
    """Return {bucket_alias: {model_pattern: rate_per_mtok}} parsed from the SQL."""
    sql = _SQL_PATH.read_text(encoding="utf-8")
    rates: dict[str, dict[str, float]] = {}
    for block in _BUCKET_RE.finditer(sql):
        alias = block.group("alias")
        rates[alias] = {
            m.group("pattern"): float(m.group("rate"))
            for m in _BRANCH_RE.finditer(block.group("body"))
        }
    return rates


def _base_rate(model_id: str, price_field: str) -> float | None:
    """genai-prices' base (short-context) rate per MTok, or None if unpriced."""
    from genai_prices import data_snapshot

    snapshot = data_snapshot.get_snapshot()
    _, model_info = snapshot.find_provider_model(model_id, None, None, None)
    value = getattr(model_info.get_prices(datetime.now(tz=UTC)), price_field, None)
    if value is None:
        return None
    # Tiered prices expose the short-context rate as `.base`; flat ones are scalar.
    return float(getattr(value, "base", value))


def _cases() -> list[tuple[str, str, float]]:
    return [
        (alias, pattern, rate)
        for alias, branches in _parse_sql_rates().items()
        for pattern, rate in branches.items()
    ]


def test_sql_buckets_were_parsed():
    """Guard the parser itself — a regex that matches nothing would pass vacuously."""
    rates = _parse_sql_rates()
    assert set(rates) == set(_BUCKET_TO_PRICE_FIELD), f"parsed buckets: {sorted(rates)}"
    assert all(branches for branches in rates.values())


def test_every_sql_pattern_has_a_representative_model():
    """A new `model like` branch must be registered here so it gets price-checked."""
    patterns = {p for branches in _parse_sql_rates().values() for p in branches}
    unregistered = sorted(patterns - set(_REPRESENTATIVE_MODEL))
    assert not unregistered, (
        f"dashboard SQL prices models with no representative in this test: {unregistered}. "
        "Add them to _REPRESENTATIVE_MODEL so their rates stay checked against genai-prices."
    )


@pytest.mark.parametrize(("alias", "pattern", "sql_rate"), _cases(), ids=lambda v: str(v))
def test_sql_rate_matches_genai_prices(alias: str, pattern: str, sql_rate: float):
    model_id = _REPRESENTATIVE_MODEL[pattern]
    expected = _base_rate(model_id, _BUCKET_TO_PRICE_FIELD[alias])

    assert expected is not None, (
        f"{pattern!r} has a {alias} rate of {sql_rate} in the dashboard SQL, but "
        f"genai-prices has no {_BUCKET_TO_PRICE_FIELD[alias]} price for {model_id!r}. "
        "Remove the branch or bump genai-prices."
    )
    via_openrouter = pattern.startswith("openai/")
    if via_openrouter:
        expected *= _OPENROUTER_DISCOUNT
    discount_note = f", x{_OPENROUTER_DISCOUNT} OpenRouter discount" if via_openrouter else ""

    assert sql_rate == pytest.approx(expected, rel=1e-6), (
        f"{pattern!r} {alias}: dashboard SQL says ${sql_rate}/MTok, genai-prices says "
        f"${expected}/MTok (model {model_id!r}{discount_note}). "
        "Fix the rate in logfire/dashboards/llm-cost/queries/cost_by_token_type.sql "
        "(then re-run logfire/dashboards/render.py), or bump genai-prices if it's the stale one."
    )


def test_sernia_selectable_models_are_all_priced_by_the_dashboard():
    """Every model the runtime picker offers must hit a branch, not fall to `else 0`.

    `claude-opus` was missing before this test existed, so selecting Opus 4.7
    would have shown $0 on the by-token-type panel.
    """
    from api.src.sernia_ai.config import MAIN_AGENT_MODEL, SUB_AGENT_MODEL
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    patterns = {p for branches in _parse_sql_rates().values() for p in branches}
    model_ids = {
        s.split(":", 1)[1] if ":" in s else s
        for s in ({m.model_string for m in AVAILABLE_MODELS} | {MAIN_AGENT_MODEL, SUB_AGENT_MODEL})
    }

    unmatched = sorted(
        model_id
        for model_id in model_ids
        if not any(model_id.startswith(p.removesuffix("%")) for p in patterns)
    )
    assert not unmatched, (
        f"these selectable models hit no pricing branch in the dashboard SQL: {unmatched}. "
        "They would be costed as $0 on the cost-by-token-type panel."
    )
