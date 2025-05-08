-- Zillow email threads in the past week in the inbox, with at least one reply
SELECT
    e.thread_id,
    e.received_date at time zone 'America/New_York' as received_date_et,
    e.subject,
    e.body_html
FROM
    email_messages AS e
WHERE
    TRUE
    AND 'Label_5289438082921996324' = ANY(e.label_ids) -- label: zillowlisting
    AND 'INBOX' = ANY(e.label_ids) -- label: inbox
    AND e.received_date > CURRENT_DATE - INTERVAL '1 week'
    AND EXISTS ( -- Check for at least one reply
        SELECT 1
        FROM email_messages AS reply
        WHERE e.thread_id = reply.thread_id
          AND reply.subject ILIKE 'Re%'
          AND reply.received_date > CURRENT_DATE - INTERVAL '1 week' -- for efficiency
    )
ORDER BY
    e.thread_id, e.received_date
LIMIT
    10;


