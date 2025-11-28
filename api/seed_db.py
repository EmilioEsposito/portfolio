#!/usr/bin/env python3
"""
Idempotent database seed script for local development.

This script ensures required seed data exists in the local database.
It can be expanded to add more seed data as needed.

Usage:
    # Run with default values
    python api/seed_db.py

    # Run interactively (prompts for values)
    python api/seed_db.py --interactive

    # Dry run (show what would be created)
    python api/seed_db.py --dry-run
"""

import asyncio
import argparse
import logging
import sys
from typing import Optional
from dataclasses import dataclass, field

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ContactSeed:
    """Definition for a contact seed."""
    slug: str
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    # Fields that can be overridden interactively
    interactive_fields: list = field(default_factory=list)


# Define seed data here
CONTACT_SEEDS: list[ContactSeed] = [
    ContactSeed(
        slug="sernia",
        first_name="Sernia",
        last_name="Capital",
        notes="Main company contact for Sernia Capital LLC",
        company="Sernia Capital LLC",
        interactive_fields=["phone_number", "email"],  # Can be overridden interactively
    ),
    # Add more seeds here as needed:
    # ContactSeed(
    #     slug="test-tenant",
    #     first_name="Test",
    #     last_name="Tenant",
    #     email="tenant@example.com",
    #     phone_number="+15551234567",
    #     interactive_fields=["phone_number", "email"],
    # ),
]


def prompt_for_value(field_name: str, current_value: Optional[str], seed_name: str) -> str:
    """Prompt user for a value, showing current default."""
    if current_value:
        user_input = input(f"  {field_name} for '{seed_name}' [{current_value}]: ").strip()
        return user_input if user_input else current_value
    else:
        return input(f"  {field_name} for '{seed_name}': ").strip()


async def seed_contacts(interactive: bool = False, dry_run: bool = False) -> None:
    """Seed contacts into the database."""
    # Import here to avoid circular imports and ensure env is loaded
    from api.src.database.database import AsyncSessionFactory
    from api.src.contact.models import Contact
    from api.src.contact.service import ContactCreate, create_contact
    from sqlalchemy.future import select
    async with AsyncSessionFactory() as session:
        for seed in CONTACT_SEEDS:
            # Check if contact already exists by slug
            query = select(Contact).where(Contact.slug == seed.slug)
            result = await session.execute(query)
            existing = result.scalars().first()

            if existing:
                logger.info(f"✓ Contact '{seed.slug}' already exists (id: {existing.id})")
                continue

            # Handle interactive mode (auto-prompt if interactive_fields is defined)
            phone_number = seed.phone_number
            email = seed.email

            # Force interactive mode if this seed has interactive_fields defined
            should_prompt = interactive or len(seed.interactive_fields) > 0
            logger.info(f"should_prompt: {should_prompt}")

            if should_prompt and len(seed.interactive_fields) > 0:
                logger.info(f"Interactive mode for '{seed.slug}':")
                for field_name in seed.interactive_fields:
                    current_value = getattr(seed, field_name, None)
                    new_value = prompt_for_value(field_name, current_value, seed.slug)
                    if field_name == "phone_number":
                        phone_number = new_value
                    elif field_name == "email":
                        email = new_value

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would create contact '{seed.slug}': "
                    f"{seed.first_name} {seed.last_name}, "
                    f"email={email}, phone={phone_number}"
                )
                continue

            # Create the contact
            try:
                contact_data = ContactCreate(
                    slug=seed.slug,
                    first_name=seed.first_name,
                    last_name=seed.last_name,
                    email=email,
                    phone_number=phone_number,
                    notes=seed.notes,
                )
                created = await create_contact(session, contact_data)
                logger.info(f"✓ Created contact '{seed.slug}' (id: {created.id})")
            except Exception as e:
                logger.error(f"✗ Failed to create contact '{seed.slug}': {e}")
                raise


async def main(interactive: bool = False, dry_run: bool = False) -> None:
    """Main entry point for seeding the database."""
    logger.info("=" * 50)
    logger.info("Database Seed Script")
    logger.info("=" * 50)

    if dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")
    if interactive:
        logger.info("Running in INTERACTIVE mode - will prompt for values")

    logger.info("")
    logger.info("Seeding contacts...")
    await seed_contacts(interactive=interactive, dry_run=dry_run)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Seed complete!")
    logger.info("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Idempotent database seed script for local development"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Prompt for values that can be overridden"
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Show what would be created without making changes"
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(interactive=args.interactive, dry_run=args.dry_run))
    except KeyboardInterrupt:
        logger.info("\nSeed cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Seed failed: {e}")
        sys.exit(1)
