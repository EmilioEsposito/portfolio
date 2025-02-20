from datetime import datetime
from sqlalchemy import String, DateTime, func, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from api_src.database.database import Base
from typing import Optional
import pytz



class OAuthCredential(Base):
    """SQLAlchemy model for storing OAuth credentials from any provider"""
    __tablename__ = "oauth_credentials"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)  # Clerk user ID
    provider: Mapped[str] = mapped_column(String, index=True)  # e.g. 'oauth_google', 'oauth_github'
    provider_user_id: Mapped[str] = mapped_column(String)  # Provider's user ID
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    token_type: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False)) # Google expects naive datetime
    scopes: Mapped[list] = mapped_column(JSON)  # Store granted scopes
    raw_response: Mapped[dict] = mapped_column(JSON)  # Store complete provider response
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # Optional label from provider
    
    # Metadata timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
    )

    def is_expired(self) -> bool:
        """Check if the token is expired"""
        utc_now_naive = datetime.now(pytz.UTC).replace(tzinfo=None)
        return utc_now_naive >= self.expires_at
    
    __table_args__ = (
        UniqueConstraint('user_id', 'provider', name='uix_user_provider'),
    ) 