import uuid
from typing import List, Optional, AsyncGenerator
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import HTTPException, status
import logfire


from api.src.contact.models import Contact
from api.src.user.models import User # For type hinting if needed for user_id validation
from pydantic import BaseModel, EmailStr, Field, field_validator
import pytest
from api.src.database.database import AsyncSessionFactory
import asyncio
from datetime import datetime
import types
import re


# Pydantic Schemas for Contact
class ContactBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=50, pattern="^[a-zA-Z0-9_-]+$", description="Unique, URL-friendly identifier for the contact. Will be stored in lowercase.")
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = Field(default=None, description="Phone number, will be normalized to +1XXXXXXXXXX format for US numbers.")
    notes: Optional[str] = None
    openphone_contact_id: Optional[str] = None
    user_id: Optional[uuid.UUID] = None

    @field_validator('slug', mode='before')
    @classmethod
    def lowercase_slug(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v # Should not happen if Pydantic validates type first, but good practice

    @field_validator('phone_number', mode='before')
    @classmethod
    def normalize_phone_number(cls, v):
        if v is None or not isinstance(v, str) or v.strip() == "":
            return None
        
        digits = re.sub(r'\D', '', v)
        
        if len(digits) < 10:
            raise ValueError('Phone number must contain at least 10 digits for US E.164 format.')
        
        normalized_number = "+1" + digits[-10:]
        return normalized_number

    class Config:
        from_attributes = True

class ContactCreate(ContactBase):
    pass

class ContactUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = None
    user_id: Optional[uuid.UUID] = None # Allow updating user_id, though slug remains immutable for a created contact

class ContactResponse(ContactBase):
    id: uuid.UUID


async def create_contact(db: AsyncSession, contact_create: ContactCreate) -> Contact:
    # Check if slug already exists
    logfire.info(f"Attempting to create contact with slug: {contact_create.slug}, email: {contact_create.email}")
    if contact_create.slug:
        logfire.debug(f"Checking for existing contact with slug: {contact_create.slug}")
        existing_contact_by_slug = await get_contact_by_slug(contact_create.slug)
        if existing_contact_by_slug:
            logfire.warn(f"Contact with slug '{contact_create.slug}' already exists.")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Contact with slug '{contact_create.slug}' already exists."
            )

    final_user_id = contact_create.user_id

    # If email is provided and user_id is not, try to find user by email
    if contact_create.email and contact_create.user_id is None:
        user_by_email_query = select(User).where(User.email == contact_create.email)
        user_result = await db.execute(user_by_email_query)
        found_user = user_result.scalars().first()
        if found_user:
            logfire.info(f"Found user ID {found_user.id} by email {contact_create.email} for contact creation.")
            final_user_id = found_user.id
        else:
            logfire.info(f"No user found with email {contact_create.email} during contact creation.")

    # Validate user_id if it's set (either provided or found by email)
    if final_user_id:
        logfire.debug(f"Validating user ID {final_user_id} for new contact.")
        user_exists_query = select(User).where(User.id == final_user_id)
        user_result = await db.execute(user_exists_query)
        if not user_result.scalars().first():
            logfire.warn(f"User with ID '{final_user_id}' not found during contact creation.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id '{final_user_id}' not found."
            )
    
    contact_data = contact_create.model_dump()
    contact_data['user_id'] = final_user_id # Ensure final_user_id is used

    db_contact = Contact(**contact_data) # For Pydantic V1
    # For Pydantic V2, use: db_contact = Contact(**contact_data)
    db.add(db_contact)
    await db.commit()
    await db.refresh(db_contact)
    logfire.info(f"Successfully created contact with ID: {db_contact.id}, slug: {db_contact.slug}")
    return db_contact

async def get_contact_by_id(db: AsyncSession, contact_id: uuid.UUID) -> Optional[Contact]:
    logfire.info(f"Attempting to get contact by ID: {contact_id}")
    query = select(Contact).where(Contact.id == contact_id)
    result = await db.execute(query)
    contact = result.scalars().first()
    if contact:
        logfire.info(f"Found contact with ID: {contact_id}")
    else:
        logfire.warn(f"Contact with ID: {contact_id} not found.")
    return contact

async def get_contact_by_slug(slug: str) -> Optional[Contact]:
    """
    Get a contact by slug.
    """
    logfire.info(f"Attempting to get contact by slug: {slug}")
    async with AsyncSessionFactory() as session:
        lowercase_slug = slug.lower() # Convert incoming slug to lowercase for query
        logfire.debug(f"Querying for contact with lowercase slug: {lowercase_slug}")
        query = select(Contact).where(Contact.slug == lowercase_slug)
        result = await session.execute(query)
        contact = result.scalars().first()
        if contact:
            logfire.info(f"Found contact with slug: {slug} (ID: {contact.id})")
        else:
            logfire.error(f"Contact with slug: {slug} not found.")
        return contact

async def get_all_contacts(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Contact]:
    logfire.info(f"Attempting to get all contacts with skip: {skip}, limit: {limit}")
    query = select(Contact).offset(skip).limit(limit)
    result = await db.execute(query)
    contacts = result.scalars().all()
    logfire.info(f"Retrieved {len(contacts)} contacts.")
    return contacts

async def update_contact(db: AsyncSession, contact_id: uuid.UUID, contact_update: ContactUpdate) -> Optional[Contact]:
    logfire.info(f"Attempting to update contact with ID: {contact_id}. Update data: {contact_update.model_dump(exclude_unset=True)}")
    db_contact = await get_contact_by_id(db, contact_id)
    if not db_contact:
        return None

    update_data = contact_update.model_dump(exclude_unset=True)
    
    final_user_id_for_update = db_contact.user_id # Start with current user_id

    # Logic to determine user_id based on email, if email is in update_data
    if "email" in update_data:
        if update_data["email"] is not None:
            user_by_email_query = select(User).where(User.email == update_data["email"])
            user_result = await db.execute(user_by_email_query)
            found_user = user_result.scalars().first()
            if found_user:
                logfire.info(f"Found user ID {found_user.id} by email {update_data['email']} for contact update.")
                final_user_id_for_update = found_user.id
            else:
                logfire.info(f"No user found with email {update_data['email']} during contact update. Unlinking user.")
                # Email provided, but no user found, so unlink
                final_user_id_for_update = None
        else:
            logfire.info(f"Email explicitly set to None during contact update. Unlinking user.")
            # Email is explicitly set to None, so unlink
            final_user_id_for_update = None

    # If user_id is explicitly provided in the update, it takes precedence
    if "user_id" in update_data:
        final_user_id_for_update = update_data["user_id"]
        logfire.info(f"User ID explicitly provided in update: {final_user_id_for_update}")

    # Validate final_user_id_for_update if it's not None and it's different from original or was explicitly provided
    if final_user_id_for_update is not None and (final_user_id_for_update != db_contact.user_id or "user_id" in update_data):
        logfire.debug(f"Validating final user ID {final_user_id_for_update} for contact update.")
        user_exists_query = select(User).where(User.id == final_user_id_for_update)
        user_result = await db.execute(user_exists_query)
        if not user_result.scalars().first():
            logfire.warn(f"User with ID '{final_user_id_for_update}' not found during contact update.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id '{final_user_id_for_update}' not found when trying to update contact."
            )

    # Apply all updates from update_data dictionary
    for key, value in update_data.items():
        if key == "user_id": # user_id is handled separately below
            continue
        setattr(db_contact, key, value)
    
    db_contact.user_id = final_user_id_for_update # Set the determined user_id

    await db.commit()
    await db.refresh(db_contact)
    logfire.info(f"Successfully updated contact with ID: {contact_id}. New user_id: {db_contact.user_id}")
    return db_contact

async def delete_contact(db: AsyncSession, contact_id: uuid.UUID) -> Optional[Contact]:
    logfire.info(f"Attempting to delete contact with ID: {contact_id}")
    db_contact = await get_contact_by_id(db, contact_id)
    if not db_contact:
        return None
    await db.delete(db_contact)
    await db.commit()
    logfire.info(f"Successfully deleted contact with ID: {contact_id}")
    return db_contact


# Test Section (Simplified)
# IMPORTANT: These tests assume that the database schema (tables) 
# has already been created (e.g., via Alembic migrations) in the test database.

@pytest.mark.asyncio
async def test_basic_create_contact(): # Removed db_session parameter
    async with AsyncSessionFactory() as session: # Use AsyncSessionFactory directly
        print(f"Type of session in test_basic_create_contact: {type(session)}")
        # ... (rest of the existing test logic, using 'session')
        existing_contact_query = select(Contact).where(Contact.slug == "test-contact")
        existing_contact_result = await session.execute(existing_contact_query)
        contact_to_delete = existing_contact_result.scalars().first()
        if contact_to_delete:
            await session.delete(contact_to_delete)
            await session.commit()

        contact_create_data = ContactCreate(slug="test-contact", first_name="Test", last_name="Contact", email="basic@example.com", phone_number="0 00-31 23 45(67) 890")
        created_contact = await create_contact(session, contact_create_data)
        await session.refresh(created_contact)

        assert created_contact is not None
        assert created_contact.slug == "test-contact"
        assert created_contact.first_name == "Test"
        assert created_contact.last_name == "Contact"
        assert created_contact.email == "basic@example.com"
        
        user_check_query = select(User).where(User.email == "basic@example.com")
        user_check_result = await session.execute(user_check_query)
        assert user_check_result.scalars().first() is None
        assert created_contact.user_id is None

        await session.commit()
        

@pytest.mark.asyncio
async def test_basic_update_contact():
    async with AsyncSessionFactory() as session:
        # Create a test contact
        existing_contact_query = select(Contact).where(Contact.slug == "test-contact")
        existing_contact_result = await session.execute(existing_contact_query)
        existing_contact = existing_contact_result.scalars().first()
        if not existing_contact:
            contact_create_data = ContactCreate(slug="test-contact", first_name="Test", last_name="Contact", email="basic@example.com")
            created_contact = await create_contact(session, contact_create_data)
            await session.refresh(created_contact)
            existing_contact = created_contact

        # Update the contact
        update_data = ContactUpdate(first_name="Updated", last_name="Contact", email="updated@example.com")
        updated_contact = await update_contact(session, existing_contact.id, update_data)
        await session.refresh(updated_contact)

        assert updated_contact is not None
        assert updated_contact.first_name == "Updated"
        assert updated_contact.last_name == "Contact"
        assert updated_contact.email == "updated@example.com"
        assert updated_contact.user_id == existing_contact.user_id


@pytest.mark.asyncio
async def test_get_contact_by_slug():
    contact = await get_contact_by_slug("test-contact")
    assert contact is not None
    assert hasattr(contact, "id")
    assert hasattr(contact, "slug")
    assert hasattr(contact, "first_name")
    assert hasattr(contact, "last_name")
    assert hasattr(contact, "email")
    assert hasattr(contact, "phone_number")
    assert hasattr(contact, "notes")
    assert hasattr(contact, "user_id")
    assert contact.slug == "test-contact"
