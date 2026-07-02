"""
Tests for the Zillow email alerts service.

Moved verbatim from ``api/src/zillow_email/service.py``.

The live test hits real third-party APIs and only runs when explicitly
requested:

    pytest -m live api/src/tests/test_zillow_email_service.py -v -s
"""

import pytest

from api.src.zillow_email.service import check_email_threads, check_unreplied_emails


@pytest.mark.asyncio
async def test_has_unreplied_emails():
    sql_query = """SELECT
    'testing' AS subject,
    TO_CHAR(
        CURRENT_TIMESTAMP at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str;"""
    sent_message_count = await check_unreplied_emails(
        sql=sql_query, target_slugs=["emilio"], mock=True
    )
    assert sent_message_count == 1


@pytest.mark.asyncio
async def test_has_no_unreplied_emails():
    sql_query = """SELECT
    'testing' AS subject,
    TO_CHAR(
        CURRENT_TIMESTAMP at time zone 'America/New_York',
        'Mon DD, HH12:MIpm'
    ) AS received_date_str
    WHERE 1=0;"""
    sent_message_count = await check_unreplied_emails(
        sql=sql_query, target_slugs=["emilio"], mock=True
    )
    assert sent_message_count == 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_check_email_threads():
    await check_email_threads(overwrite_calendar_events=True)
