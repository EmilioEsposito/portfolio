from sqlalchemy import Column, Integer, String, DateTime, func, Index
from sqlalchemy.dialects.postgresql import TEXT # Use TEXT for potentially long tokens
from apps.api.src.database.database import Base # Import Base from the correct location

class PushToken(Base):
    __tablename__ = 'push_tokens'

    id = Column(Integer, primary_key=True)
    # Store email associated with the token for easy lookup
    email = Column(String, nullable=False, index=True) # Re-add index for email
    token = Column(TEXT, nullable=False, unique=True) # Expo tokens can be long
    created_at = Column(DateTime, default=func.now(), server_default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), server_default=func.now())

    # Index for faster lookup by email (re-enabled)
    # __table_args__ = (Index('ix_push_tokens_email', 'email'), )

    def __repr__(self):
        return f'<PushToken(email={self.email}, token={self.token[:15]}...)>'
