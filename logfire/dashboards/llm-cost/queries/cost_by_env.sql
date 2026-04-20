-- `agent_name` lives on the agent root span, not the per-LLM-call spans that
-- carry `operation.cost`. Build a trace → agent_name lookup once, then JOIN
-- it back to filter. COALESCE lets rows in traces without any agent_name still
-- match when "All" is selected (customAllValue='%', '' LIKE '%' is true).
with trace_agent_name as (
  select
    trace_id,
    MAX(attributes ->> 'agent_name') as trace_agent_name
  from records
  group by trace_id
)
SELECT
    time_bucket($resolution, r.start_timestamp) as x,
    r.deployment_environment as dim,
    sum((r.attributes ->> 'operation.cost')::float) as llm_cost,
    count(distinct r.trace_id) as runs
FROM records as r
left join trace_agent_name on r.trace_id = trace_agent_name.trace_id
where r.attributes ->> 'operation.cost' is not null
  and COALESCE(trace_agent_name.trace_agent_name, '') LIKE $agent_name
group by 1, 2
;
