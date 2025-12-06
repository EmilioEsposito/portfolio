#!/usr/bin/env python3
"""
Idempotent database seed script for local development.

This script ensures required seed data exists in the local database.
It can be expanded to add more seed data as needed.

Usage:
    # Run with default values (uses environment variables)
    python api/seed_db.py

    # Dry run (show what would be created)
    python api/seed_db.py --dry-run

Environment variables for seed data:
    EMILIO_EMAIL - Email for Emilio contact
    EMILIO_PHONE - Phone for Emilio contact
    SERNIA_EMAIL - Email for Sernia contact
    SERNIA_PHONE - Phone for Sernia contact
"""

import asyncio
import argparse
import os
import sys
from typing import Optional
from dataclasses import dataclass


def log_info(msg: str) -> None:
    """Simple logging for seed script."""
    print(f"[INFO] {msg}")


def log_error(msg: str) -> None:
    """Simple error logging for seed script."""
    print(f"[ERROR] {msg}", file=sys.stderr)


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


def get_contact_seeds() -> list[ContactSeed]:
    """Build contact seeds from environment variables."""
    return [
        ContactSeed(
            slug="sernia",
            first_name="Sernia",
            last_name="Capital",
            email=os.environ.get("SERNIA_EMAIL"),
            phone_number=os.environ.get("SERNIA_PHONE"),
            notes="Main company contact for Sernia Capital LLC",
            company="Sernia Capital LLC",
        ),
        ContactSeed(
            slug="emilio",
            first_name="Emilio",
            last_name="Esposito",
            email=os.environ.get("EMILIO_EMAIL"),
            phone_number=os.environ.get("EMILIO_PHONE"),
            notes="Main contact for Emilio Esposito",
            company="Sernia Capital LLC",
        ),
        # Add more seeds here as needed:
        # ContactSeed(
        #     slug="test-tenant",
        #     first_name="Test",
        #     last_name="Tenant",
        #     email=os.environ.get("TEST_TENANT_EMAIL"),
        #     phone_number=os.environ.get("TEST_TENANT_PHONE"),
        # ),
    ]


async def seed_contacts(dry_run: bool = False) -> None:
    """Seed contacts into the database."""
    # Import here to avoid circular imports and ensure env is loaded
    from apps.api.src.database.database import AsyncSessionFactory
    from apps.api.src.contact.models import Contact
    from apps.api.src.contact.service import ContactCreate, create_contact
    from sqlalchemy.future import select

    seeds = get_contact_seeds()

    async with AsyncSessionFactory() as session:
        for seed in seeds:
            # Check if contact already exists by slug
            query = select(Contact).where(Contact.slug == seed.slug)
            result = await session.execute(query)
            existing = result.scalars().first()

            if existing:
                log_info(f"✓ Contact '{seed.slug}' already exists (id: {existing.id})")
                continue

            if dry_run:
                log_info(
                    f"[DRY RUN] Would create contact '{seed.slug}': "
                    f"{seed.first_name} {seed.last_name}, "
                    f"email={seed.email}, phone={seed.phone_number}"
                )
                continue

            # Create the contact
            try:
                contact_data = ContactCreate(
                    slug=seed.slug,
                    first_name=seed.first_name,
                    last_name=seed.last_name,
                    email=seed.email,
                    phone_number=seed.phone_number,
                    notes=seed.notes,
                )
                created = await create_contact(session, contact_data)
                log_info(f"✓ Created contact '{seed.slug}' (id: {created.id})")
            except Exception as e:
                log_error(f"✗ Failed to create contact '{seed.slug}': {e}")
                raise


async def main(dry_run: bool = False) -> None:
    """Main entry point for seeding the database."""
    log_info("=" * 50)
    log_info("Database Seed Script")
    log_info("=" * 50)

    if dry_run:
        log_info("Running in DRY RUN mode - no changes will be made")

    log_info("")
    log_info("Seeding contacts...")
    await seed_contacts(dry_run=dry_run)

    log_info("")
    log_info("=" * 50)
    log_info("Seed complete!")
    log_info("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Idempotent database seed script for local development"
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Show what would be created without making changes"
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(dry_run=args.dry_run))
    except KeyboardInterrupt:
        log_info("\nSeed cancelled by user")
        sys.exit(1)
    except Exception as e:
        log_error(f"Seed failed: {e}")
        sys.exit(1)
