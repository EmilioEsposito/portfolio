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


def test_breakdown_processor_attrs_reach_the_exporter() -> None:
    """Pins the write path against the installed OTel SDK.

    The SDK freezes span attributes before calling processors, so a processor
    that writes in ``on_end`` silently no-ops (this processor did, unnoticed,
    until 2026-11). The exporter is registered first, as logfire's are in prod.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from api.src.utils.llm_cost_breakdown import (
        ATTR_COST_INPUT_NON_CACHED,
        ATTR_COST_OUTPUT,
        CostBreakdownSpanProcessor,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.add_span_processor(CostBreakdownSpanProcessor())

    with provider.get_tracer(__name__).start_as_current_span("chat claude-sonnet-4-6") as span:
        span.set_attributes(
            {
                "operation.cost": 0.0045,
                "gen_ai.request.model": "claude-sonnet-4-6",
                "gen_ai.usage.input_tokens": 1000,
                "gen_ai.usage.output_tokens": 100,
            }
        )

    (exported,) = exporter.get_finished_spans()
    expected = compute_cost_breakdown("claude-sonnet-4-6", input_tokens=1000, output_tokens=100)
    assert expected is not None
    assert exported.attributes[ATTR_COST_INPUT_NON_CACHED] == pytest.approx(
        expected["input_non_cached"]
    )
    assert exported.attributes[ATTR_COST_OUTPUT] == pytest.approx(expected["output"])


@pytest.mark.parametrize(
    ("provider_details", "expected"),
    [
        ({"cost": 0.0125}, 0.0125),  # OpenRouter-billed (not BYOK)
        ({"cost": 0.0125, "is_byok": False}, 0.0125),
        # BYOK: fee is $0 so pydantic-ai drops `cost`; the spend is upstream.
        ({"upstream_inference_cost": 0.0065, "is_byok": True}, 0.0065),
        ({"cost": 0.0001, "upstream_inference_cost": 0.0065, "is_byok": True}, 0.0066),
        # Upstream is only meaningful for BYOK; never double-count it otherwise.
        ({"cost": 0.0125, "upstream_inference_cost": 0.0125}, 0.0125),
        ({}, None),
        (None, None),
        ({"cost": True}, None),
    ],
)
def test_gateway_billed_cost(provider_details: dict | None, expected: float | None) -> None:
    from api.src.utils.llm_cost_breakdown import gateway_billed_cost

    result = gateway_billed_cost(provider_details)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
