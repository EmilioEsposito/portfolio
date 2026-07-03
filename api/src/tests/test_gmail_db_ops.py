"""
Integration tests for Gmail database operations and pubsub notification
processing.

Moved verbatim from their service modules
(``api/src/google/gmail/db_ops.py`` and ``api/src/google/pubsub/routes.py``).
These require a reachable Postgres database (and, for the notification test,
Google credentials to talk to the Gmail API):

    pytest api/src/tests/test_gmail_db_ops.py -v -s
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import AsyncSessionFactory
from api.src.google.gmail.db_ops import get_email_by_message_id, save_email_message
from api.src.google.pubsub.routes import process_gmail_notification


# Test helper for creating database sessions
# (moved with the tests from api/src/google/gmail/db_ops.py)
@asynccontextmanager
async def get_test_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a database session specifically for testing"""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.mark.asyncio
async def test_save_email_message():
    """Test saving and retrieving an email message"""
    # Test data with proper datetime
    message_data = {
        "message_id": "1234567890",
        "thread_id": "1234567890",
        "subject": "Test Email",
        "from_address": "test@example.com",
        "to_address": "recipient@example.com",
        "date": datetime.now().isoformat(),  # Current time in ISO format
        "body_text": "Test body",
        "body_html": "<p>Test body</p>",
        "raw_payload": {"test": "data"},
        "label_ids": ["INBOX", "UNREAD"],
    }

    # Test history ID
    history_id = 12345

    saved_msg = None
    async with get_test_session() as session:
        try:
            # Save message with history_id — first call should be a fresh insert
            saved_msg, was_inserted = await save_email_message(session, message_data, history_id)
            assert saved_msg is not None
            assert was_inserted is True
            assert saved_msg.message_id == message_data["message_id"]
            assert saved_msg.first_history_id == history_id
            assert saved_msg.history_ids == [history_id]
            assert saved_msg.label_ids == message_data["label_ids"]

            # Verify we can retrieve it
            retrieved = await get_email_by_message_id(session, message_data["message_id"])
            assert retrieved is not None
            assert retrieved.subject == message_data["subject"]

            # Test update with a new history_id — second call should be an update, not an insert
            new_history_id = 67890
            updated_data = message_data.copy()
            updated_data["raw_payload"] = {"test": "updated data"}
            updated_data["label_ids"] = ["INBOX", "READ"]

            updated_msg, was_inserted_again = await save_email_message(
                session, updated_data, new_history_id
            )
            assert updated_msg is not None
            assert was_inserted_again is False
            assert updated_msg.message_id == message_data["message_id"]
            assert updated_msg.first_history_id == history_id  # First history should not change
            assert new_history_id in updated_msg.history_ids  # New history should be added
            assert updated_msg.label_ids == updated_data["label_ids"]  # Labels should be updated
            assert (
                updated_msg.raw_payload == updated_data["raw_payload"]
            )  # Payload should be updated

        finally:
            # Cleanup: Delete test message
            if saved_msg:
                await session.delete(saved_msg)
                await session.commit()


@pytest.mark.asyncio
async def test_process_gmail_notification():
    """
    Test function to test the process_gmail_notification function.
    """
    # Create test notification data
    pubsub_notification_data = {"emailAddress": "emilio@serniacapital.com", "historyId": 6531598}

    # Call the function (it creates its own per-message sessions)
    processing_result = await process_gmail_notification(pubsub_notification_data)

    assert processing_result["status"] in ["success", "no_messages", "retry_needed"]

    # Log the results
    for email_msg in processing_result["messages"]:
        print(f"Processed email: {email_msg['subject']} from {email_msg['from_address']}")
        assert email_msg["subject"] is not None
