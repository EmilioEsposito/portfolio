"""Durable unavailable-message evidence, separate from synchronization progress."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from api.src.database.app_settings import AppSetting
from api.src.database.database import session_context
from api.src.google.gmail.config import GMAIL_WATCH_MAILBOX

PREFIX = f"gmail_unavailable:{GMAIL_WATCH_MAILBOX}:"


async def record_unavailable(message_id: str, observation_key: str, evidence: dict) -> None:
    """Commit evidence before acknowledging a 404; retries merge without resolving it."""
    now = datetime.now(UTC).isoformat()
    value = {
        "mailbox": GMAIL_WATCH_MAILBOX,
        "message_id": message_id,
        "status": "unresolved",
        "classification": "unknown",
        "first_recorded_at": now,
        "last_observed_at": now,
        "observations": {observation_key: evidence},
    }
    statement = insert(AppSetting).values(key=PREFIX + message_id, value=value)
    # Atomic merge retains original evidence and human classification. A repeated
    # page uses the same observation key, avoiding duplicate evidence entries.
    merged = AppSetting.value.op("||")(
        func.jsonb_build_object(
            "last_observed_at",
            now,
            "status",
            "unresolved",
            "observations",
            AppSetting.value["observations"].op("||")(statement.excluded.value["observations"]),
        )
    )
    async with session_context() as session:
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["key"], set_={"value": merged, "updated_at": func.now()}
            )
        )
        await session.commit()


async def has_unresolved_exception(message_id: str) -> bool:
    async with session_context() as session:
        value = await session.scalar(
            select(AppSetting.value).where(AppSetting.key == PREFIX + message_id)
        )
    # Malformed/unknown states also fail closed for historical lead automation.
    return value is not None and value.get("status") != "resolved"
