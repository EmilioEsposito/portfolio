SELECT *
FROM EXAMPLE_NEON1
order by id;


SELECT received_date, subject
FROM email_messages -- WHERE subject ilike '%blair%'
order by received_date desc;


select 
body_html ilike '%zillow%',
-- received_date is null,
    count(*),
    min(received_date) as first_date,
    max(received_date) as last_date
from email_messages
GROUP BY 1
order by 1 desc;


select * 
from google_oauth_tokens 
order by id;


    em.message_id,
select 
    em.message_id,
    em.thread_id,
    em.received_date,
    em.subject,
    -- em.body_text,
    em.body_html -- html is shown in some sort of container when row is clicked
from email_messages as em
where em.body_html ilike '%zillow%' and subject not like '%daily listing%'
ORDER BY random()
Limit 10 
;

