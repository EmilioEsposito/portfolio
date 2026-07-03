import re
import uuid

import logfire
from fastapi import HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.src.contact.models import Contact
from api.src.database.database import AsyncSessionFactory
from api.src.user.models import User  # For type hinting if needed for user_id validation


# Pydantic Schemas for Contact
class ContactBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        pattern="^[a-zA-Z0-9_-]+$",
        description="Unique, URL-friendly identifier for the contact. Will be stored in lowercase.",
    )
    email: EmailStr | None = None
    phone_number: str | None = Field(
        default=None,
        description="Phone number, will be normalized to +1XXXXXXXXXX format for US numbers.",
    )
    notes: str | None = None
    openphone_contact_id: str | None = None
    user_id: uuid.UUID | None = None

    @field_validator("slug", mode="before")
    @classmethod
    def lowercase_slug(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v  # Should not happen if Pydantic validates type first, but good practice

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone_number(cls, v):
        if v is None or not isinstance(v, str) or v.strip() == "":
            return None

        digits = re.sub(r"\D", "", v)

        if len(digits) < 10:
            raise ValueError("Phone number must contain at least 10 digits for US E.164 format.")

        normalized_number = "+1" + digits[-10:]
        return normalized_number

    class Config:
        from_attributes = True


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone_number: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    user_id: uuid.UUID | None = (
        None  # Allow updating user_id, though slug remains immutable for a created contact
    )


class ContactResponse(ContactBase):
    id: uuid.UUID


async def create_contact(db: AsyncSession, contact_create: ContactCreate) -> Contact:
    # Check if slug already exists
    logfire.info(
        f"Attempting to create contact with slug: {contact_create.slug}, email: {contact_create.email}"
    )
    if contact_create.slug:
        logfire.debug(f"Checking for existing contact with slug: {contact_create.slug}")
        existing_contact_by_slug = await get_contact_by_slug(contact_create.slug)
        if existing_contact_by_slug:
            logfire.warn(f"Contact with slug '{contact_create.slug}' already exists.")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Contact with slug '{contact_create.slug}' already exists.",
            )

    final_user_id = contact_create.user_id

    # If email is provided and user_id is not, try to find user by email
    if contact_create.email and contact_create.user_id is None:
        user_by_email_query = select(User).where(User.email == contact_create.email)
        user_result = await db.execute(user_by_email_query)
        found_user = user_result.scalars().first()
        if found_user:
            logfire.info(
                f"Found user ID {found_user.id} by email {contact_create.email} for contact creation."
            )
            final_user_id = found_user.id
        else:
            logfire.info(
                f"No user found with email {contact_create.email} during contact creation."
            )

    # Validate user_id if it's set (either provided or found by email)
    if final_user_id:
        logfire.debug(f"Validating user ID {final_user_id} for new contact.")
        user_exists_query = select(User).where(User.id == final_user_id)
        user_result = await db.execute(user_exists_query)
        if not user_result.scalars().first():
            logfire.warn(f"User with ID '{final_user_id}' not found during contact creation.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id '{final_user_id}' not found.",
            )

    contact_data = contact_create.model_dump()
    contact_data["user_id"] = final_user_id  # Ensure final_user_id is used

    db_contact = Contact(**contact_data)  # For Pydantic V1
    # For Pydantic V2, use: db_contact = Contact(**contact_data)
    db.add(db_contact)
    await db.commit()
    await db.refresh(db_contact)
    logfire.info(f"Successfully created contact with ID: {db_contact.id}, slug: {db_contact.slug}")
    return db_contact


async def get_contact_by_id(db: AsyncSession, contact_id: uuid.UUID) -> Contact | None:
    logfire.info(f"Attempting to get contact by ID: {contact_id}")
    query = select(Contact).where(Contact.id == contact_id)
    result = await db.execute(query)
    contact = result.scalars().first()
    if contact:
        logfire.info(f"Found contact with ID: {contact_id}")
    else:
        logfire.warn(f"Contact with ID: {contact_id} not found.")
    return contact


async def get_contact_by_slug(slug: str) -> Contact | None:
    """
    Get a contact by slug.
    """
    logfire.info(f"Attempting to get contact by slug: {slug}")
    async with AsyncSessionFactory() as session:
        lowercase_slug = slug.lower()  # Convert incoming slug to lowercase for query
        logfire.debug(f"Querying for contact with lowercase slug: {lowercase_slug}")
        query = select(Contact).where(Contact.slug == lowercase_slug)
        result = await session.execute(query)
        contact = result.scalars().first()
        if contact:
            logfire.info(f"Found contact with slug: {slug} (ID: {contact.id})")
        else:
            logfire.error(f"Contact with slug: {slug} not found.")
        return contact


async def get_clerk_user_id_by_slug(slug: str) -> str | None:
    """Look up a contact's clerk_user_id by slug (contacts → users join)."""
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User.clerk_user_id)
            .join(Contact, Contact.user_id == User.id)
            .where(Contact.slug == slug.lower())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_all_contacts(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Contact]:
    logfire.info(f"Attempting to get all contacts with skip: {skip}, limit: {limit}")
    query = select(Contact).offset(skip).limit(limit)
    result = await db.execute(query)
    contacts = result.scalars().all()
    logfire.info(f"Retrieved {len(contacts)} contacts.")
    return contacts


async def update_contact(
    db: AsyncSession, contact_id: uuid.UUID, contact_update: ContactUpdate
) -> Contact | None:
    logfire.info(
        f"Attempting to update contact with ID: {contact_id}. Update data: {contact_update.model_dump(exclude_unset=True)}"
    )
    db_contact = await get_contact_by_id(db, contact_id)
    if not db_contact:
        return None

    update_data = contact_update.model_dump(exclude_unset=True)

    final_user_id_for_update = db_contact.user_id  # Start with current user_id

    # Logic to determine user_id based on email, if email is in update_data
    if "email" in update_data:
        if update_data["email"] is not None:
            user_by_email_query = select(User).where(User.email == update_data["email"])
            user_result = await db.execute(user_by_email_query)
            found_user = user_result.scalars().first()
            if found_user:
                logfire.info(
                    f"Found user ID {found_user.id} by email {update_data['email']} for contact update."
                )
                final_user_id_for_update = found_user.id
            else:
                logfire.info(
                    f"No user found with email {update_data['email']} during contact update. Unlinking user."
                )
                # Email provided, but no user found, so unlink
                final_user_id_for_update = None
        else:
            logfire.info("Email explicitly set to None during contact update. Unlinking user.")
            # Email is explicitly set to None, so unlink
            final_user_id_for_update = None

    # If user_id is explicitly provided in the update, it takes precedence
    if "user_id" in update_data:
        final_user_id_for_update = update_data["user_id"]
        logfire.info(f"User ID explicitly provided in update: {final_user_id_for_update}")

    # Validate final_user_id_for_update if it's not None and it's different from original or was explicitly provided
    if final_user_id_for_update is not None and (
        final_user_id_for_update != db_contact.user_id or "user_id" in update_data
    ):
        logfire.debug(f"Validating final user ID {final_user_id_for_update} for contact update.")
        user_exists_query = select(User).where(User.id == final_user_id_for_update)
        user_result = await db.execute(user_exists_query)
        if not user_result.scalars().first():
            logfire.warn(
                f"User with ID '{final_user_id_for_update}' not found during contact update."
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id '{final_user_id_for_update}' not found when trying to update contact.",
            )

    # Apply all updates from update_data dictionary
    for key, value in update_data.items():
        if key == "user_id":  # user_id is handled separately below
            continue
        setattr(db_contact, key, value)

    db_contact.user_id = final_user_id_for_update  # Set the determined user_id

    await db.commit()
    await db.refresh(db_contact)
    logfire.info(
        f"Successfully updated contact with ID: {contact_id}. New user_id: {db_contact.user_id}"
    )
    return db_contact


async def delete_contact(db: AsyncSession, contact_id: uuid.UUID) -> Contact | None:
    logfire.info(f"Attempting to delete contact with ID: {contact_id}")
    db_contact = await get_contact_by_id(db, contact_id)
    if not db_contact:
        return None
    await db.delete(db_contact)
    await db.commit()
    logfire.info(f"Successfully deleted contact with ID: {contact_id}")
    return db_contact
