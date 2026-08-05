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
