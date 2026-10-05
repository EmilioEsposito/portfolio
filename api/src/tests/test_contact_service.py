"""
Integration tests for the contact service (api.src.contact.service).

These tests hit the database via AsyncSessionFactory, so they need a
reachable Postgres with the schema already migrated:

    pytest api/src/tests/test_contact_service.py -v -s
"""

from unittest.mock import patch

import logfire
import pytest
from sqlalchemy.future import select

from api.src.contact.models import Contact
from api.src.contact.service import (
    ContactCreate,
    ContactUpdate,
    create_contact,
    get_contact_by_slug,
    update_contact,
)
from api.src.database.database import AsyncSessionFactory
from api.src.user.models import User

# Test Section (Simplified)
# IMPORTANT: These tests assume that the database schema (tables)
# has already been created (e.g., via Alembic migrations) in the test database.


@pytest.mark.asyncio
async def test_basic_create_contact():  # Removed db_session parameter
    async with AsyncSessionFactory() as session:  # Use AsyncSessionFactory directly
        print(f"Type of session in test_basic_create_contact: {type(session)}")
        # ... (rest of the existing test logic, using 'session')
        existing_contact_query = select(Contact).where(Contact.slug == "test-contact")
        existing_contact_result = await session.execute(existing_contact_query)
        contact_to_delete = existing_contact_result.scalars().first()
        if contact_to_delete:
            await session.delete(contact_to_delete)
            await session.commit()

        contact_create_data = ContactCreate(
            slug="test-contact",
            first_name="Test",
            last_name="Contact",
            email="basic@example.com",
            phone_number="0 00-31 23 45(67) 890",
        )
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
            contact_create_data = ContactCreate(
                slug="test-contact",
                first_name="Test",
                last_name="Contact",
                email="basic@example.com",
            )
            created_contact = await create_contact(session, contact_create_data)
            await session.refresh(created_contact)
            existing_contact = created_contact

        # Update the contact
        update_data = ContactUpdate(
            first_name="Updated", last_name="Contact", email="updated@example.com"
        )
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


@pytest.mark.asyncio
async def test_get_contact_by_slug_miss_is_not_error_level():
    """A lookup miss must not be logged at error level.

    Regression coverage for the "Error-level records (non-local)" alert firing on
    every successful contact creation: create_contact() calls get_contact_by_slug()
    to confirm the slug is free, so the not-found branch is the happy path. It used
    to call logfire.error(), which made each creation (and each 404 from
    GET /api/contact/slug/{slug}) trip the alert.
    """
    with patch.object(logfire, "error") as mock_error:
        contact = await get_contact_by_slug("definitely-not-a-real-slug")

    assert contact is None
    assert not mock_error.called, (
        f"a missing slug was logged at error level: {mock_error.call_args_list}"
    )
