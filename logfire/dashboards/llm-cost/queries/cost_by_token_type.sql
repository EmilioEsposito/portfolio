with base as (
  select
    start_timestamp,
    attributes ->> 'gen_ai.request.model' as model,
    COALESCE((attributes ->> 'gen_ai.usage.input_tokens')::float, 0) as total_input_tokens,
    COALESCE(
      (attributes ->> 'gen_ai.usage.details.cache_read_tokens')::float,
      (attributes ->> 'gen_ai.usage.details.cache_read_input_tokens')::float,
      0
    ) as cache_read_tokens,
    COALESCE(
      (attributes ->> 'gen_ai.usage.details.cache_write_tokens')::float,
      (attributes ->> 'gen_ai.usage.details.cache_creation_input_tokens')::float,
      0
    ) as cache_write_tokens,
    COALESCE((attributes ->> 'gen_ai.usage.output_tokens')::float, 0) as output_tokens
  from records
  where attributes ->> 'operation.cost' is not null
),
priced as (
  select
    start_timestamp,
    -- Non-cached input = total input minus cache read/write. Works for both
    -- Anthropic (where genai-prices sums input+cache_read+cache_write into the
    -- top-level input_tokens) and OpenAI (where input_tokens already includes
    -- cached_tokens; cache_write is 0).
    greatest(total_input_tokens - cache_read_tokens - cache_write_tokens, 0) * (case
      when model like 'claude-sonnet%' then 3.0
      when model like 'claude-haiku%' then 1.0
      when model like 'gpt-4o-mini%' then 0.15
      when model like 'gpt-4o%' then 2.5
      when model like 'gpt-4.1-mini%' then 0.40
      when model like 'gpt-4.1%' then 2.0
      when model like 'gpt-5.4-nano%' then 0.20
      when model like 'gpt-5.4-mini%' then 0.75
      when model like 'gpt-5.4%' then 2.5
      else 0
    end) / 1e6 as cost_input_non_cached,
    cache_read_tokens * (case
      when model like 'claude-sonnet%' then 0.30
      when model like 'claude-haiku%' then 0.10
      when model like 'gpt-4o-mini%' then 0.075
      when model like 'gpt-4o%' then 1.25
      when model like 'gpt-5.4-nano%' then 0.02
      when model like 'gpt-5.4-mini%' then 0.075
      when model like 'gpt-5.4%' then 0.25
      else 0
    end) / 1e6 as cost_cache_input,
    cache_write_tokens * (case
      when model like 'claude-sonnet%' then 3.75
      when model like 'claude-haiku%' then 1.25
      else 0
    end) / 1e6 as cost_cache_write,
    output_tokens * (case
      when model like 'claude-sonnet%' then 15.0
      when model like 'claude-haiku%' then 5.0
      when model like 'gpt-4o-mini%' then 0.60
      when model like 'gpt-4o%' then 10.0
      when model like 'gpt-4.1-mini%' then 1.60
      when model like 'gpt-4.1%' then 8.0
      when model like 'gpt-5.4-nano%' then 1.25
      when model like 'gpt-5.4-mini%' then 4.5
      when model like 'gpt-5.4%' then 15.0
      else 0
    end) / 1e6 as cost_output
  from base
),
unpivoted as (
  select start_timestamp, 'input_non_cached' as dim, cost_input_non_cached as llm_cost from priced
  union all
  select start_timestamp, 'cache_input', cost_cache_input from priced
  union all
  select start_timestamp, 'cache_write', cost_cache_write from priced
  union all
  select start_timestamp, 'output', cost_output from priced
)
select
  time_bucket($resolution, start_timestamp) as x,
  dim,
  sum(llm_cost) as llm_cost
from unpivoted
group by 1, 2
;
