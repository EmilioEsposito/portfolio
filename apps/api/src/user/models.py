import uuid
from sqlalchemy import Column, String, DateTime, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func # For server-side default timestamps

# Import Base from the correct location
from apps.api.src.database.database import Base

class User(Base):
    __tablename__ = "users"

    # Internal UUID primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Clerk specific identifiers
    clerk_user_id = Column(String, nullable=False)
    environment = Column(String, nullable=False) # e.g., "development" or "production"

    # Basic user info from Clerk (allow nulls initially)
    email = Column(String, nullable=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    image_url = Column(String, nullable=True) # Clerk's profile image url

    # Timestamps from Clerk event data (store as naive UTC, handle timezone on interpretation if needed)
    # Clerk sends timestamps in milliseconds since epoch (Unix time)
    # Store them as DateTime for easier querying. We might need conversion logic in service.
    clerk_created_at = Column(DateTime(timezone=False), nullable=False)
    clerk_updated_at = Column(DateTime(timezone=False), nullable=False)

    # Metadata (optional, store as JSON)
    public_metadata = Column(JSON, nullable=True)
    private_metadata = Column(JSON, nullable=True) # Be cautious storing private metadata

    # Store the raw payload from the webhook event
    raw_payload = Column(JSON, nullable=True)

    # Timestamps managed by your database (UTC)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Ensure combination of clerk_user_id and environment is unique
    __table_args__ = (
        Index(
            "uq_user_clerk_id_env",  # New, descriptive index name
            "clerk_user_id",         # First column in the composite index
            "environment",           # Second column in the composite index
            unique=True
        ),
    )

    def __repr__(self):
        return f"<User(id={self.id}, clerk_user_id='{self.clerk_user_id}', email='{self.email}')>"
