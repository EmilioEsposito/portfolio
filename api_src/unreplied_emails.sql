-- unreplied emails within past week that were received >4 hours ago
SELECT
    -- e.id,
    -- e.message_id,
    -- e.received_date,
    -- e.received_date at time zone 'America/New_York' as received_date_et,
    -- re.id,
    -- re.message_id,
    -- re.received_date,
    -- re.subject as reply_subject,
    e.subject,
    TO_CHAR(
        e.received_date at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str
FROM
    email_messages AS e
    LEFT JOIN email_messages AS re ON e.thread_id = re.thread_id
    AND re.subject ilike 'Re%'
    AND re.received_date > e.received_date
    AND e.id <> re.id
    AND re.received_date > CURRENT_DATE - INTERVAL '1 week'
    /* uncomment line below to backtest */
    -- AND re.received_date < CURRENT_TIMESTAMP - INTERVAL '24 hour' -- backtest
WHERE
    TRUE
    AND 'Label_5289438082921996324' = ANY(e.label_ids) -- label: zillowlisting
    AND 'INBOX' = ANY(e.label_ids) -- label: inbox
    AND e.subject NOT ilike 'Re%' -- not a reply
    AND e.received_date > CURRENT_DATE - INTERVAL '1 week'
    AND e.received_date < CURRENT_TIMESTAMP - INTERVAL '4 hour'
    AND re.id IS NULL -- no reply found!
ORDER BY
    e.received_date DESC
LIMIT
    10;