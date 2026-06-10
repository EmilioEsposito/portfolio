#!/usr/bin/env python3
"""Export sanitized Sernia conversations as a seed fixture and upload to the bucket.

Run this LOCALLY against the real database (Neon) — dev environments can't
reach it. It pulls the most recent Sernia conversations, redacts PII (phone
numbers and email addresses, deterministically so threads stay internally
consistent), truncates oversized tool results (the bulk of the data, and the
hardest part to review), and writes a JSON fixture.

The fixture is NOT committed — this repo is public. It lives in a private
Railway bucket; `api/seed_db.py` downloads it automatically in non-production
environments that have the SEED_BUCKET_* env vars (see
api/src/utils/seed_fixture.py for the variable names).

Usage (from repo root, with .env pointing at the source DB + bucket creds):
    uv run python scripts/export_seed_fixture.py                # 1. export
    # 2. REVIEW api/seed_fixtures/agent_conversations.json
    uv run python scripts/export_seed_fixture.py --upload       # 3. upload reviewed file

`--upload` deliberately does NOT re-export — it uploads the file you just
reviewed, exactly as reviewed.
"""
import argparse
import asyncio
import json
import sys

from api.src.utils.seed_fixture import (
    FIXTURE_BUCKET_KEY,
    FIXTURE_LOCAL_PATH,
    bucket_client,
    bucket_env,
    digest,
    sanitize_text,
    sanitize_value,
)


async def export(limit: int) -> None:
    from sqlalchemy import select

    from api.src.ai_demos.models import AgentConversation
    from api.src.database.database import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(AgentConversation)
            .where(AgentConversation.agent_name == "sernia")
            .order_by(AgentConversation.updated_at.desc())
            .limit(limit)
        )
        conversations = result.scalars().all()

    rows = []
    for conv in conversations:
        rows.append(
            {
                # Stable fixture id namespace — never collides with real rows
                # when the fixture is loaded back into another environment.
                "id": f"fixture_{digest(conv.id, 10)}",
                "agent_name": conv.agent_name,
                "messages": sanitize_value(conv.messages),
                "metadata_": sanitize_value(conv.metadata_ or {}) | {"seed_fixture": True},
                "modality": conv.modality,
                "contact_identifier": sanitize_text(conv.contact_identifier)
                if conv.contact_identifier
                else None,
            }
        )

    FIXTURE_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_LOCAL_PATH.write_text(json.dumps(rows, indent=2, default=str) + "\n")
    size_kb = FIXTURE_LOCAL_PATH.stat().st_size / 1024
    print(f"Wrote {len(rows)} sanitized conversations to {FIXTURE_LOCAL_PATH} ({size_kb:.0f} KB)")
    print(
        "\nIMPORTANT: phones/emails are redacted automatically, but names and "
        "free-text business details are NOT exhaustively scrubbed.\n"
        "The fixture is gitignored — distribute it via the seed bucket "
        "(--upload), never by committing it."
    )


def upload() -> None:
    from api.src.utils.seed_bucket import get_seed_bucket

    bucket = get_seed_bucket()
    if bucket is None:
        print(
            "SEED_BUCKET_* env vars not set (need SEED_BUCKET_ENDPOINT_URL, "
            "SEED_BUCKET_NAME, SEED_BUCKET_ACCESS_KEY_ID, "
            "SEED_BUCKET_SECRET_ACCESS_KEY — from the Railway bucket's "
            "Credentials tab).",
            file=sys.stderr,
        )
        sys.exit(1)
    bucket.upload_fixture(str(OUTPUT_PATH))
    print(f"Uploaded {OUTPUT_PATH} -> s3://{bucket.bucket_name}/{bucket.FIXTURE_KEY}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="How many recent conversations to export")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the fixture to the seed bucket after exporting (uses SEED_BUCKET_* env vars)",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="Skip the export and just upload the existing local fixture file",
    )
    args = parser.parse_args()
    try:
        if not args.upload_only:
            asyncio.run(export(args.limit))
        if args.upload or args.upload_only:
            if not OUTPUT_PATH.exists():
                print(f"No fixture file at {OUTPUT_PATH} to upload", file=sys.stderr)
                sys.exit(1)
            upload()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
