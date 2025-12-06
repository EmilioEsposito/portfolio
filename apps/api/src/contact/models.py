import uuid
from sqlalchemy import Column, String, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from apps.api.src.database.database import Base # Assuming your Base is here
from sqlalchemy.dialects.postgresql import JSONB

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String, unique=True, index=True, nullable=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=True, index=True)
    phone_number = Column(String, nullable=True)
    
    # Optional notes or description for the contact
    notes = Column(Text, nullable=True) 

    company = Column(String, nullable=True)
    role = Column(String, nullable=True)

    openphone_contact_id = Column(String, nullable=True)
    openphone_json = Column(JSONB, nullable=True)

    # Foreign key to User table
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    user = relationship("User") # Establishes the relationship to the User model

    # Add extend_existing=True to handle cases where the model might be registered multiple times during test collection
    __table_args__ = ({'extend_existing': True})

    # It's good practice to have a __repr__ method for debugging
    def __repr__(self):
        return f"<Contact(id={self.id}, slug='{self.slug}', name='{self.name}')>"

# It might be useful to have an index on user_id if you plan to query contacts by user frequently.
# __table_args__ = (
# Index('idx_contact_user_id', 'user_id'),
# )
# For now, I'll comment this out as it's an optimization. We can add it later if needed.
