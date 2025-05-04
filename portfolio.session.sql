SELECT
    *
FROM
    EXAMPLE_NEON1
ORDER BY
    id;

SELECT
    received_date,
    subject
FROM
    email_messages -- WHERE subject ilike '%blair%'
ORDER BY
    received_date DESC;

SELECT
    body_html ilike '%zillow%',
    -- received_date is null,
    count(*),
    min(received_date) AS first_date,
    max(received_date) AS last_date
FROM
    email_messages
GROUP BY
    1
ORDER BY
    1 DESC;

SELECT
    *
FROM
    alembic_version;

SELECT
    *
FROM
    oauth_credentials
ORDER BY
    id;

SELECT
    em.message_id,
    em.thread_id,
    em.received_date,
    em.subject,
    -- em.body_text,
    em.body_html -- html is shown in some sort of container when row is clicked
FROM
    email_messages AS em
WHERE
    em.body_html ilike '%zillow%'
    AND subject NOT LIKE '%daily listing%'
ORDER BY
    random()
LIMIT
    10;

SELECT
    *
FROM
    email_messages AS e
LIMIT
    100;

SELECT
    received_date,
    updated_at,
    label_ids,
    subject,
    first_history_id,
    history_ids,
    raw_payload
FROM
    email_messages AS e
ORDER BY
    received_date DESC
LIMIT
    10;

SELECT
    *
FROM
    email_messages AS e
WHERE
    e.message_id = '192fc775897f1518';

SELECT
    max(array_length(history_ids, 1))
FROM
    email_messages;

SELECT
    *
FROM
    email_messages
WHERE
    array_length(label_ids, 1) > 1;
;


SELECT
    received_date,
    created_at,
    created_at - received_date AS time_to_receive,
    e.subject

FROM
    email_messages AS e
WHERE
    'Label_5289438082921996324' = ANY(e.label_ids) -- label: zillowlisting
    AND 'INBOX' = ANY(e.label_ids) -- label: inbox
    AND e.subject NOT ilike 'Re%' -- not a reply
    AND e.received_date > CURRENT_DATE - INTERVAL '1 week'
ORDER BY
    created_at DESC
LIMIT
    10;