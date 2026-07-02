"""
Database operations for Gmail-related functionality.
"""

from datetime import datetime
from typing import Any

import logfire
import pytz
from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.google.gmail.models import EmailMessage


async def save_email_message(
    session: AsyncSession,
    message_data: dict[str, Any],
    history_id: int | None = None
) -> tuple[EmailMessage | None, bool]:
    """
    Save a Gmail message to the database.

    Args:
        session: SQLAlchemy async session
        message_data: Processed message data containing all required fields
        history_id: The Gmail history ID related to this message (optional)

    Returns:
        Tuple ``(email_msg, was_inserted)``:
        - ``email_msg``: The saved ``EmailMessage`` instance, or ``None`` if the
          save failed.
        - ``was_inserted``: ``True`` if this call inserted a brand-new row;
          ``False`` if it updated an existing row (e.g. pubsub redelivery or
          Gmail label-change notification for a message we'd already saved).
    """
    try:
        message_id = message_data.get('message_id')
        logfire.info(f"Saving email message {message_id} to database")

        # Check whether this message_id already exists. This lets callers
        # distinguish truly new emails from pubsub redeliveries / label-change
        # notifications (both of which trigger upserts on the same message_id).
        existing = await get_email_by_message_id(session, message_id)
        was_inserted = existing is None

        # Parse the date and ensure it's timezone aware
        date_str = message_data['date']
        try:
            received_date = datetime.fromisoformat(date_str)
            if received_date.tzinfo is None:
                received_date = pytz.UTC.localize(received_date)
            else:
                received_date = received_date.astimezone(pytz.UTC)
            # Remove timezone info (column is TIMESTAMP WITHOUT TIME ZONE)
            received_date = received_date.replace(tzinfo=None)
        except ValueError as e:
            logfire.warn(f"Failed to parse date {date_str}, using current UTC time: {str(e)}")
            received_date = datetime.now(pytz.UTC).replace(tzinfo=None)

        # Atomic upsert: insert or update on conflict
        stmt = pg_insert(EmailMessage).values(
            message_id=message_id,
            thread_id=message_data['thread_id'],
            subject=message_data['subject'],
            from_address=message_data['from_address'],
            to_address=message_data['to_address'],
            received_date=received_date,
            body_text=message_data['body_text'],
            body_html=message_data['body_html'],
            raw_payload=message_data['raw_payload'],
            first_history_id=history_id,
            history_ids=[history_id] if history_id else None,
            label_ids=message_data.get('label_ids'),
        )

        # On conflict, update mutable fields; first_history_id is preserved
        update_set = {
            'raw_payload': stmt.excluded.raw_payload,
            'label_ids': stmt.excluded.label_ids,
        }
        if history_id is not None:
            empty_int_array = literal_column("ARRAY[]::INTEGER[]")
            update_set['history_ids'] = func.array_cat(
                func.coalesce(EmailMessage.history_ids, empty_int_array),
                func.coalesce(stmt.excluded.history_ids, empty_int_array),
            )

        stmt = stmt.on_conflict_do_update(
            index_elements=['message_id'],
            set_=update_set,
        )

        await session.execute(stmt)
        await session.commit()

        # Expire cached ORM objects so the fetch reads the upserted row
        session.expire_all()
        email_msg = await get_email_by_message_id(session, message_id)
        logfire.info(f"Successfully saved email message {message_id}")
        return email_msg, was_inserted

    except Exception as e:
        logfire.exception(f"Failed to save email message: {str(e)}")
        await session.rollback()
        return None, False


async def get_email_by_message_id(
    session: AsyncSession,
    message_id: str
) -> EmailMessage | None:
    """
    Retrieve an email message by its Gmail message ID.
    
    Args:
        session: SQLAlchemy async session
        message_id: Gmail message ID
        
    Returns:
        EmailMessage instance if found, None otherwise
    """
    try:
        logfire.info(f"Fetching email message with ID: {message_id}")
        result = await session.execute(
            select(EmailMessage).where(EmailMessage.message_id == message_id)
        )
        email_msg = result.scalar_one_or_none()
        
        if email_msg:
            logfire.info(f"Found email message {message_id}")
        else:
            logfire.info(f"No email message found with ID {message_id}")
            
        return email_msg
        
    except Exception as e:
        logfire.exception(f"Error fetching email message {message_id}: {str(e)}")
        return None

async def get_emails_by_thread_id(
    session: AsyncSession,
    thread_id: str
) -> list[EmailMessage]:
    """
    Retrieve all email messages in a thread.
    
    Args:
        session: SQLAlchemy async session
        thread_id: Gmail thread ID
        
    Returns:
        List of EmailMessage instances in the thread
    """
    try:
        logfire.info(f"Fetching email messages for thread: {thread_id}")
        result = await session.execute(
            select(EmailMessage)
            .where(EmailMessage.thread_id == thread_id)
            .order_by(EmailMessage.received_date)
        )
        messages = result.scalars().all()
        
        logfire.info(f"Found {len(messages)} messages in thread {thread_id}")
        return list(messages)
        
    except Exception as e:
        logfire.exception(f"Error fetching thread {thread_id}: {str(e)}")
        return [] 