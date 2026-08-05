"""Smoke tests for sernia_ai.model_config — keeps the runtime model picker honest.

GPT models are reached through OpenRouter, Claude models directly through
Anthropic. Effort tiers are gateway-routed (see ``model_config.build_run_kwargs``):
- OpenRouter (GPT-5.6 Luna): the effort is passed through as
  ``openrouter_reasoning={"effort": ...}``. OpenRouter validates that enum
  server-side (``max|xhigh|high|medium|low|minimal|none``), so the full ladder
  — including ``max``, GPT-5.6's top tier above ``xhigh`` — works. Routing it
  through the unified ``thinking`` setting instead would silently collapse both
  xhigh and max to ``high`` via pydantic-ai's ``_OPENROUTER_EFFORT_MAP``.
- Anthropic (Sonnet 4.6 / Opus 4.7): the effort feeds the unified ``thinking``
  setting (mapped to adaptive thinking). ``max`` is an OpenAI tier, so it
  clamps to ``xhigh`` there.
"""

import pytest


def test_build_run_kwargs_openrouter_shape():
    from api.src.sernia_ai.model_config import SerniaOpenRouterModel, build_run_kwargs

    kw = build_run_kwargs("gpt-5.6-luna")
    # OpenRouter runs get a model *instance* (the subclass carrying the
    # web-search domain allowlist), not a bare model string.
    model = kw["model"]
    assert isinstance(model, SerniaOpenRouterModel)
    assert model.model_name == "openai/gpt-5.6-luna"
    # No per-run native tools anymore — web search/fetch live on the agent
    # as provider-adaptive capabilities.
    assert "builtin_tools" not in kw
    settings = kw["model_settings"]
    # Effort routes through OpenRouter's own reasoning knob, not unified thinking.
    assert settings.get("openrouter_reasoning") == {"effort": "medium", "enabled": True}
    assert "thinking" not in settings
    # Usage accounting is what puts OpenRouter's real per-request cost in
    # `provider_details`, which SerniaOpenRouterModel stamps as operation.cost.
    assert settings.get("openrouter_usage") == {"include": True}


def test_build_run_kwargs_pins_openrouter_upstream():
    """Provider pinning keeps the served compute identical to pre-OpenRouter.

    OpenRouter would otherwise load-balance gpt-5.6-luna across OpenAI, Azure
    and Bedrock, which differ in price by ~10x and in supported parameters.
    """
    from api.src.sernia_ai.config import OPENROUTER_ALLOWED_PROVIDERS
    from api.src.sernia_ai.model_config import build_run_kwargs

    settings = build_run_kwargs("gpt-5.6-luna")["model_settings"]
    assert settings.get("openrouter_provider") == {"only": OPENROUTER_ALLOWED_PROVIDERS}


def test_openrouter_model_is_cached():
    """Each model instance owns an HTTP client — don't rebuild one per run."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    assert build_run_kwargs("gpt-5.6-luna")["model"] is build_run_kwargs("gpt-5.6-luna")["model"]


def test_resolve_model_passes_through_non_openrouter_strings():
    from api.src.sernia_ai.model_config import resolve_model

    assert resolve_model("anthropic:claude-sonnet-4-6") == "anthropic:claude-sonnet-4-6"


def test_build_run_kwargs_anthropic_shape():
    from api.src.sernia_ai.model_config import build_run_kwargs

    for key, expected in (
        ("sonnet-4-6", "anthropic:claude-sonnet-4-6"),
        ("opus-4-7", "anthropic:claude-opus-4-7"),
    ):
        kw = build_run_kwargs(key)
        assert kw["model"] == expected, f"{key}: wrong model string {kw['model']!r}"
        assert "builtin_tools" not in kw
        # Anthropic caching is enabled on all three layers.
        settings = kw["model_settings"]
        assert settings.get("anthropic_cache_instructions") is True
        assert settings.get("anthropic_cache_tool_definitions") is True
        assert settings.get("anthropic_cache_messages") is True


def test_build_run_kwargs_unknown_key_falls_back_to_default():
    from api.src.sernia_ai.model_config import DEFAULT_MODEL_KEY, build_run_kwargs

    assert DEFAULT_MODEL_KEY == "gpt-5.6-luna"
    assert build_run_kwargs(None)["model"].model_name == "openai/gpt-5.6-luna"
    assert build_run_kwargs("nonsense")["model"].model_name == "openai/gpt-5.6-luna"


def test_default_thinking_effort_is_medium():
    from api.src.sernia_ai.model_config import DEFAULT_THINKING_EFFORT

    assert DEFAULT_THINKING_EFFORT == "medium"


def test_effort_defaults_to_medium_for_both_gateways():
    from api.src.sernia_ai.model_config import build_run_kwargs

    openrouter_settings = build_run_kwargs("gpt-5.6-luna")["model_settings"]
    assert openrouter_settings["openrouter_reasoning"]["effort"] == "medium"
    anthropic_settings = build_run_kwargs("sonnet-4-6")["model_settings"]
    assert anthropic_settings.get("thinking") == "medium"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_openrouter_effort_uses_reasoning_knob(effort: str):
    """All tiers — including ``max`` (GPT-5.6's top tier) — pass through verbatim.

    OpenRouter's ``reasoning.effort`` enum accepts the whole ladder and 400s on
    anything else, so no clamping is needed on this path.
    """
    from api.src.sernia_ai.model_config import build_run_kwargs

    settings = build_run_kwargs("gpt-5.6-luna", effort)["model_settings"]
    assert settings["openrouter_reasoning"] == {"effort": effort, "enabled": True}
    assert "thinking" not in settings


def test_openrouter_preserves_minimal_effort_for_ab_tests():
    """`minimal` must survive to OpenRouter instead of rounding up to medium.

    The A/B CLI accepts `--thinking minimal`, but it is below the settings UI's
    `ThinkingEffort` ladder. Coercing it to `medium` would run the OpenRouter
    variant deeper than its Anthropic counterpart and corrupt the comparison.
    OpenRouter's own enum accepts `minimal`, so it passes through.
    """
    from api.src.sernia_ai.model_config import build_openrouter_settings

    settings = build_openrouter_settings("minimal")
    assert settings["openrouter_reasoning"] == {"effort": "minimal", "enabled": True}


def test_ab_cli_thinking_choices_all_reach_openrouter_intact():
    """Every tier the A/B CLI exposes must survive the settings builder.

    Reads the choices off the real parser, so adding a tier to the CLI without
    teaching OpenRouter about it fails here rather than skewing an experiment.
    """
    from api.src.sernia_ai.ab_tests._cli import build_parser
    from api.src.sernia_ai.model_config import build_openrouter_settings

    parser = build_parser("test", default_experiment_prefix="test")
    action = next(a for a in parser._actions if "--thinking" in a.option_strings)  # noqa: SLF001
    choices = tuple(action.choices or ())

    assert choices, "expected --thinking to declare choices"
    for effort in choices:
        settings = build_openrouter_settings(effort)
        assert settings["openrouter_reasoning"]["effort"] == effort, (
            f"A/B CLI allows --thinking {effort!r} but the OpenRouter settings builder "
            f"silently changed it to {settings['openrouter_reasoning']['effort']!r}"
        )


def test_unknown_effort_still_falls_back_on_openrouter():
    """Widening the accepted set must not turn it into a passthrough."""
    from api.src.sernia_ai.model_config import build_openrouter_settings

    assert build_openrouter_settings("ludicrous")["openrouter_reasoning"]["effort"] == "medium"
    assert build_openrouter_settings(None)["openrouter_reasoning"]["effort"] == "medium"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_anthropic_effort_uses_unified_thinking(effort: str):
    from api.src.sernia_ai.model_config import build_run_kwargs

    assert build_run_kwargs("sonnet-4-6", effort)["model_settings"].get("thinking") == effort


def test_max_effort_is_native_on_openrouter_and_clamps_on_anthropic():
    """``max`` is an OpenAI tier; on Claude models it clamps to xhigh."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    openrouter_settings = build_run_kwargs("gpt-5.6-luna", "max")["model_settings"]
    assert openrouter_settings["openrouter_reasoning"]["effort"] == "max"
    anthropic_settings = build_run_kwargs("sonnet-4-6", "max")["model_settings"]
    assert anthropic_settings.get("thinking") == "xhigh"


def test_unified_thinking_maps_to_adaptive_on_anthropic():
    """Guard the provider translation we rely on: with no explicit
    `anthropic_thinking`, pydantic-ai turns unified thinking into adaptive
    thinking + effort on models that support it (Sonnet 4.6 / Opus 4.7)."""
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("sonnet-4-6", "high")
    model = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-key"))
    params = ModelRequestParameters(thinking=kw["model_settings"].get("thinking"))
    translated = model._translate_thinking(kw["model_settings"], params)  # noqa: SLF001
    assert translated == {"type": "adaptive"}


def test_unknown_effort_falls_back_to_medium():
    from api.src.sernia_ai.model_config import build_run_kwargs

    anthropic_settings = build_run_kwargs("sonnet-4-6", "ultra")["model_settings"]
    assert anthropic_settings.get("thinking") == "medium"
    openrouter_settings = build_run_kwargs("gpt-5.6-luna", None)["model_settings"]
    assert openrouter_settings["openrouter_reasoning"]["effort"] == "medium"


def test_available_models_cover_all_keys():
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    keys = {m.key for m in AVAILABLE_MODELS}
    assert keys == {"gpt-5.6-luna", "sonnet-4-6", "opus-4-7"}
    providers = {m.provider for m in AVAILABLE_MODELS}
    assert providers == {"openrouter", "anthropic"}
    # Opus carries a cost note so the UI can warn users.
    opus = next(m for m in AVAILABLE_MODELS if m.key == "opus-4-7")
    assert opus.cost_note and "Sonnet" in opus.cost_note


def test_no_direct_openai_models_remain():
    """The whole point of the migration: no `openai:` / `openai-responses:` route.

    Sernia AI reaches GPT through OpenRouter so billing, rate limits, and model
    availability all sit behind one gateway.
    """
    from api.src.sernia_ai.config import MAIN_AGENT_MODEL
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    strings = {m.model_string for m in AVAILABLE_MODELS} | {MAIN_AGENT_MODEL}
    offenders = [s for s in strings if s.startswith("openai:") or s.startswith("openai-responses:")]
    assert not offenders, f"direct OpenAI model strings left behind: {offenders}"


def test_web_search_domain_allowlist_reaches_openrouter_plugin():
    """OpenRouter's `web` plugin must carry WEB_SEARCH_ALLOWED_DOMAINS.

    pydantic-ai maps WebSearchTool -> `plugins: [{"id": "web"}]` but drops
    `allowed_domains`; SerniaOpenRouterModel re-attaches them as the plugin's
    `include_domains`. Without this the agent could search the whole web.
    """
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.native_tools import WebSearchTool

    from api.src.sernia_ai.config import WEB_SEARCH_ALLOWED_DOMAINS
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("gpt-5.6-luna")
    model = kw["model"]
    params = model.customize_request_parameters(
        ModelRequestParameters(
            native_tools=[WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS, optional=True)]
        )
    )
    settings, _ = model.prepare_request(kw["model_settings"], params)

    plugins = settings["extra_body"]["plugins"]
    web = [p for p in plugins if p.get("id") == "web"]
    assert len(web) == 1, f"expected exactly one web plugin, got {plugins}"
    assert web[0]["include_domains"] == WEB_SEARCH_ALLOWED_DOMAINS


def test_openrouter_cost_stamped_on_span():
    """OpenRouter runs get `operation.cost` from OpenRouter's own usage accounting.

    genai-prices has no entry for the `openai/gpt-5.6-luna` OpenRouter route, so
    pydantic-ai's own pricing raises LookupError and never sets the attribute —
    which would drop the main agent out of the LLM-cost dashboard. See
    `SerniaOpenRouterModel` and `test_model_pricing.py`.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic_ai.messages import ModelResponse, TextPart

    from api.src.sernia_ai.model_config import stamp_openrouter_cost

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("chat openai/gpt-5.6-luna"):
        stamp_openrouter_cost(
            ModelResponse(parts=[TextPart("hi")], provider_details={"cost": 0.0000041})
        )

    (span,) = exporter.get_finished_spans()
    assert span.attributes["operation.cost"] == pytest.approx(0.0000041)


def test_stamp_openrouter_cost_is_a_noop_without_cost():
    """Anthropic runs (and any response without reported cost) must not be touched."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic_ai.messages import ModelResponse, TextPart

    from api.src.sernia_ai.model_config import stamp_openrouter_cost

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("chat anthropic"):
        stamp_openrouter_cost(ModelResponse(parts=[TextPart("hi")], provider_details=None))

    (span,) = exporter.get_finished_spans()
    assert "operation.cost" not in (span.attributes or {})


# ---------------------------------------------------------------------------
# Streamed encrypted-reasoning integrity (SerniaOpenRouterStreamedResponse)
# ---------------------------------------------------------------------------
#
# Root cause of the production `invalid_encrypted_content` 400 (2026-08-05,
# trace 019fcff60d3999d40125ebed3a7929be): pydantic-ai's OpenRouter streaming
# keys reasoning_details deltas by type + position-within-chunk, so two
# encrypted reasoning items with different `rs_...` ids in one streamed
# response merge into a single ThinkingPart — first item's id, last item's
# encrypted payload. OpenAI then rejects the replay because the encrypted blob
# decrypts to a different item id than the part claims.


def _encrypted_chunk_choice(item_id: str, data: str, index: int):
    """A real _OpenRouterChunkChoice carrying one encrypted reasoning detail."""
    from pydantic_ai.models.openrouter import (
        _OpenRouterChunkChoice,  # pyright: ignore[reportPrivateUsage]
    )

    return _OpenRouterChunkChoice.model_validate(
        {
            "index": 0,
            "delta": {
                "reasoning_details": [
                    {
                        "type": "reasoning.encrypted",
                        "id": item_id,
                        "format": "openai-responses-v1",
                        "index": index,
                        "data": data,
                    }
                ]
            },
            "finish_reason": None,
        }
    )


def _drain_thinking_deltas(response_cls, choices):
    """Feed chunk choices through a streamed-response class's thinking mapper.

    Builds the instance without running the dataclass __init__ (it wants a live
    HTTP stream); _map_thinking_delta only touches _parts_manager (derived from
    model_request_parameters) and _provider_name.
    """
    from pydantic_ai.messages import ThinkingPart
    from pydantic_ai.models import ModelRequestParameters

    resp = object.__new__(response_cls)
    resp.model_request_parameters = ModelRequestParameters()
    resp._provider_name = "openrouter"
    for choice in choices:
        for _ in resp._map_thinking_delta(choice):
            pass
    return [p for p in resp._parts_manager.get_parts() if isinstance(p, ThinkingPart)]


def test_streamed_encrypted_reasoning_items_stay_distinct():
    """Two encrypted reasoning items must survive streaming as two parts.

    Each keeps its own id↔payload pairing, so the replayed reasoning_details
    are exactly what OpenAI produced and verification passes.
    """
    from api.src.sernia_ai.model_config import SerniaOpenRouterStreamedResponse

    parts = _drain_thinking_deltas(
        SerniaOpenRouterStreamedResponse,
        [
            _encrypted_chunk_choice("rs_AAA", "ENCRYPTED_BLOB_OF_AAA", index=0),
            _encrypted_chunk_choice("rs_BBB", "ENCRYPTED_BLOB_OF_BBB", index=1),
        ],
    )

    assert [(p.id, p.signature) for p in parts] == [
        ("rs_AAA", "ENCRYPTED_BLOB_OF_AAA"),
        ("rs_BBB", "ENCRYPTED_BLOB_OF_BBB"),
    ]


def test_streamed_response_class_is_wired():
    """SerniaOpenRouterModel must actually stream through the fixed class."""
    from api.src.sernia_ai.model_config import (
        SerniaOpenRouterStreamedResponse,
        resolve_model,
    )

    model = resolve_model("openrouter:openai/gpt-5.6-luna")
    assert model._streamed_response_cls is SerniaOpenRouterStreamedResponse


def test_upstream_still_merges_encrypted_reasoning_items():
    """CANARY: upstream OpenRouterStreamedResponse still has the merge bug.

    When this test FAILS, pydantic-ai has fixed the vendor-key collision
    upstream — delete SerniaOpenRouterStreamedResponse (and these tests) and
    rely on the library. Until then, this documents exactly what the subclass
    protects against: one part, first id, last payload.
    """
    from pydantic_ai.models.openrouter import OpenRouterStreamedResponse

    parts = _drain_thinking_deltas(
        OpenRouterStreamedResponse,
        [
            _encrypted_chunk_choice("rs_AAA", "ENCRYPTED_BLOB_OF_AAA", index=0),
            _encrypted_chunk_choice("rs_BBB", "ENCRYPTED_BLOB_OF_BBB", index=1),
        ],
    )

    assert [(p.id, p.signature) for p in parts] == [("rs_AAA", "ENCRYPTED_BLOB_OF_BBB")]
