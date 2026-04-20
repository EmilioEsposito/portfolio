select
  trace_id,
  start_timestamp,
  MAX(attributes ->> 'agent_name') OVER (PARTITION BY  r.trace_id) as agent_name,
  -- MAX(attributes ->> 'trigger_source') OVER (PARTITION BY  r.trace_id) as trigger_source_old,
  MAX((attributes ->> 'metadata') ->> 'trigger_source') OVER (PARTITION BY  r.trace_id) as trigger_source,
  (attributes ->> 'operation.cost')::float as llm_cost
from records as r
QUALIFY agent_name is not null and llm_cost is not null
order by start_timestamp desc
limit 10
