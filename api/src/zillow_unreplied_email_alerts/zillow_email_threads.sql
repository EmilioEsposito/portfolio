-- Zillow email threads in the past week in the inbox, with at least one reply
WITH zillow_threads_with_replies AS (
        SELECT 
            DISTINCT
            e.thread_id
        FROM
            email_messages AS e
        WHERE
            TRUE
            AND 'INBOX' = ANY(e.label_ids) -- label: inbox
            AND e.subject ILIKE 'Re%' -- is a reply email (this filers out theads with no replies)
            AND 'Label_5289438082921996324' = ANY(e.label_ids) -- label: zillowlisting
            AND e.received_date > CURRENT_DATE - INTERVAL '1 week'
)
SELECT
    e.thread_id,
    e.received_date at time zone 'America/New_York' as email_timestamp_et,
    e.subject,
    e.body_html,
    -- e.body_text,
    -- SPLIT_PART(e.body_text, '>', 1) || SPLIT_PART(e.body_text, '>', 2) || '...' as body_text_short,
    e.from_address,
    e.to_address
FROM email_messages AS e
INNER JOIN zillow_threads_with_replies AS zt
    ON e.thread_id = zt.thread_id
WHERE
    TRUE
    AND e.received_date > CURRENT_DATE - INTERVAL '1 week' -- for efficiency
ORDER BY
    e.thread_id, e.received_date
LIMIT
    10;

