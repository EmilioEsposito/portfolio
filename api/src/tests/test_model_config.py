"""Smoke tests for sernia_ai.model_config — keeps the runtime model picker honest.

Since the pydantic-ai 1.106 upgrade, web search/fetch are provider-adaptive
capabilities on the agent itself (see test_sernia_agent_wiring.py), and
thinking depth uses the unified ``thinking`` model setting instead of the
provider-specific ``anthropic_thinking``/``openai_reasoning_effort`` knobs.
"""
import pytest


def test_build_run_kwargs_openai_shape():
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("gpt-5.4")
    assert kw["model"] == "openai-responses:gpt-5.4"
    # No per-run native tools anymore — web search/fetch live on the agent
    # as provider-adaptive capabilities.
    assert "builtin_tools" not in kw
    # OpenAI Responses settings include the cache retention knob.
    assert kw["model_settings"].get("openai_prompt_cache_retention") == "24h"


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
    from api.src.sernia_ai.model_config import build_run_kwargs, DEFAULT_MODEL_KEY

    assert DEFAULT_MODEL_KEY == "gpt-5.4"
    assert build_run_kwargs(None)["model"] == "openai-responses:gpt-5.4"
    assert build_run_kwargs("nonsense")["model"] == "openai-responses:gpt-5.4"


def test_default_thinking_effort_is_medium():
    from api.src.sernia_ai.model_config import DEFAULT_THINKING_EFFORT

    assert DEFAULT_THINKING_EFFORT == "medium"


@pytest.mark.parametrize("key", ["gpt-5.4", "sonnet-4-6", "opus-4-7"])
def test_unified_thinking_defaults_to_medium(key: str):
    """All models get the unified `thinking` setting (pydantic-ai maps it to
    adaptive thinking + effort on Anthropic, reasoning_effort on OpenAI)."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs(key)
    assert kw["model_settings"].get("thinking") == "medium", key


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_explicit_effort_threads_through_for_both_providers(effort: str):
    from api.src.sernia_ai.model_config import build_run_kwargs

    assert build_run_kwargs("gpt-5.4", effort)["model_settings"].get("thinking") == effort
    assert build_run_kwargs("sonnet-4-6", effort)["model_settings"].get("thinking") == effort


def test_unified_thinking_maps_to_adaptive_on_anthropic():
    """Guard the provider translation we rely on: with no explicit
    `anthropic_thinking`, pydantic-ai turns unified thinking into adaptive
    thinking + effort on models that support it (Sonnet 4.6 / Opus 4.7)."""
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("sonnet-4-6", "high")
    model = AnthropicModel(
        "claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-key")
    )
    params = ModelRequestParameters(thinking=kw["model_settings"].get("thinking"))
    translated = model._translate_thinking(kw["model_settings"], params)  # noqa: SLF001
    assert translated == {"type": "adaptive"}


def test_unknown_effort_falls_back_to_medium():
    from api.src.sernia_ai.model_config import build_run_kwargs

    assert build_run_kwargs("sonnet-4-6", "ultra")["model_settings"].get("thinking") == "medium"
    assert build_run_kwargs("gpt-5.4", None)["model_settings"].get("thinking") == "medium"


def test_available_models_cover_all_keys():
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    keys = {m.key for m in AVAILABLE_MODELS}
    assert keys == {"gpt-5.4", "sonnet-4-6", "opus-4-7"}
    providers = {m.provider for m in AVAILABLE_MODELS}
    assert providers == {"openai", "anthropic"}
    # Opus carries a cost note so the UI can warn users.
    opus = next(m for m in AVAILABLE_MODELS if m.key == "opus-4-7")
    assert opus.cost_note and "Sonnet" in opus.cost_note
