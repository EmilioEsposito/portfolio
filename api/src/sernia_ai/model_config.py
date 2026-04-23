"""
Runtime model selection for the Sernia AI agent.

The main agent model is user-switchable via the ``model_config`` row in
``app_settings``. Call sites resolve the active model with
``resolve_active_run_kwargs()`` and spread the result into ``agent.run(...)``
/ ``VercelAIAdapter.dispatch_request(...)`` / ``resume_with_approvals(...)``.

Why per-run and not per-agent: builtin tools like ``WebFetchTool`` only work
on Anthropic/Google, and model settings classes (``AnthropicModelSettings``
vs ``OpenAIResponsesModelSettings``) are not cross-compatible. PydanticAI
exposes ``model`` / ``model_settings`` / ``builtin_tools`` on every run
entrypoint, so one Agent instance with per-run overrides is simpler than
maintaining one Agent per provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import logfire
from pydantic_ai import WebFetchTool
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select

from api.src.sernia_ai.config import WEB_SEARCH_ALLOWED_DOMAINS

ModelKey = Literal["gpt-5.4", "sonnet-4-6", "opus-4-7"]

DEFAULT_MODEL_KEY: ModelKey = "gpt-5.4"


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


def build_run_kwargs(key: str | None) -> dict:
    """Return kwargs to spread into agent.run() / VercelAIAdapter.dispatch_request().

    Produces ``model``, ``model_settings``, and ``builtin_tools`` suited to the
    selected provider. ``WebFetchTool`` is added only for Anthropic (OpenAI
    Responses does not support it and would raise ``UserError``).
    """
    choice = get_model_choice(key)
    settings: ModelSettings
    extra_builtins: list[AbstractBuiltinTool] = []

    if choice.provider == "anthropic":
        settings = AnthropicModelSettings(
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            anthropic_cache_messages=True,
        )
        extra_builtins.append(WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
    else:  # openai
        # `thinking="high"` → openai_reasoning_effort='high' on Responses API.
        # `openai_prompt_cache_retention="24h"` extends the default ~5–10 min
        # in-memory cache to 24h so infrequent scheduled runs still hit cache.
        settings = OpenAIResponsesModelSettings(
            thinking="high",
            openai_prompt_cache_retention="24h",
        )

    return {
        "model": choice.model_string,
        "model_settings": settings,
        "builtin_tools": extra_builtins,
    }


async def get_active_model_key() -> ModelKey:
    """Read model_config from the DB, falling back to DEFAULT_MODEL_KEY.

    Stored shape: ``{"model_key": "<key>"}``.
    """
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.models import AppSetting

    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "model_config")
            )
            row = result.scalar_one_or_none()
            if isinstance(row, dict):
                candidate = row.get("model_key")
                if candidate in _BY_KEY:
                    return candidate  # type: ignore[return-value]
    except Exception:
        logfire.warn("Failed to read model_config from DB, using default", default=DEFAULT_MODEL_KEY)
    return DEFAULT_MODEL_KEY


async def resolve_active_run_kwargs() -> dict:
    """Convenience: read active key from DB + build run kwargs in one call."""
    return build_run_kwargs(await get_active_model_key())
