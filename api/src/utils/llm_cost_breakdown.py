"""Per-bucket LLM cost breakdown, emitted as span attributes.

Pydantic AI's instrumentation only emits `operation.cost` (total) plus token
counts. For the cost-by-token-type dashboard we want per-bucket costs:
input_non_cached, cache_read, cache_write, output.

Rather than hard-code pricing in SQL (which drifts as new models launch), we
compute each bucket here using `genai_prices` (the same data source pydantic_ai
already uses for `operation.cost`) and attach the results as span attributes.
The SQL then just sums them.

Approach: look up the model's `ModelPrice` once, then call `calc_mtok_price`
per bucket. This mirrors `ModelPrice.calc_price` in genai-prices and preserves
tiered-pricing correctness (tier is chosen from real total_input_tokens).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from genai_prices import data_snapshot, types as gp_types
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

logger = logging.getLogger(__name__)

# Span attribute names we read from the LLM call span.
_ATTR_MODEL = "gen_ai.request.model"
_ATTR_COST = "operation.cost"
_ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
_ATTR_CACHE_READ = "gen_ai.usage.details.cache_read_tokens"
_ATTR_CACHE_WRITE = "gen_ai.usage.details.cache_write_tokens"

# Span attribute names we write.
ATTR_COST_INPUT_NON_CACHED = "gen_ai.cost.input_non_cached"
ATTR_COST_CACHE_READ = "gen_ai.cost.cache_read"
ATTR_COST_CACHE_WRITE = "gen_ai.cost.cache_write"
ATTR_COST_OUTPUT = "gen_ai.cost.output"


def compute_cost_breakdown(
    model: str,
    *,
    input_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, float] | None:
    """Return per-bucket cost in USD, or None if the model isn't priced.

    `input_tokens` is the TOTAL input token count (inclusive of cached/written).
    Semantics match `genai_prices.types.Usage` and pydantic_ai's RequestUsage.
    """
    snapshot = data_snapshot.get_snapshot()
    try:
        _, model_info = snapshot.find_provider_model(model, None, None, None)
    except Exception:
        return None
    if model_info is None:
        return None
    # `prices` may be a ModelPrice or a list of ConditionalPrice entries —
    # `get_prices(timestamp)` normalizes to a single ModelPrice for a given time.
    prices = model_info.get_prices(datetime.now(tz=timezone.utc))

    uncached_input = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    total_input = input_tokens  # tier lookup uses the real total

    cost_input_non_cached = gp_types.calc_mtok_price(prices.input_mtok, uncached_input, total_input)
    cost_cache_read = gp_types.calc_mtok_price(prices.cache_read_mtok, cache_read_tokens, total_input)
    cost_cache_write = gp_types.calc_mtok_price(prices.cache_write_mtok, cache_write_tokens, total_input)
    cost_output = gp_types.calc_mtok_price(prices.output_mtok, output_tokens, total_input)

    return {
        "input_non_cached": float(cost_input_non_cached),
        "cache_read": float(cost_cache_read),
        "cache_write": float(cost_cache_write),
        "output": float(cost_output),
    }


def _get_int_attr(attrs: Any, key: str) -> int:
    """Read an integer span attribute, tolerating missing/None/non-int values."""
    value = attrs.get(key) if attrs else None
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class CostBreakdownSpanProcessor(SpanProcessor):
    """On span end, enrich LLM call spans with per-token-bucket cost attrs.

    Triggers only on spans that already have both `operation.cost` and a model
    name, which pydantic_ai sets together on the LLM call span. Writes directly
    to the span's `_attributes` dict because `Span.set_attribute()` silently
    no-ops after end() per OTel Python SDK semantics.
    """

    def on_start(self, span, parent_context=None):  # pragma: no cover - no-op
        pass

    def on_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes
        if not attrs:
            return
        if attrs.get(_ATTR_COST) is None:
            return
        model = attrs.get(_ATTR_MODEL)
        if not model:
            return

        breakdown = compute_cost_breakdown(
            str(model),
            input_tokens=_get_int_attr(attrs, _ATTR_INPUT_TOKENS),
            cache_read_tokens=_get_int_attr(attrs, _ATTR_CACHE_READ),
            cache_write_tokens=_get_int_attr(attrs, _ATTR_CACHE_WRITE),
            output_tokens=_get_int_attr(attrs, _ATTR_OUTPUT_TOKENS),
        )
        if breakdown is None:
            return

        # Span is ended; set_attribute() logs a warning and no-ops. The
        # underlying `_attributes` BoundedAttributes dict is still mutable.
        mutable_attrs = getattr(span, "_attributes", None)
        if mutable_attrs is None:
            return
        try:
            mutable_attrs[ATTR_COST_INPUT_NON_CACHED] = breakdown["input_non_cached"]
            mutable_attrs[ATTR_COST_CACHE_READ] = breakdown["cache_read"]
            mutable_attrs[ATTR_COST_CACHE_WRITE] = breakdown["cache_write"]
            mutable_attrs[ATTR_COST_OUTPUT] = breakdown["output"]
        except Exception:
            logger.debug("failed to attach cost breakdown attributes", exc_info=True)

    def shutdown(self) -> None:  # pragma: no cover - no resources held
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # pragma: no cover
        return True
