"""Stored web-run status and admission locks, without constructing an agent."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text

RUN_KEY = "web_chat_run"


def public_run(metadata: dict | None) -> dict | None:
    run = (metadata or {}).get(RUN_KEY)
    if not isinstance(run, dict):
        return None
    run = dict(run)
    if run.get("status") == "running":
        try:
            if datetime.fromisoformat(run["expires_at"]) < datetime.now(UTC):
                run["status"] = "interrupted"
        except (KeyError, TypeError, ValueError):
            run["status"] = "interrupted"
    return run


async def lock_conversation(session, conversation_id: str) -> None:
    # A transaction lock also serializes admission for a not-yet-created row.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"),
        {"id": f"sernia-chat:{conversation_id}"},
    )


def require_idle(metadata: dict | None) -> None:
    run = public_run(metadata)
    if run and run["status"] == "running":
        raise HTTPException(409, "This conversation is still running. Wait or cancel it first.")
