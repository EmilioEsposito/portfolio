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
* **OpenRouter routes** (`openrouter:...`) carry the exact billed amount:
  we ask OpenRouter for it (`openrouter_usage={"include": True}`) and
  `SerniaOpenRouterModel` stamps it. Since genai-prices 0.1.5 pydantic-ai can
  also price these routes itself and writes that list-price estimate to
  `operation.cost` after the model returns, so `BilledCostSpanProcessor`
  restores the billed amount when the span ends. The billed amount is better
  data: it reflects the endpoint actually routed to and includes web-plugin
  charges. `test_openrouter_route_carries_usage_accounting` guards the request
  side and `test_billed_cost_wins_on_the_exported_span` the span side.

Known gap: the per-token-type breakdown (`utils/llm_cost_breakdown.py`) looks
models up without a provider, so OpenRouter routes get no per-bucket attrs and
the v1 SQL panel remains their only by-bucket source.

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


# --- Billed cost wins over pydantic-ai's list-price estimate -----------------

_BILLED_COST = 0.0123  # deliberately unlike any list-price estimate
_PROMPT_TOKENS = 1_000
_COMPLETION_TOKENS = 100


def _usage(byok: bool) -> dict:
    """OpenRouter's usage block. BYOK (Sernia's real OpenAI route) reports a $0
    OpenRouter fee and puts the spend in `cost_details.upstream_inference_cost`."""
    usage = {
        "prompt_tokens": _PROMPT_TOKENS,
        "completion_tokens": _COMPLETION_TOKENS,
        "total_tokens": _PROMPT_TOKENS + _COMPLETION_TOKENS,
    }
    if byok:
        return usage | {
            "cost": 0,
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": _BILLED_COST},
        }
    return usage | {"cost": _BILLED_COST}


def _openrouter_completion_body(byok: bool) -> dict:
    return {
        "id": "gen-test",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-6-luna",
        "provider": "OpenAI",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "native_finish_reason": "stop",
                "message": {"role": "assistant", "content": "OK"},
            }
        ],
        "usage": _usage(byok),
    }


def _openrouter_stream_body(byok: bool) -> bytes:
    import json

    base = {"id": "gen-test", "object": "chat.completion.chunk", "created": 1}
    base |= {"model": "openai/gpt-6-luna", "provider": "OpenAI"}
    first = base | {
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}}],
    }
    last = base | {
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop", "native_finish_reason": "stop"}
        ],
        "usage": _usage(byok),
    }
    events = [f"data: {json.dumps(chunk)}\n\n" for chunk in (first, last)]
    return ("".join(events) + "data: [DONE]\n\n").encode()


def _mock_openrouter_model(stream: bool, byok: bool):
    import httpx
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    from api.src.sernia_ai.model_config import SerniaOpenRouterModel

    def handler(request: httpx.Request) -> httpx.Response:
        if stream:
            return httpx.Response(
                200,
                content=_openrouter_stream_body(byok),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=_openrouter_completion_body(byok))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="test-key", http_client=client)
    return SerniaOpenRouterModel("openai/gpt-6-luna", provider=provider)


def _list_price_estimate() -> float:
    """What pydantic-ai would put on operation.cost for the mocked usage."""
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    response = ModelResponse(
        parts=[TextPart("OK")],
        model_name="openai/gpt-6-luna",
        provider_name="openrouter",
        provider_url="https://openrouter.ai/api/v1",
        usage=RequestUsage(input_tokens=_PROMPT_TOKENS, output_tokens=_COMPLETION_TOKENS),
    )
    return float(response.cost().total_price)


def test_pydantic_ai_prices_the_openrouter_route_itself():
    """The reason BilledCostSpanProcessor exists: pydantic-ai's own estimate.

    If genai-prices ever stops pricing the route, the processor becomes a
    harmless no-op override (the stamp then survives on its own).
    """
    assert _list_price_estimate() != pytest.approx(_BILLED_COST)


@pytest.mark.asyncio
@pytest.mark.parametrize("byok", [False, True], ids=["openrouter-billed", "byok"])
@pytest.mark.parametrize("stream", [False, True], ids=["request", "stream"])
async def test_billed_cost_wins_on_the_exported_span(stream: bool, byok: bool):
    """End to end: instrumented run -> exported LLM span carries the billed cost."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import Instrumentation
    from pydantic_ai.models.instrumented import InstrumentationSettings

    from api.src.sernia_ai.model_config import build_openrouter_settings
    from api.src.utils.llm_cost_breakdown import BilledCostSpanProcessor

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    # Exporter first, as logfire's are in prod: the override must still reach it.
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer_provider.add_span_processor(BilledCostSpanProcessor())

    agent = Agent(
        _mock_openrouter_model(stream, byok),
        model_settings=build_openrouter_settings("low"),
        capabilities=[Instrumentation(InstrumentationSettings(tracer_provider=tracer_provider))],
    )
    if stream:
        async with agent.run_stream("hi") as result:
            assert await result.get_output() == "OK"
    else:
        assert (await agent.run("hi")).output == "OK"

    llm_spans = [
        s for s in exporter.get_finished_spans() if (s.attributes or {}).get("gen_ai.request.model")
    ]
    assert len(llm_spans) == 1, [s.name for s in exporter.get_finished_spans()]
    attrs = llm_spans[0].attributes
    assert attrs["operation.billed_cost"] == pytest.approx(_BILLED_COST)
    assert attrs["operation.cost"] == pytest.approx(_BILLED_COST), (
        f"operation.cost is {attrs['operation.cost']} but OpenRouter billed {_BILLED_COST}; "
        f"pydantic-ai's list estimate is {_list_price_estimate()}. "
        "BilledCostSpanProcessor did not restore the billed amount."
    )
