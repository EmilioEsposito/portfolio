"""
Database operations for Gmail-related functionality.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import pytz
from typing import Dict, Any, Optional
from api_src.google.gmail.models import EmailMessage
import pytest
import logging
from typing import List, AsyncGenerator
from contextlib import asynccontextmanager
from api_src.database.database import AsyncSessionFactory


# Configure logging
logger = logging.getLogger(__name__)

# Test helper for creating database sessions
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

async def save_email_message(
    session: AsyncSession,
    message_data: Dict[str, Any]
) -> Optional[EmailMessage]:
    """
    Save a Gmail message to the database.
    
    Args:
        session: SQLAlchemy async session
        message_data: Processed message data containing all required fields
        
    Returns:
        The saved EmailMessage instance or None if save failed
    """
    try:
        logger.info(f"Saving email message {message_data.get('message_id')} to database")
        
        # Parse the date and ensure it's timezone aware
        date_str = message_data['date']
        try:
            # Parse the date string
            received_date = datetime.fromisoformat(date_str)
            
            # If the datetime is naive, assume UTC
            if received_date.tzinfo is None:
                received_date = pytz.UTC.localize(received_date)
            else:
                # Convert to UTC if it's not already
                received_date = received_date.astimezone(pytz.UTC)
                
            # Remove timezone info before saving to database
            # This is because the column is TIMESTAMP WITHOUT TIME ZONE
            received_date = received_date.replace(tzinfo=None)
            
        except ValueError as e:
            # Fallback to UTC now if date parsing fails
            logger.warning(f"Failed to parse date {date_str}, using current UTC time: {str(e)}")
            received_date = datetime.now(pytz.UTC).replace(tzinfo=None)
        
        # Create new email message instance
        email_msg = EmailMessage(
            message_id=message_data['message_id'],
            thread_id=message_data['thread_id'],
            subject=message_data['subject'],
            from_address=message_data['from_address'],
            to_address=message_data['to_address'],
            received_date=received_date,
            body_text=message_data['body_text'],
            body_html=message_data['body_html'],
            raw_payload=message_data['raw_payload']
        )
        
        # Add to session and commit
        session.add(email_msg)
        await session.commit()
        await session.refresh(email_msg)
        
        logger.info(f"Successfully saved email message {email_msg.message_id}")
        return email_msg
        
    except Exception as e:
        logger.error(f"Failed to save email message: {str(e)}", exc_info=True)
        await session.rollback()
        return None



@pytest.mark.asyncio
async def test_save_email_message():
    """Test saving and retrieving an email message"""
    # Test data with proper datetime
    message_data = {
        'message_id': '1234567890',
        'thread_id': '1234567890',
        'subject': 'Test Email',
        'from_address': 'test@example.com',
        'to_address': 'recipient@example.com',
        'date': datetime.now().isoformat(),  # Current time in ISO format
        'body_text': 'Test body',
        'body_html': '<p>Test body</p>',
        'raw_payload': {'test': 'data'}
    }
    
    saved_msg = None
    async with get_test_session() as session:
        try:
            # Save message
            saved_msg = await save_email_message(session, message_data)
            assert saved_msg is not None
            assert saved_msg.message_id == message_data['message_id']
            
            # Verify we can retrieve it
            retrieved = await get_email_by_message_id(session, message_data['message_id'])
            assert retrieved is not None
            assert retrieved.subject == message_data['subject']
            
        finally:
            # Cleanup: Delete test message
            if saved_msg:
                await session.delete(saved_msg)
                await session.commit()

async def get_email_by_message_id(
    session: AsyncSession,
    message_id: str
) -> Optional[EmailMessage]:
    """
    Retrieve an email message by its Gmail message ID.
    
    Args:
        session: SQLAlchemy async session
        message_id: Gmail message ID
        
    Returns:
        EmailMessage instance if found, None otherwise
    """
    try:
        logger.info(f"Fetching email message with ID: {message_id}")
        result = await session.execute(
            select(EmailMessage).where(EmailMessage.message_id == message_id)
        )
        email_msg = result.scalar_one_or_none()
        
        if email_msg:
            logger.info(f"Found email message {message_id}")
        else:
            logger.info(f"No email message found with ID {message_id}")
            
        return email_msg
        
    except Exception as e:
        logger.error(f"Error fetching email message {message_id}: {str(e)}", exc_info=True)
        return None

async def get_emails_by_thread_id(
    session: AsyncSession,
    thread_id: str
) -> List[EmailMessage]:
    """
    Retrieve all email messages in a thread.
    
    Args:
        session: SQLAlchemy async session
        thread_id: Gmail thread ID
        
    Returns:
        List of EmailMessage instances in the thread
    """
    try:
        logger.info(f"Fetching email messages for thread: {thread_id}")
        result = await session.execute(
            select(EmailMessage)
            .where(EmailMessage.thread_id == thread_id)
            .order_by(EmailMessage.received_date)
        )
        messages = result.scalars().all()
        
        logger.info(f"Found {len(messages)} messages in thread {thread_id}")
        return list(messages)
        
    except Exception as e:
        logger.error(f"Error fetching thread {thread_id}: {str(e)}", exc_info=True)
        return [] 