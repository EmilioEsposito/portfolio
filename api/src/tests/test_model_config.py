"""Smoke tests for sernia_ai.model_config — keeps the runtime model picker honest.

Effort tiers are provider-routed (see ``model_config.build_run_kwargs``):
- OpenAI (GPT-5.6 Luna): the effort is passed through as the native
  ``openai_reasoning_effort`` knob, so the full ladder — including ``max``,
  GPT-5.6's top tier above ``xhigh`` — works regardless of the pinned
  pydantic-ai's unified ``thinking`` map (which only knows up to xhigh).
- Anthropic (Sonnet 4.6 / Opus 4.7): the effort feeds the unified ``thinking``
  setting (mapped to adaptive thinking). ``max`` is OpenAI-only, so it clamps
  to ``xhigh`` there.
"""

import pytest


def test_build_run_kwargs_openai_shape():
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("gpt-5.6-luna")
    assert kw["model"] == "openai-responses:gpt-5.6-luna"
    # No per-run native tools anymore — web search/fetch live on the agent
    # as provider-adaptive capabilities.
    assert "builtin_tools" not in kw
    settings = kw["model_settings"]
    # OpenAI Responses settings include the cache retention knob.
    assert settings.get("openai_prompt_cache_retention") == "24h"
    # Effort routes through the native reasoning-effort knob, not unified thinking.
    assert settings.get("openai_reasoning_effort") == "medium"
    assert "thinking" not in settings


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
    assert build_run_kwargs(None)["model"] == "openai-responses:gpt-5.6-luna"
    assert build_run_kwargs("nonsense")["model"] == "openai-responses:gpt-5.6-luna"


def test_default_thinking_effort_is_medium():
    from api.src.sernia_ai.model_config import DEFAULT_THINKING_EFFORT

    assert DEFAULT_THINKING_EFFORT == "medium"


def test_effort_defaults_to_medium_for_both_providers():
    from api.src.sernia_ai.model_config import build_run_kwargs

    openai_settings = build_run_kwargs("gpt-5.6-luna")["model_settings"]
    assert openai_settings.get("openai_reasoning_effort") == "medium"
    anthropic_settings = build_run_kwargs("sonnet-4-6")["model_settings"]
    assert anthropic_settings.get("thinking") == "medium"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_openai_effort_uses_reasoning_effort_knob(effort: str):
    """All tiers — including ``max`` (GPT-5.6's top tier, newer than the pinned
    pydantic-ai's unified ``thinking`` map) — pass through the native knob."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    settings = build_run_kwargs("gpt-5.6-luna", effort)["model_settings"]
    assert settings.get("openai_reasoning_effort") == effort
    assert "thinking" not in settings


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_anthropic_effort_uses_unified_thinking(effort: str):
    from api.src.sernia_ai.model_config import build_run_kwargs

    assert build_run_kwargs("sonnet-4-6", effort)["model_settings"].get("thinking") == effort


def test_max_effort_is_native_on_openai_and_clamps_on_anthropic():
    """``max`` is OpenAI-only; on Claude models it clamps to xhigh."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    openai_settings = build_run_kwargs("gpt-5.6-luna", "max")["model_settings"]
    assert openai_settings.get("openai_reasoning_effort") == "max"
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
    openai_settings = build_run_kwargs("gpt-5.6-luna", None)["model_settings"]
    assert openai_settings.get("openai_reasoning_effort") == "medium"


def test_available_models_cover_all_keys():
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    keys = {m.key for m in AVAILABLE_MODELS}
    assert keys == {"gpt-5.6-luna", "sonnet-4-6", "opus-4-7"}
    providers = {m.provider for m in AVAILABLE_MODELS}
    assert providers == {"openai", "anthropic"}
    # Opus carries a cost note so the UI can warn users.
    opus = next(m for m in AVAILABLE_MODELS if m.key == "opus-4-7")
    assert opus.cost_note and "Sonnet" in opus.cost_note
