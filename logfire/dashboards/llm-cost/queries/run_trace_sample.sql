select
  trace_id,
  min(start_timestamp) as run_start,
  max(attributes ->> 'agent_name') as agent_name,
  max((attributes ->> 'metadata') ->> 'trigger_source') as trigger_source,
  sum((attributes ->> 'operation.cost')::float) as llm_cost,
  sum((attributes ->> 'gen_ai.usage.input_tokens')::int) as input_tokens,
  sum(COALESCE(
    (attributes ->> 'gen_ai.usage.details.cache_read_tokens')::int,
    (attributes ->> 'gen_ai.usage.details.cache_read_input_tokens')::int
  )) as cache_read_tokens,
  sum(COALESCE(
    (attributes ->> 'gen_ai.usage.details.cache_write_tokens')::int,
    (attributes ->> 'gen_ai.usage.details.cache_creation_input_tokens')::int
  )) as cache_write_tokens,
  sum((attributes ->> 'gen_ai.usage.output_tokens')::int) as output_tokens
from records as r
group by trace_id
having COALESCE(max(attributes ->> 'agent_name'), '') LIKE $agent_name
   and sum((attributes ->> 'operation.cost')::float) is not null
order by run_start desc
limit 50
