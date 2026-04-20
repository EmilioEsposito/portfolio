-- Cost by token type, v2.
--
-- Reads per-bucket cost attributes (gen_ai.cost.*) written by the
-- CostBreakdownSpanProcessor in api/src/utils/llm_cost_breakdown.py.
-- No hard-coded per-model rates in SQL — rates come from genai-prices.
--
-- Unlike v1 (cost_by_token_type.sql), this query only sees data from runs
-- emitted after the SpanProcessor was deployed. Kept side-by-side with v1
-- for a parity check. Once parity holds for the desired window, v1 and the
-- hard-coded SQL can be removed.
select
  time_bucket($resolution, start_timestamp) as x,
  dim,
  sum(llm_cost) as llm_cost
from (
  select start_timestamp, 'input_non_cached' as dim,
         (attributes ->> 'gen_ai.cost.input_non_cached')::float as llm_cost
  from records where attributes ->> 'gen_ai.cost.input_non_cached' is not null
  union all
  select start_timestamp, 'cache_input',
         (attributes ->> 'gen_ai.cost.cache_read')::float
  from records where attributes ->> 'gen_ai.cost.cache_read' is not null
  union all
  select start_timestamp, 'cache_write',
         (attributes ->> 'gen_ai.cost.cache_write')::float
  from records where attributes ->> 'gen_ai.cost.cache_write' is not null
  union all
  select start_timestamp, 'output',
         (attributes ->> 'gen_ai.cost.output')::float
  from records where attributes ->> 'gen_ai.cost.output' is not null
) unpivoted
group by 1, 2
;
