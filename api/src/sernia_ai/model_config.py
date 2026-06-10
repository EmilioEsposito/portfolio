"""
Runtime model selection for the Sernia AI agent.

The main agent model is user-switchable via the ``model_config`` row in
``app_settings``. Call sites resolve the active model with
``resolve_active_run_kwargs()`` and spread the result into ``agent.run(...)``
/ ``VercelAIAdapter.dispatch_request(...)`` / ``resume_with_approvals(...)``.

Why per-run and not per-agent: model settings classes
(``AnthropicModelSettings`` vs ``OpenAIResponsesModelSettings``) are not
cross-compatible — prompt-cache knobs are provider-specific. PydanticAI
exposes ``model`` / ``model_settings`` on every run entrypoint, so one Agent
instance with per-run overrides is simpler than maintaining one Agent per
provider.

Web search/fetch are no longer attached here: the agent's ``WebSearch`` /
``WebFetch`` capabilities (see ``agent.py``) adapt to the active provider
automatically (native web fetch is Anthropic-only and is dropped on OpenAI
runs). Thinking depth uses the unified ``thinking`` model setting, which
pydantic-ai maps to adaptive thinking + effort on Anthropic and
``reasoning_effort`` on OpenAI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast, get_args

import logfire
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select

ModelKey = Literal["gpt-5.4", "sonnet-4-6", "opus-4-7"]
ThinkingEffort = Literal["low", "medium", "high"]

DEFAULT_MODEL_KEY: ModelKey = "gpt-5.4"
DEFAULT_THINKING_EFFORT: ThinkingEffort = "medium"
_VALID_EFFORTS: frozenset[str] = frozenset(get_args(ThinkingEffort))


@dataclass(frozen=True)
class ModelChoice:
    key: ModelKey
    label: str
    provider: Literal["openai", "anthropic"]
    model_string: str  # e.g. "openai-responses:gpt-5.4"
    cost_note: str | None = None


AVAILABLE_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        key="gpt-5.4",
        label="GPT-5.4",
        provider="openai",
        model_string="openai-responses:gpt-5.4",
    ),
    ModelChoice(
        key="sonnet-4-6",
        label="Claude Sonnet 4.6",
        provider="anthropic",
        model_string="anthropic:claude-sonnet-4-6",
    ),
    ModelChoice(
        key="opus-4-7",
        label="Claude Opus 4.7",
        provider="anthropic",
        model_string="anthropic:claude-opus-4-7",
        cost_note="~5x Sonnet pricing — use sparingly.",
    ),
)

_BY_KEY: dict[str, ModelChoice] = {m.key: m for m in AVAILABLE_MODELS}


def get_model_choice(key: str | None) -> ModelChoice:
    """Resolve a model key (or None) to a ModelChoice, falling back to default."""
    return _BY_KEY.get(key or "", _BY_KEY[DEFAULT_MODEL_KEY])


def get_thinking_effort(value: str | None) -> ThinkingEffort:
    """Coerce a stored/user-supplied effort to a valid ThinkingEffort, defaulting to medium."""
    if value in _VALID_EFFORTS:
        return cast(ThinkingEffort, value)
    return DEFAULT_THINKING_EFFORT


def build_run_kwargs(key: str | None, effort: str | None = None) -> dict:
    """Return kwargs to spread into agent.run() / VercelAIAdapter.dispatch_request().

    Produces ``model`` and ``model_settings`` suited to the selected provider.
    The only provider-specific parts left are the prompt-cache knobs; web
    search/fetch live on the agent as provider-adaptive capabilities.

    ``effort`` controls reasoning depth — low/medium/high — via the unified
    ``thinking`` model setting. On Sonnet 4.6 and Opus 4.7 pydantic-ai maps it
    to adaptive thinking + effort
    (https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking),
    where Claude decides per-request whether and how much to think. On GPT-5.4
    it maps to ``reasoning_effort``. Defaults to medium.
    """
    choice = get_model_choice(key)
    resolved_effort = get_thinking_effort(effort)
    settings: ModelSettings

    if choice.provider == "anthropic":
        settings = AnthropicModelSettings(
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            anthropic_cache_messages=True,
            thinking=resolved_effort,
        )
    else:  # openai
        # `openai_prompt_cache_retention="24h"` extends the default ~5–10 min
        # in-memory cache to 24h so infrequent scheduled runs still hit cache.
        settings = OpenAIResponsesModelSettings(
            thinking=resolved_effort,
            openai_prompt_cache_retention="24h",
        )

    return {
        "model": choice.model_string,
        "model_settings": settings,
    }


async def _read_model_config_row() -> dict:
    """Read the raw ``model_config`` JSONB row, returning ``{}`` on error/miss."""
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.models import AppSetting

    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "model_config")
            )
            row = result.scalar_one_or_none()
            if isinstance(row, dict):
                return row
    except Exception:
        logfire.warn("Failed to read model_config from DB, using defaults")
    return {}


async def get_active_model_key() -> ModelKey:
    """Read the active model key from the DB, falling back to DEFAULT_MODEL_KEY.

    Stored shape: ``{"model_key": "<key>", "thinking_effort": "<effort>"}``.
    """
    row = await _read_model_config_row()
    candidate = row.get("model_key")
    if candidate in _BY_KEY:
        return candidate  # type: ignore[return-value]
    return DEFAULT_MODEL_KEY


async def get_active_thinking_effort() -> ThinkingEffort:
    """Read the active thinking effort from the DB, falling back to medium."""
    row = await _read_model_config_row()
    return get_thinking_effort(row.get("thinking_effort"))


async def resolve_active_run_kwargs() -> dict:
    """Convenience: read active key + effort from DB + build run kwargs in one call."""
    row = await _read_model_config_row()
    return build_run_kwargs(row.get("model_key"), row.get("thinking_effort"))
