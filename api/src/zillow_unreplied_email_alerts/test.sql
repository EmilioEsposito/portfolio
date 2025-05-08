SELECT
    'testing' AS subject,
    TO_CHAR(
        CURRENT_TIMESTAMP at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str;