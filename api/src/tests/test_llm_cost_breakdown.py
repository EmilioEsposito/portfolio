"""Reconciliation tests for per-bucket LLM cost breakdown.

Ensures compute_cost_breakdown() sums to the same total as genai-prices'
authoritative calc_price() — which is what pydantic_ai uses to set
`operation.cost` on LLM spans. If these drift, the dashboard v2 panel will
diverge from the totals panel.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from genai_prices import Usage, calc_price

from api.src.utils.llm_cost_breakdown import compute_cost_breakdown


RECONCILE_TOLERANCE = Decimal("0.0001")  # 0.01%


@pytest.mark.parametrize(
    "model, usage_kwargs",
    [
        # gpt-5.4 with a mix of cached + uncached + output
        (
            "gpt-5.4",
            dict(
                input_tokens=100_000,
                cache_read_tokens=40_000,
                output_tokens=5_000,
            ),
        ),
        # claude-sonnet-4-6 with cache write + cache read + uncached
        (
            "claude-sonnet-4-6",
            dict(
                input_tokens=50_000,
                cache_read_tokens=15_000,
                cache_write_tokens=5_000,
                output_tokens=2_000,
            ),
        ),
        # output-heavy reasoning run
        (
            "gpt-5.4",
            dict(
                input_tokens=10_000,
                output_tokens=80_000,
            ),
        ),
        # no cache usage
        (
            "claude-sonnet-4-6",
            dict(input_tokens=25_000, output_tokens=3_000),
        ),
    ],
)
def test_breakdown_reconciles_with_total(model: str, usage_kwargs: dict) -> None:
    breakdown = compute_cost_breakdown(model, **usage_kwargs)
    assert breakdown is not None, f"{model} not priced in genai-prices"

    our_total = Decimal(str(sum(breakdown.values())))
    authoritative = calc_price(Usage(**usage_kwargs), model).total_price

    if authoritative == 0:
        assert our_total == 0
        return

    diff_ratio = abs(our_total - authoritative) / authoritative
    assert diff_ratio < RECONCILE_TOLERANCE, (
        f"breakdown sum {our_total} != total {authoritative} for {model} "
        f"(diff {diff_ratio * 100:.4f}%)"
    )


def test_unknown_model_returns_none() -> None:
    assert compute_cost_breakdown("not-a-real-model-xyz", input_tokens=100) is None
