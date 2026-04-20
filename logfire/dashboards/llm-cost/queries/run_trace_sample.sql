select
  trace_id,
  min(start_timestamp) as run_start,
  max(attributes ->> 'agent_name') as agent_name,
  max((attributes ->> 'metadata') ->> 'trigger_source') as trigger_source,
  sum((attributes ->> 'operation.cost')::float) as llm_cost
from records as r
group by trace_id
having max(attributes ->> 'agent_name') is not null
   and sum((attributes ->> 'operation.cost')::float) is not null
order by run_start desc
limit 50
