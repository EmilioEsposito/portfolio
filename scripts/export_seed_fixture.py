#!/usr/bin/env python3
"""Export sanitized Sernia conversations as a committed seed fixture.

Run this LOCALLY against the real database (Neon) — dev environments can't
reach it. It pulls the most recent Sernia conversations, redacts PII
(phone numbers and email addresses, deterministically so threads stay
internally consistent), and writes a JSON fixture that `api/seed_db.py`
loads into any non-production database.

Usage (from repo root, with .env pointing at the source DB):
    uv run python scripts/export_seed_fixture.py            # 10 most recent
    uv run python scripts/export_seed_fixture.py --limit 25

ALWAYS review the output file for PII the regexes missed (names, addresses
in free text) BEFORE committing:
    api/seed_fixtures/agent_conversations.json
"""
import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

OUTPUT_PATH = Path("api/seed_fixtures/agent_conversations.json")

# Phone numbers: +1 412 555 0100 / (412) 555-0100 / 4125550100 / +14125550100
_PHONE_RE = re.compile(r"\+?1?[\s.\-(]*\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def _digest(value: str, n: int = 7) -> str:
    """Deterministic short numeric digest so the same input maps to the same fake."""
    return str(int(hashlib.sha256(value.encode()).hexdigest(), 16))[-n:]


def _redact_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"+1555{_digest(digits)}"


def _redact_email(match: re.Match) -> str:
    domain = match.group(1).lower()
    if domain == "serniacapital.com":
        # Keep the internal domain — internal/external routing logic depends on it.
        return f"internal-{_digest(match.group(0))}@serniacapital.com"
    return f"user-{_digest(match.group(0))}@example.com"


def sanitize_text(text: str) -> str:
    text = _PHONE_RE.sub(_redact_phone, text)
    text = _EMAIL_RE.sub(_redact_email, text)
    return text


def sanitize_value(value):
    """Recursively sanitize strings inside any JSON-ish structure."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_value(v) for k, v in value.items()}
    return value


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
                "id": f"fixture_{_digest(conv.id, 10)}",
                "agent_name": conv.agent_name,
                "messages": sanitize_value(conv.messages),
                "metadata_": sanitize_value(conv.metadata_ or {}) | {"seed_fixture": True},
                "modality": conv.modality,
                "contact_identifier": sanitize_text(conv.contact_identifier)
                if conv.contact_identifier
                else None,
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(rows, indent=2, default=str) + "\n")
    print(f"Wrote {len(rows)} sanitized conversations to {OUTPUT_PATH}")
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
        print(f"Export failed: {e}", file=sys.stderr)
        sys.exit(1)
