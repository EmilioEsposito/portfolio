-- Unpriced models: LLM spans that have a model + token usage but NO
-- `operation.cost`. pydantic-ai stamps `operation.cost` (via genai-prices) at
-- emit time; a model genai-prices doesn't know gets no cost, so it silently
-- vanishes from the cost panels (which filter `operation.cost is not null`).
--
-- This panel surfaces those models so they don't disappear unnoticed. If a
-- model shows up here, bump `genai-prices` (and, for the v1 hard-coded panel,
-- add a pricing tier) so it gets costed. Intentionally global (no $agent_name
-- filter) — this is a cost-hygiene alarm across all agents.
--
-- Exception: OpenRouter routes (`openai/...`) are not in genai-prices at all,
-- so `SerniaOpenRouterModel` stamps `operation.cost` from OpenRouter's own
-- usage accounting instead. They should therefore NOT appear here — if one
-- does, that stamping has broken (see api/src/sernia_ai/model_config.py).
--
-- A dev/CI guardrail (api/src/tests/test_model_pricing.py) catches models added
-- in code; this panel additionally catches anything that reaches prod telemetry
-- unpriced (e.g. a model selected via the runtime model_config DB row).
--
-- Scoped to production: this is a real-spend alarm, and it keeps synthetic
-- local models (e.g. pydantic-ai's TestModel, reported as "test") out.
select
  attributes ->> 'gen_ai.request.model' as model,
  count(*) as unpriced_spans,
  min(start_timestamp) as first_seen,
  max(start_timestamp) as last_seen
from records
where deployment_environment = 'production'
  and attributes ->> 'gen_ai.request.model' is not null
  and attributes ->> 'operation.cost' is null
  and (
    attributes ->> 'gen_ai.usage.input_tokens' is not null
    or attributes ->> 'gen_ai.usage.output_tokens' is not null
  )
group by 1
order by unpriced_spans desc
limit 50
