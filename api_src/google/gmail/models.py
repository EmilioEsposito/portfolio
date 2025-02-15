"""
SQLAlchemy models for Gmail-related database tables.
"""

from sqlalchemy import Column, String, DateTime, JSON, Integer
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class EmailMessage(Base):
    """Model for storing Gmail messages."""
    __tablename__ = 'email_messages'

    id = Column(Integer, primary_key=True)
    message_id = Column(String, unique=True, nullable=False)
    thread_id = Column(String)
    subject = Column(String)
    from_address = Column(String)
    to_address = Column(String)
    received_date = Column(DateTime)
    body_text = Column(String)
    body_html = Column(String)
    raw_payload = Column(JSON)

    def __repr__(self):
        return f"<EmailMessage(id={self.id}, subject='{self.subject}')>" 