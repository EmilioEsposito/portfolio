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
    from api.src.database.database import AsyncSessionFactory
    from api.src.contact.models import Contact
    from api.src.contact.service import ContactCreate, create_contact
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


async def seed_app_settings(dry_run: bool = False) -> None:
    """Seed a default ``model_config`` app setting so /sernia-settings renders.

    Only writes when the row is missing — never overrides a real configuration.
    """
    from sqlalchemy import select

    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.models import AppSetting

    async with AsyncSessionFactory() as session:
        existing = (
            await session.execute(select(AppSetting).where(AppSetting.key == "model_config"))
        ).scalar_one_or_none()
        if existing:
            log_info("✓ app_setting 'model_config' already exists")
            return
        if dry_run:
            log_info("[DRY RUN] Would create app_setting 'model_config'")
            return
        session.add(AppSetting(key="model_config", value={"model_key": "gpt-5.4", "thinking_effort": "medium"}))
        await session.commit()
        log_info("✓ Created app_setting 'model_config' (gpt-5.4 / medium)")


def _build_sample_conversations() -> list[dict]:
    """Synthetic but realistically-shaped Sernia conversations.

    Messages are built from real pydantic-ai message objects (not hand-written
    JSON), so they always match the persistence format the UI and history
    loaders expect — including tool call/return pairs.
    """
    from datetime import datetime, timedelta, timezone

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    now = datetime.now(timezone.utc)

    web_chat_messages = [
        ModelRequest(parts=[UserPromptPart(
            content="What maintenance tasks are open at 320?",
            timestamp=now - timedelta(hours=3),
        )]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="clickup_search_tasks",
            args={"query": "320 maintenance", "include_closed": False},
            tool_call_id="seed_call_clickup_1",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="clickup_search_tasks",
            content=(
                "- Task: Fix dripping faucet Unit 02 (id: seed0001)\n"
                "  Status: in progress | Priority: normal | Due: 2026-06-15\n"
                "  Assignees: John\n"
                "- Task: Replace hallway light bulb (id: seed0002)\n"
                "  Status: to do | Priority: low | Due: 2026-06-20\n"
                "  Assignees: Emilio"
            ),
            tool_call_id="seed_call_clickup_1",
            timestamp=now - timedelta(hours=3),
        )]),
        ModelResponse(parts=[TextPart(
            content=(
                "Two open maintenance tasks at 320:\n\n"
                "1. **Dripping faucet, Unit 02** — in progress, assigned to John, due Jun 15\n"
                "2. **Hallway light bulb** — to do, assigned to Emilio, due Jun 20"
            ),
        )]),
    ]

    sms_messages = [
        ModelRequest(parts=[UserPromptPart(
            content="did the plumber confirm for tomorrow?",
            timestamp=now - timedelta(hours=1),
        )]),
        ModelResponse(parts=[TextPart(
            content="Yes — confirmed for 9am tomorrow at 320 Unit 02. He'll text when en route.",
        )]),
    ]

    memory_messages = [
        ModelRequest(parts=[UserPromptPart(
            content="Remember that the new dishwasher for Unit 05 arrives June 18.",
            timestamp=now - timedelta(days=1),
        )]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="workspace_write_file",
            args={
                "path": "/workspace/daily_notes/2026-06-09_unit05-dishwasher.md",
                "content": "Dishwasher delivery for Unit 05 scheduled June 18.",
            },
            tool_call_id="seed_call_ws_1",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="workspace_write_file",
            content="Wrote 52 chars to /workspace/daily_notes/2026-06-09_unit05-dishwasher.md",
            tool_call_id="seed_call_ws_1",
            timestamp=now - timedelta(days=1),
        )]),
        ModelResponse(parts=[TextPart(
            content="Noted — I saved the June 18 dishwasher delivery for Unit 05 to my daily notes.",
        )]),
    ]

    return [
        {
            "conversation_id": "seed_demo_web_chat_maintenance",
            "messages": web_chat_messages,
            "modality": "web_chat",
            "contact_identifier": None,
            "metadata": {"seed": True, "description": "Demo web chat with ClickUp tool round-trip"},
        },
        {
            "conversation_id": "seed_demo_sms_plumber",
            "messages": sms_messages,
            "modality": "sms",
            "contact_identifier": "+14125550100",
            "metadata": {"seed": True, "description": "Demo SMS thread"},
        },
        {
            "conversation_id": "seed_demo_memory_note",
            "messages": memory_messages,
            "modality": "web_chat",
            "contact_identifier": None,
            "metadata": {"seed": True, "description": "Demo workspace-memory write"},
        },
    ]


async def seed_sample_conversations(dry_run: bool = False) -> None:
    """Seed demo Sernia conversations so the chat UI and DB-search tools have data.

    Non-production only: gives dev / PR / Claude-Code-on-web environments a
    usable app out of the box (conversation list, message rendering with tool
    calls, `db_search_conversations` results) without touching real data.
    Idempotent via fixed conversation IDs.
    """
    if os.getenv("RAILWAY_ENVIRONMENT_NAME", "") == "production":
        log_info("Skipping sample conversations (production environment)")
        return

    from sqlalchemy import select

    from api.src.ai_demos.models import AgentConversation, save_agent_conversation
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.config import AGENT_NAME

    samples = _build_sample_conversations()

    async with AsyncSessionFactory() as session:
        for sample in samples:
            existing = (
                await session.execute(
                    select(AgentConversation.id).where(AgentConversation.id == sample["conversation_id"])
                )
            ).scalar_one_or_none()
            if existing:
                log_info(f"✓ Conversation '{sample['conversation_id']}' already exists")
                continue
            if dry_run:
                log_info(f"[DRY RUN] Would create conversation '{sample['conversation_id']}'")
                continue
            await save_agent_conversation(
                session=session,
                conversation_id=sample["conversation_id"],
                agent_name=AGENT_NAME,
                messages=sample["messages"],
                clerk_user_id=None,  # shared team access, matches Sernia convention
                metadata=sample["metadata"],
                modality=sample["modality"],
                contact_identifier=sample["contact_identifier"],
            )
            await session.commit()
            log_info(f"✓ Created conversation '{sample['conversation_id']}'")


def _download_fixture_if_configured() -> bool:
    """Fetch the fixture from the private Railway bucket when creds are present.

    Returns True if a download happened. Fail-soft: a missing/unreachable
    bucket must never break seeding (and therefore environment setup).
    """
    from api.src.utils.seed_fixture import (
        FIXTURE_BUCKET_KEY,
        FIXTURE_LOCAL_PATH,
        bucket_client,
        bucket_env,
    )

    cfg = bucket_env()
    if cfg is None:
        return False
    try:
        FIXTURE_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        bucket_client(cfg).download_file(
            cfg["bucket"], FIXTURE_BUCKET_KEY, str(FIXTURE_LOCAL_PATH)
        )
        log_info(f"Downloaded seed fixture from bucket -> {FIXTURE_LOCAL_PATH}")
        return True
    except Exception as e:
        log_error(f"Seed fixture download failed (non-fatal): {e}")
        return False


async def seed_fixture_conversations(dry_run: bool = False) -> None:
    """Load sanitized real conversations exported by scripts/export_seed_fixture.py.

    The fixture lives in a private Railway bucket (this repo is public). When
    the SEED_BUCKET_* env vars are present and no local copy exists, it is
    downloaded first; with neither, this is a no-op. Non-production only,
    idempotent via the fixture's stable ``fixture_*`` ids.
    """
    import json
    from pathlib import Path

    from api.src.utils.seed_fixture import FIXTURE_LOCAL_PATH as FIXTURE_PATH

    if os.getenv("RAILWAY_ENVIRONMENT_NAME", "") == "production":
        log_info("Skipping fixture conversations (production environment)")
        return

    fixture_file = Path(FIXTURE_PATH)
    if not fixture_file.exists():
        _download_fixture_if_configured()
    if not fixture_file.exists():
        log_info(
            f"No fixture at {FIXTURE_PATH} and no SEED_BUCKET_* creds — skipping "
            "(see README.md 'Sanitized Seed Data')"
        )
        return

    from sqlalchemy import select

    from api.src.ai_demos.models import AgentConversation
    from api.src.database.database import AsyncSessionFactory

    rows = json.loads(fixture_file.read_text())
    async with AsyncSessionFactory() as session:
        for row in rows:
            existing = (
                await session.execute(
                    select(AgentConversation.id).where(AgentConversation.id == row["id"])
                )
            ).scalar_one_or_none()
            if existing:
                log_info(f"✓ Fixture conversation '{row['id']}' already exists")
                continue
            if dry_run:
                log_info(f"[DRY RUN] Would create fixture conversation '{row['id']}'")
                continue
            session.add(
                AgentConversation(
                    id=row["id"],
                    agent_name=row["agent_name"],
                    clerk_user_id=None,  # shared team access
                    messages=row["messages"],
                    metadata_=row.get("metadata_") or {},
                    modality=row.get("modality"),
                    contact_identifier=row.get("contact_identifier"),
                )
            )
            await session.commit()
            log_info(f"✓ Created fixture conversation '{row['id']}'")


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
    log_info("Seeding app settings...")
    await seed_app_settings(dry_run=dry_run)

    log_info("")
    log_info("Seeding sample conversations (non-production only)...")
    await seed_sample_conversations(dry_run=dry_run)

    log_info("")
    log_info("Seeding fixture conversations (non-production only)...")
    await seed_fixture_conversations(dry_run=dry_run)

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
