SELECT
    time_bucket($resolution,start_timestamp) as x,
    deployment_environment as dim,
    sum((attributes ->> 'operation.cost')::float) as llm_cost,
    count(distinct trace_id) as runs
FROM
    records
where attributes ->> 'operation.cost' is not null
group by 1,2
;
