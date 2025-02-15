from datetime import datetime
from sqlalchemy import String, DateTime, func, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from api_src.database.database import Base



class GoogleOAuthToken(Base):
    """SQLAlchemy model for storing Google OAuth tokens"""
    __tablename__ = "google_oauth_tokens"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String, unique=True, index=True)  # Email or unique identifier
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)
    token_type: Mapped[str] = mapped_column(String)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list] = mapped_column(JSON)  # Store granted scopes
    
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
        return datetime.now(self.expiry.tzinfo) >= self.expiry 
    
    