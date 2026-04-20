select
  trace_id,
  start_timestamp,
  MAX(attributes ->> 'agent_name') OVER (PARTITION BY  r.trace_id) as agent_name,
  -- MAX(attributes ->> 'trigger_source') OVER (PARTITION BY  r.trace_id) as trigger_source_old,
  MAX((attributes ->> 'metadata') ->> 'trigger_source') OVER (PARTITION BY  r.trace_id) as trigger_source,
  attributes ->> 'gen_ai.request.model' as model,
  (attributes ->> 'operation.cost')::float as llm_cost,
  (attributes ->> 'gen_ai.usage.input_tokens')::int as input_tokens,
  COALESCE(
    (attributes ->> 'gen_ai.usage.details.cache_read_tokens')::int,
    (attributes ->> 'gen_ai.usage.details.cache_read_input_tokens')::int
  ) as cache_read_tokens,
  COALESCE(
    (attributes ->> 'gen_ai.usage.details.cache_write_tokens')::int,
    (attributes ->> 'gen_ai.usage.details.cache_creation_input_tokens')::int
  ) as cache_write_tokens,
  (attributes ->> 'gen_ai.usage.output_tokens')::int as output_tokens
from records as r
QUALIFY COALESCE(agent_name, '') LIKE $agent_name and llm_cost is not null
order by start_timestamp desc
limit 10
