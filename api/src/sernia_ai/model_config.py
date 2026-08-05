"""
Runtime model selection for the Sernia AI agent.

The main agent model is user-switchable via the ``model_config`` row in
``app_settings``. Call sites resolve the active model with
``resolve_active_run_kwargs()`` and spread the result into ``agent.run(...)``
/ ``VercelAIAdapter.dispatch_request(...)`` / ``resume_with_approvals(...)``.

Why per-run and not per-agent: model settings classes
(``AnthropicModelSettings`` vs ``OpenRouterModelSettings``) are not
cross-compatible — prompt-cache and reasoning knobs are provider-specific.
PydanticAI exposes ``model`` / ``model_settings`` on every run entrypoint, so
one Agent instance with per-run overrides is simpler than maintaining one
Agent per provider.

GPT models are reached through **OpenRouter**, not the OpenAI API directly.
Claude models still go straight to Anthropic — Anthropic's own API exposes
richer cache-control and adaptive-thinking knobs than OpenRouter's
pass-through, and the key is already wired.

Web search/fetch are no longer attached here: the agent's ``WebSearch`` /
``WebFetch`` capabilities (see ``agent.py``) adapt to the active provider
automatically (native web fetch is Anthropic-only and is dropped on OpenRouter
runs; native web search maps to OpenRouter's ``web`` plugin). Reasoning depth
(low/medium/high/xhigh/max) is provider-routed: on OpenRouter it is passed
through as ``openrouter_reasoning={"effort": ...}``, whose enum accepts the
whole ladder including GPT-5.6's ``max`` tier; on Anthropic it feeds the
unified ``thinking`` setting, mapped to adaptive thinking + effort (``max``
clamps to ``xhigh``, being an OpenAI tier).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, cast, get_args

import logfire
from opentelemetry import trace
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select

from api.src.sernia_ai.config import OPENROUTER_ALLOWED_PROVIDERS

# pydantic-ai's provider prefix for OpenRouter model strings.
_OPENROUTER_PREFIX = "openrouter"

ModelKey = Literal["gpt-5.6-luna", "sonnet-4-6", "opus-4-7"]
# Reasoning-effort tiers, ascending. low/medium/high/xhigh come from OpenAI's
# reasoning_effort scale; "max" is GPT-5.6's top tier (added *above* xhigh — it
# gives the model even more time to explore alternatives, run checks, and
# revise). OpenRouter's `reasoning.effort` enum accepts the full ladder
# (max|xhigh|high|medium|low|minimal|none) and rejects anything else with a
# 400, so the tier is passed straight through. "max" is an OpenAI tier — on
# Anthropic models it clamps to "xhigh" (see build_run_kwargs).
ThinkingEffort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL_KEY: ModelKey = "gpt-5.6-luna"
# Safe fallback only — used when the DB lookup fails or is bypassed. The active
# effort is the DB-backed `model_config` row (seeded/migrated to "max").
DEFAULT_THINKING_EFFORT: ThinkingEffort = "medium"
_VALID_EFFORTS: frozenset[str] = frozenset(get_args(ThinkingEffort))


@dataclass(frozen=True)
class ModelChoice:
    key: ModelKey
    label: str
    # Gateway the model is reached through, not the lab that trained it —
    # GPT-5.6 Luna is served by OpenAI but billed and routed via OpenRouter.
    # `build_run_kwargs` branches on this to pick the settings class.
    provider: Literal["openrouter", "anthropic"]
    model_string: str  # e.g. "openrouter:openai/gpt-5.6-luna"
    cost_note: str | None = None


AVAILABLE_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        key="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        provider="openrouter",
        model_string="openrouter:openai/gpt-5.6-luna",
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


# OTel attribute pydantic-ai normally stamps from genai-prices. The LLM-cost
# dashboard filters on `operation.cost is not null`.
_COST_SPAN_ATTR = "operation.cost"


class SerniaOpenRouterModel(OpenRouterModel):
    """OpenRouter model that keeps two things pydantic-ai drops on this provider.

    **1. The web-search domain allowlist.** PydanticAI maps a ``WebSearchTool``
    onto OpenRouter's ``web`` plugin (``extra_body["plugins"] = [{"id": "web"}]``)
    but drops the tool's ``allowed_domains`` — OpenRouter spells that
    ``include_domains`` on the plugin config and there is no mapping upstream.
    The OpenAI Responses API *did* enforce the allowlist
    (``filters.allowed_domains``), so without this the agent's
    ``WEB_SEARCH_ALLOWED_DOMAINS`` guardrail would silently stop being enforced.
    The domains are read back off the tool instance rather than imported from
    config, so an overridden tool (e.g. in A/B tests) carries its own list.

    **2. ``operation.cost`` on the LLM span.** pydantic-ai prices each response
    with genai-prices, whose data has no entry for OpenRouter's
    ``openai/gpt-5.6-luna`` route (neither the pinned snapshot nor upstream
    main as of 2026-08). ``ModelResponse.cost()`` raises ``LookupError``, the
    attribute is never set, and the model vanishes from the LLM-cost dashboard.
    OpenRouter reports the exact billed amount per request instead — see
    ``build_openrouter_settings``'s ``usage.include`` — so we stamp that. It is
    strictly better data than a price table: it reflects the endpoint actually
    routed to and includes web-plugin charges.
    """

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        settings, params = super().prepare_request(model_settings, model_request_parameters)
        allowed = next(
            (
                tool.allowed_domains
                for tool in params.native_tools
                if isinstance(tool, WebSearchTool) and tool.allowed_domains
            ),
            None,
        )
        if not allowed or not settings:
            return settings, params

        extra_body = cast(dict[str, Any], settings.get("extra_body") or {})
        for plugin in extra_body.get("plugins", []):
            if isinstance(plugin, dict) and plugin.get("id") == "web":
                plugin.setdefault("include_domains", list(allowed))
        return settings, params

    async def request(self, *args: Any, **kwargs: Any) -> ModelResponse:
        response = await super().request(*args, **kwargs)
        stamp_openrouter_cost(response)
        return response

    @asynccontextmanager
    async def request_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamedResponse]:
        async with super().request_stream(*args, **kwargs) as stream:
            yield stream
            # Post-yield: the caller has finished consuming, so `get()` returns
            # the completed response (usage/cost arrive in the final chunk).
            # pydantic-ai's instrumentation span wraps this call and is still
            # the current span here; its own `set_attributes` on completion
            # omits `operation.cost` whenever pricing failed, so ours survives.
            stamp_openrouter_cost(stream.get())


def stamp_openrouter_cost(response: ModelResponse) -> None:
    """Copy OpenRouter's reported request cost onto the active LLM span.

    No-ops when the cost is absent (usage accounting off, or a provider that
    doesn't report it) or when the span isn't recording. Never raises — a
    telemetry gap must not fail an agent run.
    """
    try:
        cost = (response.provider_details or {}).get("cost")
        if cost is None:
            return
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute(_COST_SPAN_ATTR, float(cost))
    except Exception:  # pragma: no cover - defensive
        logfire.debug("failed to stamp OpenRouter cost on span", exc_info=True)


@cache
def _openrouter_model(model_name: str) -> SerniaOpenRouterModel:
    """Cached model instance — each one owns an HTTP client, so don't rebuild per run."""
    return SerniaOpenRouterModel(model_name)


def resolve_model(model_string: str) -> SerniaOpenRouterModel | str:
    """Turn a pydantic-ai model string into the concrete model to run with.

    ``openrouter:`` strings become a cached :class:`SerniaOpenRouterModel` (the
    subclass that carries the web-search domain allowlist); everything else is
    handed back untouched for pydantic-ai's own string inference.

    Use this anywhere a model string reaches ``Agent(...)`` or ``agent.run(...)``
    so no code path silently loses the allowlist.
    """
    if model_string.startswith(f"{_OPENROUTER_PREFIX}:"):
        return _openrouter_model(model_string.split(":", 1)[1])
    return model_string


def build_run_kwargs(key: str | None, effort: str | None = None) -> dict:
    """Return kwargs to spread into agent.run() / VercelAIAdapter.dispatch_request().

    Produces ``model`` and ``model_settings`` suited to the selected gateway.
    Web search/fetch live on the agent as provider-adaptive capabilities.

    ``effort`` controls reasoning depth — low/medium/high/xhigh/max, ascending.
    On GPT-5.6 Luna it is passed through as ``openrouter_reasoning``, whose
    ``effort`` enum accepts the whole ladder including ``max`` (GPT-5.6's
    highest) — sidestepping the pinned pydantic-ai's unified ``thinking`` map,
    which tops out at xhigh and would silently downgrade both xhigh and max to
    ``high``. On Sonnet 4.6 and Opus 4.7 the effort feeds the unified
    ``thinking`` setting, which pydantic-ai maps to adaptive thinking + effort
    (https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking),
    where Claude decides per-request whether and how much to think; ``max`` is
    an OpenAI tier, so it clamps to ``xhigh`` there. Falls back to medium for a
    missing/unknown value.

    Anthropic runs return a model *string*; OpenRouter runs return a cached
    ``SerniaOpenRouterModel`` instance, which is what carries the web-search
    domain allowlist onto OpenRouter's ``web`` plugin.
    """
    choice = get_model_choice(key)
    resolved_effort = get_thinking_effort(effort)

    if choice.provider == "anthropic":
        # "max" is an OpenAI tier; Anthropic's highest adaptive effort is
        # "xhigh", so clamp it there for Claude models.
        anthropic_effort = "xhigh" if resolved_effort == "max" else resolved_effort
        return {
            "model": choice.model_string,
            "model_settings": AnthropicModelSettings(
                anthropic_cache_instructions=True,
                anthropic_cache_tool_definitions=True,
                anthropic_cache_messages=True,
                thinking=anthropic_effort,
            ),
        }

    return {
        "model": resolve_model(choice.model_string),
        "model_settings": build_openrouter_settings(resolved_effort),
    }


def build_openrouter_settings(effort: str | None = None) -> OpenRouterModelSettings:
    """Production OpenRouter settings for a given reasoning effort.

    Shared by ``build_run_kwargs`` and the A/B harness so an experiment measures
    the model, not a differently-configured gateway.

    Effort goes through ``openrouter_reasoning`` (the gateway's own ``reasoning``
    body field) rather than the unified ``thinking`` setting, which pydantic-ai's
    ``_OPENROUTER_EFFORT_MAP`` collapses at ``high``. ``enabled`` is set
    explicitly because some reasoning-optional routes ignore ``effort`` alone.

    No prompt-cache knob: OpenRouter reaches OpenAI over Chat Completions, where
    caching is automatic (cached tokens come back in ``prompt_tokens_details``)
    but the Responses-only ``prompt_cache_retention="24h"`` extension is
    unavailable — so infrequent scheduled runs fall back to OpenAI's default
    cache window.

    ``usage.include`` makes OpenRouter return the real dollar cost per request.
    The pinned genai-prices snapshot has no entry for ``openai/gpt-5.6-luna``
    under ``openrouter``, so this is the only cost signal on OpenRouter runs —
    see ``test_model_pricing.py``.
    """
    return OpenRouterModelSettings(
        openrouter_reasoning=cast(Any, {"effort": get_thinking_effort(effort), "enabled": True}),
        openrouter_provider={"only": cast(Any, list(OPENROUTER_ALLOWED_PROVIDERS))},
        openrouter_usage={"include": True},
    )


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
