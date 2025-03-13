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

-- unreplied emails within past week that were received >4 hours ago
SELECT
    -- e.id,
    --     e.message_id,
    -- e.received_date,
    -- convert utc to ET
    -- format date as Mar 3, 2:25pm
    TO_CHAR(
        e.received_date at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str,
    e.subject -- re.id,
    -- re.message_id,
    -- re.received_date,
    -- re.subject,
FROM
    email_messages AS e
    LEFT JOIN email_messages AS re ON e.thread_id = re.thread_id
    AND re.subject ilike 'Re%'
    AND re.received_date > e.received_date
    AND e.id <> re.id
    AND re.received_date > CURRENT_DATE - INTERVAL '1 week'
    -- AND re.received_date < CURRENT_TIMESTAMP - INTERVAL '24 hour' -- uncomment to backtest
WHERE
    TRUE
    AND 'Label_5289438082921996324' = ANY(e.label_ids) -- label: zillowlisting
    AND 'INBOX' = ANY(e.label_ids) -- label: inbox
    AND e.subject ilike '%requesting%'
    AND e.subject NOT ilike 'Re%'
    AND e.received_date > CURRENT_DATE - INTERVAL '1 week'
    AND e.received_date < CURRENT_TIMESTAMP - INTERVAL '4 hour'
    AND re.id IS NULL -- no reply found!
ORDER BY
    e.received_date DESC
LIMIT
    10;

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
    updated_at DESC;

SELECT
    *
FROM
    email_messages AS e
WHERE
    6596931 = ANY(history_ids);

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