SELECT *
FROM EXAMPLE_NEON1
order by id;
SELECT received_date,
    subject
FROM email_messages -- WHERE subject ilike '%blair%'
order by received_date desc;
select body_html ilike '%zillow%',
    -- received_date is null,
    count(*),
    min(received_date) as first_date,
    max(received_date) as last_date
from email_messages
GROUP BY 1
order by 1 desc;
select *
FROM alembic_version;
select *
from oauth_credentials
order by id;
select em.message_id,
    em.thread_id,
    em.received_date,
    em.subject,
    -- em.body_text,
    em.body_html -- html is shown in some sort of container when row is clicked
from email_messages as em
where em.body_html ilike '%zillow%'
    and subject not like '%daily listing%'
ORDER BY random()
Limit 10;
select *
from email_messages as e
limit 100;


-- unreplied emails within past week that were received >4 hours ago
select 
-- e.id,
--     e.message_id,
    -- e.received_date,
    -- convert utc to ET
    -- format date as Mar 3, 2:25pm
    TO_CHAR(e.received_date at time zone 'America/New_York', 'Mon DD, HH12:MIpm') as received_date_str,
    e.subject
    -- re.id,
    -- re.message_id,
    -- re.received_date,
    -- re.subject,
from email_messages as e
    left join email_messages as re on e.thread_id = re.thread_id
    and re.subject ilike 'Re%'
    and re.received_date > e.received_date
    and e.id <> re.id
    and re.received_date > current_date - interval '1 week'
where 
    e.raw_payload::text like '%Label_5289438082921996324%' -- label: zillowlisting
    and e.subject ilike '%requesting%'
    and e.subject not ilike 'Re%'
    and re.id is null
    and e.received_date > current_date - interval '1 week'
    and e.received_date < current_timestamp - interval '4 hour'
order by e.received_date desc
limit 10;

