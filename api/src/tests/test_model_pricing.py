"""Guardrail: every model the Sernia agent can use must be priced by genai-prices.

`operation.cost` is stamped on each LLM span at emit time by pydantic-ai via
genai-prices. If a model isn't in the pinned genai-prices data, no cost is
recorded and the run silently vanishes from the LLM-cost dashboard (which
filters `operation.cost is not null`). See the "Unpriced Models" panel in
`logfire/dashboards/llm-cost` for the prod-side backstop.

This test is the dev/CI catch: introduce a new model/version in
`sernia_ai/model_config.py` or `sernia_ai/config.py` that genai-prices doesn't
know, and this fails — telling you to bump `genai-prices` (and add a v1
hardcoded tier in `cost_by_token_type.sql`) as part of the change.

No API keys needed — genai-prices ships an offline price snapshot.
"""

import pytest


def _sernia_model_ids() -> list[str]:
    """Bare model ids for every model the Sernia agent can run.

    Strips pydantic-ai's ``provider:`` prefix (e.g. ``openai-responses:gpt-5.6``
    -> ``gpt-5.6``) to the id genai-prices / ``gen_ai.request.model`` use.
    """
    from api.src.sernia_ai.config import MAIN_AGENT_MODEL, SUB_AGENT_MODEL
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    strings = {m.model_string for m in AVAILABLE_MODELS}
    strings.add(MAIN_AGENT_MODEL)
    strings.add(SUB_AGENT_MODEL)
    return sorted(s.split(":", 1)[1] if ":" in s else s for s in strings)


@pytest.mark.parametrize("model_id", _sernia_model_ids())
def test_sernia_model_is_priced_by_genai_prices(model_id: str):
    from api.src.utils.llm_cost_breakdown import compute_cost_breakdown

    breakdown = compute_cost_breakdown(model_id, input_tokens=1000, output_tokens=100)
    assert breakdown is not None, (
        f"{model_id!r} is not priced by the installed genai-prices. "
        "Bump `genai-prices` (and add a hardcoded tier in "
        "logfire/dashboards/llm-cost/queries/cost_by_token_type.sql) as part of "
        "introducing this model — otherwise operation.cost is never stamped and "
        "the model vanishes from the LLM-cost dashboard."
    )
