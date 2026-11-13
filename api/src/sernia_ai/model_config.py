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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, cast, get_args

import logfire
from opentelemetry import trace
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from sqlalchemy import select

from api.src.sernia_ai.config import OPENROUTER_ALLOWED_PROVIDERS
from api.src.utils.llm_cost_breakdown import ATTR_BILLED_COST, gateway_billed_cost

# pydantic-ai's provider prefix for OpenRouter model strings.
_OPENROUTER_PREFIX = "openrouter"

ModelKey = Literal["gpt-6-luna", "gpt-5.6-luna", "sonnet-4-6", "opus-4-7"]
# Reasoning-effort tiers, ascending. low/medium/high/xhigh come from OpenAI's
# reasoning_effort scale; "max" is GPT-5.6's top tier (added *above* xhigh — it
# gives the model even more time to explore alternatives, run checks, and
# revise). OpenRouter's `reasoning.effort` enum accepts the full ladder
# (max|xhigh|high|medium|low|minimal|none) and rejects anything else with a
# 400, so the tier is passed straight through. "max" is an OpenAI tier — on
# Anthropic models it clamps to "xhigh" (see build_run_kwargs).
ThinkingEffort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL_KEY: ModelKey = "gpt-6-luna"
# Safe fallback only — used when the DB lookup fails or is bypassed. The active
# effort is the DB-backed `model_config` row (seeded/migrated to "max").
DEFAULT_THINKING_EFFORT: ThinkingEffort = "medium"
_VALID_EFFORTS: frozenset[str] = frozenset(get_args(ThinkingEffort))


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


def get_thinking_effort(value: str | None) -> ThinkingEffort:
    """Coerce a stored/user-supplied effort to a valid ThinkingEffort, defaulting to medium."""
    if value in _VALID_EFFORTS:
        return cast(ThinkingEffort, value)
    return DEFAULT_THINKING_EFFORT


# OpenRouter's `reasoning.effort` enum — validated server-side, 400 on anything
# else. A strict superset of ThinkingEffort: the settings UI only offers
# low..max, but the A/B CLI also accepts "minimal" (see ab_tests/_cli.py), and
# coercing that to "medium" would silently run the OpenRouter variant at a
# different depth than its Anthropic counterpart and corrupt the comparison.
# "none" is deliberately excluded — it contradicts the `enabled: True` we send.
_OPENROUTER_EFFORTS: frozenset[str] = _VALID_EFFORTS | {"minimal"}


def get_openrouter_effort(value: str | None) -> str:
    """Coerce an effort to one OpenRouter accepts, defaulting to medium."""
    if value in _OPENROUTER_EFFORTS:
        return cast(str, value)
    return DEFAULT_THINKING_EFFORT


# OTel attribute pydantic-ai normally stamps from genai-prices. The LLM-cost
# dashboard filters on `operation.cost is not null`.
_COST_SPAN_ATTR = "operation.cost"


class SerniaOpenRouterModel(OpenRouterModel):
    """OpenRouter model that puts the billed ``operation.cost`` on the LLM span.

    OpenRouter reports the exact billed amount per request (see
    ``build_openrouter_settings``'s ``usage.include``), and we stamp it. It is
    strictly better data than a price table: it reflects the endpoint actually
    routed to and includes web-search charges. pydantic-ai also prices each
    response with genai-prices, which knows OpenRouter's ``openai/gpt-*-luna``
    routes since 0.1.5, and writes that list-price estimate to
    ``operation.cost`` *after* this model returns. So the billed amount also
    goes on ``operation.billed_cost``, which pydantic-ai never touches, and
    ``BilledCostSpanProcessor`` copies it back onto ``operation.cost`` when the
    span ends. Routes genai-prices doesn't know keep our stamp directly.

    Two things this class did on pydantic-ai 1.x are now done upstream, and
    ``test_model_config.py`` pins both so a regression there fails a test here:

    - **The web-search domain allowlist.** pydantic-ai 2.x sends web search as
      OpenRouter's ``openrouter:web_search`` server tool and forwards the
      tool's ``allowed_domains`` in its parameters. 1.x used the ``web`` plugin
      and dropped the domains, so this class re-attached them.
    - **Distinct encrypted reasoning items in streamed responses.** 1.x keyed
      streamed ``reasoning_details`` by type and position within the chunk, so
      two encrypted items merged into one ThinkingPart (first item's id, last
      item's payload) whose replay 400s with ``invalid_encrypted_content``.
      2.x keys them by OpenRouter's own ``index``, which keeps them apart.
    """

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

    The cost is OpenRouter's fee plus, on BYOK requests, the upstream
    provider's charge (see ``gateway_billed_cost``). Sets both ``operation.cost`` and ``operation.billed_cost``; the latter is
    what ``BilledCostSpanProcessor`` restores after pydantic-ai overwrites the
    former with its list-price estimate. No-ops when the cost is absent (usage
    accounting off, or a provider that doesn't report it) or when the span
    isn't recording. Never raises — a telemetry gap must not fail an agent run.
    """
    try:
        cost = gateway_billed_cost(response.provider_details)
        if cost is None:
            return
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({_COST_SPAN_ATTR: float(cost), ATTR_BILLED_COST: float(cost)})
    except Exception:  # pragma: no cover - defensive
        logfire.debug("failed to stamp OpenRouter cost on span", exc_info=True)


@cache
def _openrouter_model(model_name: str) -> SerniaOpenRouterModel:
    """Cached model instance — each one owns an HTTP client, so don't rebuild per run."""
    return SerniaOpenRouterModel(model_name)


def resolve_model(model_string: str) -> SerniaOpenRouterModel | str:
    """Turn a pydantic-ai model string into the concrete model to run with.

    ``openrouter:`` strings become a cached :class:`SerniaOpenRouterModel` (the
    subclass that records the billed cost); everything else is handed back
    untouched for pydantic-ai's own string inference.

    Use this anywhere a model string reaches ``Agent(...)`` or ``agent.run(...)``
    so no code path silently loses the billed cost.
    """
    if model_string.startswith(f"{_OPENROUTER_PREFIX}:"):
        return _openrouter_model(model_string.split(":", 1)[1])
    return model_string


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


def build_openrouter_settings(effort: str | None = None) -> OpenRouterModelSettings:
    """Production OpenRouter settings for a given reasoning effort.

    Shared by ``build_run_kwargs`` and the A/B harness so an experiment measures
    the model, not a differently-configured gateway. ``effort`` is coerced
    against OpenRouter's enum rather than ``ThinkingEffort``, so the A/B CLI's
    ``minimal`` tier survives instead of being silently rounded up to medium.

    Effort goes through ``openrouter_reasoning`` (the gateway's own ``reasoning``
    body field) rather than the unified ``thinking`` setting, which pydantic-ai's
    ``_OPENROUTER_EFFORT_MAP`` collapses at ``high``. ``enabled`` is set
    explicitly because some reasoning-optional routes ignore ``effort`` alone.

    No prompt-cache knob: OpenRouter reaches OpenAI over Chat Completions, where
    caching is automatic (cached tokens come back in ``prompt_tokens_details``)
    but the Responses-only ``prompt_cache_retention="24h"`` extension is
    unavailable — so infrequent scheduled runs fall back to OpenAI's default
    cache window.

    ``usage.include`` makes OpenRouter return the real dollar cost per request,
    which becomes the span's ``operation.cost`` in place of genai-prices'
    list-price estimate — see ``SerniaOpenRouterModel`` and
    ``test_model_pricing.py``.
    """
    return OpenRouterModelSettings(
        openrouter_reasoning=cast(Any, {"effort": get_openrouter_effort(effort), "enabled": True}),
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
