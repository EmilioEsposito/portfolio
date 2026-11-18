"""Durable Gmail progress in app_settings; no automatic cursor initialization."""

from sqlalchemy import select, update

from api.src.database.app_settings import AppSetting
from api.src.database.database import session_context
from api.src.google.gmail.config import GMAIL_WATCH_MAILBOX

CHECKPOINT_KEY = f"gmail_history_checkpoint:{GMAIL_WATCH_MAILBOX}"


def validate_history_id(value: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("Gmail checkpoint must be a decimal string")
    return str(int(value))


async def get_history_checkpoint() -> str | None:
    async with session_context() as session:
        value = await session.scalar(
            select(AppSetting.value).where(AppSetting.key == CHECKPOINT_KEY)
        )
    if value is None:
        return None
    return validate_history_id(value["history_id"])


async def advance_history_checkpoint(expected: str, completed: str) -> None:
    expected, completed = validate_history_id(expected), validate_history_id(completed)
    if int(completed) < int(expected):
        raise ValueError("Gmail checkpoint must not move backward")
    async with session_context() as session:
        # Compare-and-swap: concurrent deliveries may repeat work, but cannot
        # rewind or jump past another delivery's unresolved work. All saves and
        # durable Zillow enqueues complete before this commit.
        await session.execute(
            update(AppSetting)
            .where(AppSetting.key == CHECKPOINT_KEY, AppSetting.value == {"history_id": expected})
            .values(value={"history_id": completed})
        )
        await session.commit()
