"""
Tests for the Expo push notification service.

Moved verbatim from ``api/src/push/service.py``.

Note: This requires a live database and a valid token for the specified user.
You might want to mock the database session and send_push_message in more complex tests.
"""

import logfire
import pytest

from api.src.push.service import send_push_to_user


@pytest.mark.asyncio
async def test_send_push_to_user():
    """Basic integration test for sending a notification to an email."""
    # Optionally register a known token first for reliability
    # test_token = "ExponentPushToken[...your_token...]";
    # async with AsyncSessionFactory() as session:
    #     await register_token(test_email, test_token, session)
    test_emails = ["espo412@gmail.com", "emilio@serniacapital.com"]

    ## naive approach
    for test_email in test_emails:
        logfire.info(f"[TEST] Attempting to send notification to email {test_email}")
        await send_push_to_user(
            email=test_email,
            title="Pytest Hello World!",
            body="This is a test notification from pytest.",
            data={"test": True},
        )

    ## with a custom session
    # async with AsyncSessionFactory() as session:
    #     for test_email in test_emails:
    #         logfire.info(f"[TEST] Attempting to send notification to email {test_email}")
    #         try:
    #             await send_push_to_user(
    #                 email=test_email,
    #                 title="Pytest Hello World!",
    #                 body="This is a test notification from pytest.",
    #                 data={"test": True},
    #                 db=session
    #         )
    #             # Basic assertion: Check if the function ran without throwing exceptions
    #             assert True
    #         except Exception as e:
    #             pytest.fail(f"send_push_to_user failed: {e}")
