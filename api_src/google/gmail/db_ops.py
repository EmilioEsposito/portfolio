"""
Database operations for Gmail-related functionality.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Dict, Any, Optional
from email.utils import parsedate_to_datetime

from api_src.google.gmail.models import EmailMessage

async def save_email_message(session: AsyncSession, message_data: Dict[str, Any]) -> Optional[EmailMessage]:
    """
    Save a Gmail message to the database.
    
    Args:
        session: SQLAlchemy async session
        message_data: Processed message data
        
    Returns:
        Saved EmailMessage instance or None if save failed
    """
    try:
        # Convert date string to datetime
        date = message_data['date']
        if isinstance(date, str):
            date = parsedate_to_datetime(date)
        
        # Create new message instance
        message = EmailMessage(
            message_id=message_data['message_id'],
            thread_id=message_data['thread_id'],
            subject=message_data['subject'],
            from_address=message_data['from_address'],
            to_address=message_data['to_address'],
            received_date=date,
            body_text=message_data['body_text'],
            body_html=message_data['body_html'],
            raw_payload=message_data['raw_payload']
        )
        
        # Add and commit
        session.add(message)
        await session.commit()
        await session.refresh(message)
        
        return message
        
    except Exception as e:
        await session.rollback()
        raise

async def get_email_by_message_id(session: AsyncSession, message_id: str) -> Optional[EmailMessage]:
    """
    Retrieve an email message by its Gmail message ID.
    
    Args:
        session: SQLAlchemy async session
        message_id: Gmail message ID
        
    Returns:
        EmailMessage instance if found, None otherwise
    """
    try:
        result = await session.execute(
            select(EmailMessage).where(EmailMessage.message_id == message_id)
        )
        return result.scalar_one_or_none()
        
    except Exception as e:
        raise 