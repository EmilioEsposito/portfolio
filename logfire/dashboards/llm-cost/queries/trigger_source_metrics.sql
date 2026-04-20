-- Shared base query for three panels that differ only in which `metrics`
-- Perses picks off the result set: llm_cost, runs, cost_per_run.
with a as (
  select
  trace_id,
  start_timestamp,
  MAX(attributes ->> 'agent_name') OVER (PARTITION BY  r.trace_id) as agent_name,
  MAX((attributes ->> 'metadata') ->> 'trigger_source') OVER (PARTITION BY  r.trace_id) as trigger_source,
  (attributes ->> 'operation.cost')::float as llm_cost
  from records as r
)

SELECT
    time_bucket($resolution,start_timestamp) as x,
    COALESCE(trigger_source, 'unknown') as dim,
    -- COALESCE(agent_name, 'unknown') as dim,
    sum(llm_cost) as llm_cost,
    count(distinct a.trace_id) as runs,
    sum(llm_cost)/count(distinct a.trace_id) as cost_per_run
FROM
    a
where agent_name is not null
group by 1,2
;
