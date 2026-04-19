# Sernia AI — A/B Test Harness

Run the same prompts through `sernia_agent` under different models (or settings) using `pydantic_evals.Dataset` / `Case` / `evaluate()`. Cost + token spans from the real agent nest under each case, so comparisons are queryable in Logfire.

## Layout

```
ab_tests/
  _core.py                       # shared harness (overrides, deps, run_experiment)
  _cli.py                        # shared argparse wiring
  model_comparison_search.py     # tool-heavy (web search) comparison
  model_comparison_rote.py       # pure-text no-tools comparison
  # add new experiments as standalone files that import _core and _cli
```

Each experiment file declares its own `PROMPTS` list, then calls `run_experiment(...)`. Shared behaviour (model overrides, builtin-tool filtering, deps construction, Dataset/Case wiring, Logfire-friendly metadata) lives in `_core.py` so experiment files stay short.

## Quick start

```bash
source .venv/bin/activate

# Tool-heavy comparison (web search + reasoning)
python -m api.src.sernia_ai.ab_tests.model_comparison_search

# Pure-text baseline (no tools)
python -m api.src.sernia_ai.ab_tests.model_comparison_rote

# Override variants, reasoning, concurrency:
python -m api.src.sernia_ai.ab_tests.model_comparison_search \
    --variant sonnet=anthropic:claude-sonnet-4-6 \
    --variant gpt5=openai-responses:gpt-5.4 \
    --thinking high \
    --max-concurrency 2
```

Each script prints a tailored Logfire SQL query at the end.

## Adding a new experiment

Copy `model_comparison_rote.py`, rename, and edit:

1. Replace the module docstring with your description.
2. Replace `PROMPTS` with your prompt list.
3. Change `default_experiment_prefix` in the `build_parser(...)` call to something meaningful (used in the auto-generated `--experiment-name` default).
4. Pass the right `disable_builtin_tools` value to `run_experiment(...)` — `False` to keep WebSearchTool, `True` to eliminate tool-call noise.

No need to touch `_core.py` unless you're changing the harness itself (e.g. adding `Evaluator` wiring or a new override knob).

## Terminology mismatch vs. classical A/B testing

pydantic-evals inverts the usual data-science terminology — be aware:

| Classical DS | pydantic-evals |
|---|---|
| **Experiment** (the A/B test itself: "compare Sonnet vs GPT-5.4 on these prompts") | **Dataset** |
| **Variant / arm** (one model + settings) | **Experiment** |

So the harness sets **`Dataset(name=<experiment_name>)`** to group all variants, and each call to **`evaluate(name=<variant>)`** is one arm. In the Logfire UI, navigate to *Datasets → `<experiment_name>` → Experiments* to see variants side-by-side.

## Isolating an experiment in Logfire

Each variant = one `evaluate()` = one trace with all its cases nested inside. The experiment root span has `span_name = "evaluate {name}"`, `task_name = <variant>`, and `metadata.experiment_name = <name>`. Use that as the anchor and join to every cost span in the trace:

```sql
WITH variants AS (
  SELECT trace_id, attributes ->> 'task_name' AS variant
  FROM records
  WHERE span_name = 'evaluate {name}'
    AND (attributes -> 'metadata' ->> 'experiment_name') = '<experiment_name>'
)
SELECT v.variant,
       sum((r.attributes ->> 'operation.cost')::float)                         AS cost_usd,
       sum(coalesce((r.attributes ->> 'gen_ai.usage.input_tokens')::float, 0)) AS input_tokens,
       sum(coalesce((r.attributes ->> 'gen_ai.usage.output_tokens')::float, 0)) AS output_tokens,
       count(*) AS n_calls
FROM records r
JOIN variants v ON r.trace_id = v.trace_id
WHERE (r.attributes ->> 'operation.cost') IS NOT NULL
GROUP BY v.variant
ORDER BY v.variant;
```

`metadata.trigger_source` is set to `ab_test:<experiment_name>` so these runs bucket cleanly in the existing *LLM Cost By Trigger Source* dashboard panel instead of polluting production trigger data.

## Safety

`sernia_agent` has many write-capable tools (SMS, email, ClickUp, scheduling). **By default** this harness passes `toolsets=[]` via `agent.override()`, so the run is a pure LLM comparison with no tool calls and no side effects.

To run with the full toolset (e.g. to compare tool-calling behaviour), pass `--with-tools`. Only do this with prompts you've vetted to be read-only and idempotent.

## Provider-specific settings

- `thinking` is PydanticAI's unified reasoning-effort setting — supported by Anthropic, OpenAI Responses, Google, Groq, Bedrock, xAI, etc. Use `minimal` / `low` / `medium` / `high` / `xhigh`.
- `anthropic_cache_*` fields are still passed for Anthropic variants (so the harness mirrors production caching behaviour there); they're silently ignored on other providers.
- `WebSearchTool` is kept for all providers that support it (Anthropic, OpenAI Responses, Groq, Google, xAI, OpenRouter). Rote mode clears it entirely via `disable_builtin_tools=True`.
