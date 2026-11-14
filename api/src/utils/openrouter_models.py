"""OpenRouter adapter, reasoning settings and cost telemetry shared by agents.

Model selection belongs to each agent. This module only constructs the requested
model with the existing gateway settings and encrypted-reasoning stream fix.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from functools import cache
from typing import Any, Literal, cast, get_args

import logfire
from opentelemetry import trace
from pydantic_ai.messages import ModelResponse, ModelResponseStreamEvent
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models._reasoning_details import from_reasoning_detail
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterStreamedResponse,
)

from api.src.utils.llm_cost_breakdown import ATTR_BILLED_COST, gateway_billed_cost

# Keep the same upstream pin for GPT requests through OpenRouter.
# OpenRouter provider routing for the main agent. OpenRouter load-balances a
# model across every upstream that serves it (for gpt-6-luna: OpenAI, Azure,
# Amazon Bedrock), and those upstreams differ in price by up to ~10x and in
# supported parameters (Bedrock drops `response_format`/`structured_outputs`).
# Pinning to OpenAI keeps the served compute identical to the pre-OpenRouter
# setup — same model, same upstream, just a different gateway. Widen this list
# (or drop it for full load balancing / failover) once that's the goal.
OPENROUTER_ALLOWED_PROVIDERS: list[str] = ["openai"]


# pydantic-ai's provider prefix for OpenRouter model strings.
_OPENROUTER_PREFIX = "openrouter"

# Reasoning-effort tiers, ascending. low/medium/high/xhigh come from OpenAI's
# reasoning_effort scale; "max" is GPT-5.6's top tier (added *above* xhigh — it
# gives the model even more time to explore alternatives, run checks, and
# revise). OpenRouter's `reasoning.effort` enum accepts the full ladder
# (max|xhigh|high|medium|low|minimal|none) and rejects anything else with a
# 400, so the tier is passed straight through. "max" is an OpenAI tier — on
# Anthropic models it clamps to "xhigh" (see build_run_kwargs).
ThinkingEffort = Literal["low", "medium", "high", "xhigh", "max"]

# Safe fallback only — used when the DB lookup fails or is bypassed. The active
# effort is the DB-backed `model_config` row (seeded/migrated to "max").
DEFAULT_THINKING_EFFORT: ThinkingEffort = "medium"

_VALID_EFFORTS: frozenset[str] = frozenset(get_args(ThinkingEffort))


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


class SerniaOpenRouterStreamedResponse(OpenRouterStreamedResponse):
    """Streamed response that keys each encrypted reasoning item by its own id.

    A streamed response can carry two encrypted reasoning items (OpenAI emits
    one per reasoning segment when reasoning interleaves with several tool
    calls). If both land on one vendor key, the parts manager merges them into
    one ThinkingPart with the first item's id and the last item's payload, and
    replaying it fails the run with ``invalid_encrypted_content``
    (production, 2026-08-05).

    pydantic-ai 1.x keyed details by position within the chunk, which merged
    them every time. 2.x keys them by OpenRouter's ``index`` and falls back to
    the position when ``index`` is absent, so items without an index, or with
    a repeated one, still merge. Encrypted payloads arrive whole and carry the
    item id, so keying by that id cannot split one item. Every other detail
    keeps upstream's keying. ``test_model_config.py`` has a canary that fails
    once upstream stops merging, so this class can then be deleted.
    """

    def _map_thinking_delta(self, choice: Any) -> Iterable[ModelResponseStreamEvent]:
        details = getattr(choice.delta, "reasoning_details", None) or []
        if not any(d.type == "reasoning.encrypted" and d.id for d in details):
            yield from super()._map_thinking_delta(choice)
            return
        for i, detail in enumerate(details):
            thinking_part = from_reasoning_detail(detail, self._provider_name)
            if detail.type == "reasoning.encrypted" and detail.id:
                vendor_id = f"reasoning_detail_{detail.type}_{detail.id}"
            else:
                position = detail.index if detail.index is not None else i
                vendor_id = f"reasoning_detail_{detail.type}_{position}"
            yield from self._parts_manager.handle_thinking_delta(
                vendor_part_id=vendor_id,
                id=thinking_part.id,
                content=thinking_part.content,
                signature=thinking_part.signature,
                provider_name=self._provider_name,
                provider_details=thinking_part.provider_details,
            )


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

    It also streams through :class:`SerniaOpenRouterStreamedResponse`, which
    keeps two encrypted reasoning items in one response apart.

    On pydantic-ai 1.x this class also re-attached the web-search domain
    allowlist, which the ``web`` plugin dropped. 2.x sends web search as
    OpenRouter's ``openrouter:web_search`` server tool with the tool's
    ``allowed_domains`` in its parameters; ``test_model_config.py`` pins that.
    """

    @property
    def _streamed_response_cls(self) -> type[OpenRouterStreamedResponse]:
        return SerniaOpenRouterStreamedResponse

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
