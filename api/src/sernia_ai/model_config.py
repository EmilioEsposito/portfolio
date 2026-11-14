"""
Runtime model selection for the Sernia AI agent.

The main agent model is user-switchable via the ``model_config`` row in
``app_settings``. Call sites resolve the active model with
``resolve_active_run_kwargs()`` and spread the result into ``agent.run(...)``
/ ``VercelAIAdapter.dispatch_request(...)`` / ``resume_with_approvals(...)``.

All production models, including Claude and summarizers, use OpenRouter and
PORTFOLIO_OPENROUTER_API_KEY. The model picker retains the same stable keys.
Native web fetch is optional and is dropped by the OpenRouter adapter; web
search uses OpenRouter's web search tool, limited to the allowed domains.
Reasoning is configured with OpenRouter's reasoning effort; Sonnet's
unsupported xhigh maps to high.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import logfire
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from sqlalchemy import select

from api.src.utils.openrouter_models import (
    ThinkingEffort,
    build_openrouter_settings,
    get_thinking_effort,
    resolve_model,
)

ModelKey = Literal["gpt-6-luna", "gpt-5.6-luna", "sonnet-4-6", "opus-4-7"]

DEFAULT_MODEL_KEY: ModelKey = "gpt-6-luna"


@dataclass(frozen=True)
class ModelChoice:
    key: ModelKey
    label: str
    # Gateway the model is reached through, not the lab that trained it —
    # GPT-6 Luna is served by OpenAI but billed and routed via OpenRouter.
    # `build_run_kwargs` branches on this to pick the settings class.
    provider: Literal["openrouter"]
    model_string: str  # e.g. "openrouter:openai/gpt-6-luna"
    cost_note: str | None = None


AVAILABLE_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        key="gpt-6-luna",
        label="GPT-6 Luna",
        provider="openrouter",
        model_string="openrouter:openai/gpt-6-luna",
    ),
    ModelChoice(
        key="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        provider="openrouter",
        model_string="openrouter:openai/gpt-5.6-luna",
    ),
    ModelChoice(
        key="sonnet-4-6",
        label="Claude Sonnet 4.6",
        provider="openrouter",
        model_string="openrouter:anthropic/claude-sonnet-4.6",
    ),
    ModelChoice(
        key="opus-4-7",
        label="Claude Opus 4.7",
        provider="openrouter",
        model_string="openrouter:anthropic/claude-opus-4.7",
        cost_note="~5x Sonnet pricing — use sparingly.",
    ),
)

_BY_KEY: dict[str, ModelChoice] = {m.key: m for m in AVAILABLE_MODELS}


def get_model_choice(key: str | None) -> ModelChoice:
    """Resolve a model key (or None) to a ModelChoice, falling back to default."""
    return _BY_KEY.get(key or "", _BY_KEY[DEFAULT_MODEL_KEY])


def build_run_kwargs(key: str | None, effort: str | None = None) -> dict:
    """Resolve a model and OpenRouter reasoning settings for each run.

    GPT keeps its existing upstream pin. Claude uses its own available
    endpoints, with no OpenAI-only pin. All models retain the search allowlist.
    """
    choice = get_model_choice(key)
    resolved_effort = get_thinking_effort(effort)

    if choice.model_string.startswith("openrouter:anthropic/"):
        # Sonnet does not support xhigh; Opus supports the complete ladder.
        claude_effort = (
            "high" if choice.key == "sonnet-4-6" and resolved_effort == "xhigh" else resolved_effort
        )
        return {
            "model": resolve_model(choice.model_string),
            "model_settings": OpenRouterModelSettings(
                openrouter_reasoning={"effort": claude_effort, "enabled": True},
                openrouter_usage={"include": True},
                openrouter_cache_instructions=True,
                openrouter_cache_tool_definitions=True,
                openrouter_cache_messages=True,
            ),
        }

    return {
        "model": resolve_model(choice.model_string),
        "model_settings": build_openrouter_settings(resolved_effort),
    }


async def _read_model_config_row() -> dict:
    """Read the raw ``model_config`` JSONB row, returning ``{}`` on error/miss."""
    from api.src.database.app_settings import AppSetting
    from api.src.database.database import AsyncSessionFactory

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
