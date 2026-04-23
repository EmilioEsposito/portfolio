"""Smoke tests for sernia_ai.model_config — keeps the runtime model picker honest."""
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


def test_available_models_cover_all_keys():
    from api.src.sernia_ai.model_config import AVAILABLE_MODELS

    keys = {m.key for m in AVAILABLE_MODELS}
    assert keys == {"gpt-5.4", "sonnet-4-6", "opus-4-7"}
    providers = {m.provider for m in AVAILABLE_MODELS}
    assert providers == {"openai", "anthropic"}
    # Opus carries a cost note so the UI can warn users.
    opus = next(m for m in AVAILABLE_MODELS if m.key == "opus-4-7")
    assert opus.cost_note and "Sonnet" in opus.cost_note
