"""Smoke tests for sernia_ai.model_config — keeps the runtime model picker honest."""
import pytest
from pydantic_ai import WebFetchTool


def test_build_run_kwargs_openai_has_no_webfetch():
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("gpt-5.4")
    assert kw["model"] == "openai-responses:gpt-5.4"
    assert kw["builtin_tools"] == []
    # OpenAI Responses settings include the reasoning + cache retention knobs.
    assert kw["model_settings"].get("openai_prompt_cache_retention") == "24h"


def test_build_run_kwargs_anthropic_models_get_webfetch():
    from api.src.sernia_ai.model_config import build_run_kwargs

    for key, expected in (
        ("sonnet-4-6", "anthropic:claude-sonnet-4-6"),
        ("opus-4-7", "anthropic:claude-opus-4-7"),
    ):
        kw = build_run_kwargs(key)
        assert kw["model"] == expected, f"{key}: wrong model string {kw['model']!r}"
        assert any(isinstance(t, WebFetchTool) for t in kw["builtin_tools"]), (
            f"{key}: expected a WebFetchTool in builtin_tools"
        )
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


def test_anthropic_models_get_adaptive_thinking_with_default_effort():
    """Sonnet 4.6 / Opus 4.7 enable adaptive thinking + medium effort by default."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    for key in ("sonnet-4-6", "opus-4-7"):
        kw = build_run_kwargs(key)
        settings = kw["model_settings"]
        assert settings.get("anthropic_thinking") == {"type": "adaptive"}, key
        assert settings.get("anthropic_effort") == "medium", key


def test_openai_uses_reasoning_effort_default_medium():
    """GPT-5.4 maps the effort knob to openai_reasoning_effort, defaulting to medium."""
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("gpt-5.4")
    settings = kw["model_settings"]
    assert settings.get("openai_reasoning_effort") == "medium"


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_explicit_effort_threads_through_for_both_providers(effort: str):
    from api.src.sernia_ai.model_config import build_run_kwargs

    openai = build_run_kwargs("gpt-5.4", effort)
    assert openai["model_settings"].get("openai_reasoning_effort") == effort

    anthropic = build_run_kwargs("sonnet-4-6", effort)
    assert anthropic["model_settings"].get("anthropic_effort") == effort
    assert anthropic["model_settings"].get("anthropic_thinking") == {"type": "adaptive"}


def test_unknown_effort_falls_back_to_medium():
    from api.src.sernia_ai.model_config import build_run_kwargs

    kw = build_run_kwargs("sonnet-4-6", "ultra")
    assert kw["model_settings"].get("anthropic_effort") == "medium"

    kw_oai = build_run_kwargs("gpt-5.4", None)
    assert kw_oai["model_settings"].get("openai_reasoning_effort") == "medium"


def test_available_models_cover_all_keys():
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    keys = {m.key for m in AVAILABLE_MODELS}
    assert keys == {"gpt-5.4", "sonnet-4-6", "opus-4-7"}
    providers = {m.provider for m in AVAILABLE_MODELS}
    assert providers == {"openai", "anthropic"}
    # Opus carries a cost note so the UI can warn users.
    opus = next(m for m in AVAILABLE_MODELS if m.key == "opus-4-7")
    assert opus.cost_note and "Sonnet" in opus.cost_note
