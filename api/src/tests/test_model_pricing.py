"""Guardrail: every model the Sernia agent can use must have a cost signal.

`operation.cost` is stamped on each LLM span and the LLM-cost dashboard filters
`operation.cost is not null`. A model with no cost signal silently vanishes from
it. See the "Unpriced Models" panel in `logfire/dashboards/llm-cost` for the
prod-side backstop.

There are two cost signals, one per gateway:

* **Direct-provider models** (`anthropic:...`) are priced by genai-prices,
  which pydantic-ai calls to stamp `operation.cost`. Introduce a new
  model/version genai-prices doesn't know and `test_direct_model_is_priced...`
  fails — telling you to bump `genai-prices` (and add a v1 hardcoded tier in
  `cost_by_token_type.sql`) as part of the change.
* **OpenRouter routes** (`openrouter:...`) are *not* in genai-prices' data —
  neither the pinned snapshot nor upstream main carries e.g.
  `openai/gpt-5.6-luna` under the `openrouter` provider. `ModelResponse.cost()`
  raises LookupError there. Instead we ask OpenRouter for the real billed
  amount (`openrouter_usage={"include": True}`) and `SerniaOpenRouterModel`
  stamps it. `test_openrouter_route_carries_usage_accounting` guards that
  wiring, so an OpenRouter model added without usage accounting fails here.

Known gap: the per-token-type breakdown (`utils/llm_cost_breakdown.py`) still
needs genai-prices to split a total across input/cache/output buckets, so
OpenRouter runs contribute to total cost but not to the by-bucket panel. If
genai-prices later adds the OpenRouter routes, that resolves itself and the
OpenRouter branch below can collapse into the genai-prices one.

No API keys needed — genai-prices ships an offline price snapshot.
"""

import pytest


def _model_strings() -> set[str]:
    """Every pydantic-ai model string the Sernia agent can run."""
    from api.src.sernia_ai.config import MAIN_AGENT_MODEL, SUB_AGENT_MODEL
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    return {m.model_string for m in AVAILABLE_MODELS} | {MAIN_AGENT_MODEL, SUB_AGENT_MODEL}


def _direct_model_ids() -> list[str]:
    """Bare model ids for models called directly on their provider's API.

    Strips pydantic-ai's ``provider:`` prefix (e.g. ``anthropic:claude-sonnet-4-6``
    -> ``claude-sonnet-4-6``) to the id genai-prices / ``gen_ai.request.model`` use.
    OpenRouter routes are excluded — they're covered by the usage-accounting test.
    """
    return sorted(
        s.split(":", 1)[1] if ":" in s else s
        for s in _model_strings()
        if not s.startswith("openrouter:")
    )


def _openrouter_model_strings() -> list[str]:
    return sorted(s for s in _model_strings() if s.startswith("openrouter:"))


@pytest.mark.parametrize("model_id", _direct_model_ids())
def test_direct_model_is_priced_by_genai_prices(model_id: str):
    from api.src.utils.llm_cost_breakdown import compute_cost_breakdown

    breakdown = compute_cost_breakdown(model_id, input_tokens=1000, output_tokens=100)
    assert breakdown is not None, (
        f"{model_id!r} is not priced by the installed genai-prices. "
        "Bump `genai-prices` (and add a hardcoded tier in "
        "logfire/dashboards/llm-cost/queries/cost_by_token_type.sql) as part of "
        "introducing this model — otherwise operation.cost is never stamped and "
        "the model vanishes from the LLM-cost dashboard."
    )


def test_at_least_one_openrouter_route_is_configured():
    """Sanity check so the test below can't silently pass on an empty list."""
    assert _openrouter_model_strings(), "expected the main agent to run on OpenRouter"


@pytest.mark.parametrize("model_string", _openrouter_model_strings())
def test_openrouter_route_carries_usage_accounting(model_string: str):
    """OpenRouter routes must ask OpenRouter for the cost, since genai-prices can't.

    Without ``usage.include`` the response carries no ``cost``,
    ``SerniaOpenRouterModel`` has nothing to stamp, and the run drops off the
    LLM-cost dashboard.
    """
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS, build_run_kwargs

    key = next((m.key for m in AVAILABLE_MODELS if m.model_string == model_string), None)
    settings = build_run_kwargs(key)["model_settings"]
    assert settings.get("openrouter_usage") == {"include": True}, (
        f"{model_string!r} runs without OpenRouter usage accounting, so no cost is "
        "reported back and operation.cost is never stamped."
    )


@pytest.mark.parametrize("model_string", _openrouter_model_strings())
def test_openrouter_route_still_unknown_to_genai_prices(model_string: str):
    """Tripwire: if genai-prices learns these routes, drop the custom stamping.

    This asserts the *reason* SerniaOpenRouterModel stamps cost by hand. When it
    starts failing, genai-prices has added the route — at which point
    pydantic-ai prices the response itself, per-bucket breakdown starts working
    again, and the manual stamp should be removed rather than double-counted.
    """
    from api.src.utils.llm_cost_breakdown import compute_cost_breakdown

    model_id = model_string.split(":", 1)[1]
    assert compute_cost_breakdown(model_id, input_tokens=1000, output_tokens=100) is None, (
        f"genai-prices now prices {model_id!r}. Remove the manual cost stamping in "
        "sernia_ai/model_config.SerniaOpenRouterModel and let pydantic-ai set "
        "operation.cost, or the two will fight over the attribute."
    )
