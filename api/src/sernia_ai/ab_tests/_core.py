"""Shared harness for sernia_ai A/B test experiments.

One file per experiment (e.g. `model_comparison_search.py`,
`model_comparison_rote.py`) defines its prompts and variants, then calls
`run_experiment()` from here. All the PydanticAI / PydanticEvals / Logfire
wiring lives below so experiment files stay short.

Key building blocks:

- `run_experiment()` — orchestrates the Dataset + per-variant evaluate() loop.
- `build_experiment_harness()` — lower-level if you need direct access to the
  `Dataset` and the `_make_task` factory (e.g. to attach custom Evaluators).

Logfire terminology reminder:
    Classical DS "experiment" = pydantic-evals `Dataset`
    Classical DS "variant / arm" = pydantic-evals `Experiment` (one evaluate() call)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from contextlib import asynccontextmanager

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.settings import ModelSettings
from pydantic_evals import Case, Dataset
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.identity import TRIGGER_BOT_ID, TRIGGER_BOT_NAME
from api.src.database.database import AsyncSessionFactory
from api.src.google.common.config import GOOGLE_DELEGATION_EMAIL
from api.src.sernia_ai.config import WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import sernia_agent
from api.src.sernia_ai.model_config import build_run_kwargs
from api.src.sernia_ai.tools.workspace_tools import workspace_tools_disabled
from api.src.utils.openrouter_models import build_openrouter_settings, resolve_model

# Default variants — shared across experiments unless an experiment overrides.
# GPT goes through OpenRouter (same gateway as production; see model_config.py).
DEFAULT_VARIANTS: dict[str, str] = {
    "sonnet-4-6": "openrouter:anthropic/claude-sonnet-4.6",
    "gpt-5.6-luna": "openrouter:openai/gpt-5.6-luna",
}


def _model_settings_for(model: str, thinking: str) -> ModelSettings | AnthropicModelSettings:
    """Provider-appropriate model_settings with a shared thinking level.

    For Anthropic and OpenRouter we preserve the production settings so
    behaviour mirrors the live agent; for anything else we pass only the
    unified `thinking` field.
    """
    if model.startswith("anthropic:"):
        return AnthropicModelSettings(
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            anthropic_cache_messages=True,
            thinking=thinking,
        )
    if model.startswith("openrouter:anthropic/"):
        key = "sonnet-4-6" if "sonnet" in model else "opus-4-7"
        return build_run_kwargs(key, thinking)["model_settings"]
    if model.startswith("openrouter:"):
        # Reuse production's OpenRouter settings (reasoning effort, provider
        # pinning, usage accounting) so an experiment measures the model, not a
        # different gateway configuration. The effort ladder is the same
        # vocabulary as `thinking` here.
        return build_openrouter_settings(thinking)
    return ModelSettings(thinking=thinking)


@asynccontextmanager
async def _agent_run_context(
    model: str | Model,
    *,
    with_tools: bool,
    disable_builtin_tools: bool,
    thinking: str,
):
    """Apply model + model_settings + toolset overrides for a single run.

    ``model`` is normally a model string. A ``Model`` instance is used as-is
    with no provider settings — that is how the offline smoke test drives the
    harness without a network call.
    """
    if isinstance(model, str):
        overrides: dict = {
            # `resolve_model` records the billed cost on OpenRouter variants
            # (see model_config.SerniaOpenRouterModel); non-OpenRouter strings
            # pass through untouched.
            "model": resolve_model(model),
            "model_settings": _model_settings_for(model, thinking),
        }
    else:
        overrides = {"model": model}
    if not with_tools:
        overrides["toolsets"] = []
    if disable_builtin_tools:
        # Clears the native web search/fetch tools the WebSearch/WebFetch
        # capabilities contribute (rote experiments want zero tool noise).
        # Providers that can't serve a native tool drop it on their own
        # (`optional=True` in general_agent.py), so no per-provider filtering is needed.
        overrides["native_tools"] = []

    with sernia_agent.override(**overrides):
        if with_tools:
            yield
        else:
            # The workspace file tools come from a capability, which
            # `toolsets=[]` does not reach.
            with workspace_tools_disabled():
                yield


async def _build_deps(session: AsyncSession) -> SerniaDeps:
    """Minimal SerniaDeps for a non-interactive CLI run."""
    return SerniaDeps(
        db_session=session,
        conversation_id=f"ab_test_{uuid.uuid4()}",
        user_identifier=TRIGGER_BOT_ID,
        user_name=TRIGGER_BOT_NAME,
        user_email=GOOGLE_DELEGATION_EMAIL,
        modality="web_chat",
        workspace_path=WORKSPACE_PATH,
    )


def _make_task(
    model: str | Model,
    *,
    with_tools: bool,
    disable_builtin_tools: bool,
    thinking: str,
):
    """Build the task callable pydantic_evals will run for every Case."""

    async def task(prompt: str) -> str:
        async with _agent_run_context(
            model,
            with_tools=with_tools,
            disable_builtin_tools=disable_builtin_tools,
            thinking=thinking,
        ):
            async with AsyncSessionFactory() as session:
                deps = await _build_deps(session)
                result = await sernia_agent.run(prompt, deps=deps)
                output = result.output
                return output if isinstance(output, str) else repr(output)

    return task


def _build_dataset(experiment_name: str, prompts: list[str]) -> Dataset:
    """Dataset name is the A/B test name — groups all variant runs in Logfire."""
    return Dataset(
        name=experiment_name,
        cases=[Case(name=f"prompt-{i}", inputs=p) for i, p in enumerate(prompts)],
    )


async def run_experiment(
    *,
    experiment_name: str,
    prompts: Iterable[str],
    variants: dict[str, str | Model] | None = None,
    with_tools: bool = False,
    disable_builtin_tools: bool = False,
    thinking: str = "medium",
    max_concurrency: int | None = None,
) -> None:
    """Run a dataset of prompts against every variant.

    Args:
        experiment_name: Name for the Dataset (= classical-DS "experiment").
        prompts: Prompts to evaluate; each becomes one Case.
        variants: name -> model string (or a `Model` instance, for offline
            runs). Defaults to `DEFAULT_VARIANTS`.
        with_tools: If True, include sernia's full production toolsets
            (SMS/email/ClickUp — has side effects, use with care).
        disable_builtin_tools: If True, also clear native web search/fetch.
            Useful for rote prompt sets where you want zero tool-call noise.
        thinking: PydanticAI unified reasoning-effort setting
            (minimal/low/medium/high/xhigh; `max` on OpenRouter variants).
        max_concurrency: Max parallel cases per variant (None = unlimited).
    """
    variants = variants or DEFAULT_VARIANTS
    prompt_list = list(prompts)
    dataset = _build_dataset(experiment_name, prompt_list)

    print(f"Dataset (A/B test name): {experiment_name}")
    print(f"Variants:                {variants}")
    print(
        f"Cases: {len(prompt_list)}  with_tools: {with_tools}  "
        f"builtin_tools: {'disabled' if disable_builtin_tools else 'enabled'}  "
        f"thinking: {thinking}"
    )
    print()

    for variant, model in variants.items():
        print(f"=== evaluating variant={variant} ({model}) ===")
        task = _make_task(
            model,
            with_tools=with_tools,
            disable_builtin_tools=disable_builtin_tools,
            thinking=thinking,
        )
        report = await dataset.evaluate(
            task,
            name=variant,
            task_name=variant,
            metadata={
                "experiment_name": experiment_name,
                "model": model if isinstance(model, str) else type(model).__name__,
                "thinking": thinking,
                # trigger_source keeps runs visible in the existing LLM Cost
                # dashboard bucketed under their own label so they don't pollute
                # production trigger-source breakdowns.
                "trigger_source": f"ab_test:{experiment_name}",
            },
            max_concurrency=max_concurrency,
        )
        report.print()
        print()

    print("Done. Cost-per-variant query:")
    print()
    print("  WITH variants AS (")
    print("    SELECT trace_id, attributes ->> 'task_name' AS variant")
    print("    FROM records")
    print("    WHERE span_name = 'evaluate {name}'")
    print(f"      AND (attributes -> 'metadata' ->> 'experiment_name') = '{experiment_name}'")
    print("  )")
    print("  SELECT v.variant,")
    print(
        "         sum((r.attributes ->> 'operation.cost')::float)                          AS cost_usd,"
    )
    print(
        "         sum(coalesce((r.attributes ->> 'gen_ai.usage.input_tokens')::float,  0)) AS input_tokens,"
    )
    print(
        "         sum(coalesce((r.attributes ->> 'gen_ai.usage.output_tokens')::float, 0)) AS output_tokens,"
    )
    print("         count(*) AS n_calls")
    print("  FROM records r")
    print("  JOIN variants v ON r.trace_id = v.trace_id")
    print("  WHERE (r.attributes ->> 'operation.cost') IS NOT NULL")
    print("  GROUP BY v.variant")
    print("  ORDER BY v.variant;")
