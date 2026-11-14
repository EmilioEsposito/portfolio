"""Shared production gate for automated Sernia operations."""

import os

import logfire
from sqlalchemy import select

_IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT_NAME", "") == "production"


async def is_sernia_ai_enabled() -> bool:
    """Check if automated Sernia AI runs are enabled.

    Universal kill switch for all automated agent runs — scheduled checks,
    AI SMS event triggers, and email event triggers. Web chat and HITL
    approvals are never gated by this.

    Non-production environments are hard-gated off regardless of the DB
    value. Neon PR branches inherit rows from the parent DB, so the
    ``triggers_enabled`` row in a PR env is whatever production had at
    branch time — we can't trust it to reflect operator intent on that
    environment. Forcing False on non-prod means a PR deployment will
    never fire automated triggers, even if its inherited DB row says
    True. Local dev is the same — set RAILWAY_ENVIRONMENT_NAME=production
    in a throwaway shell if you genuinely need to exercise triggers.

    On production, the DB setting acts as the kill switch: missing row
    defaults to enabled, an explicit ``false`` disables.
    """
    from api.src.database.app_settings import AppSetting
    from api.src.database.database import AsyncSessionFactory

    if not _IS_PRODUCTION:
        return False

    enabled = True
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "triggers_enabled")
            )
            value = result.scalar_one_or_none()
            if value is not None:
                enabled = value
    except Exception:
        logfire.warn(
            "sernia_ai enabled check failed (table may not exist), using env default",
            default=enabled,
        )
    return bool(enabled)


def background_automation_enabled() -> bool:
    value = os.getenv("BACKGROUND_AUTOMATION_ENABLED")
    if value is None:
        return False
    if value.lower() not in {"true", "false", "1", "0"}:
        raise ValueError("BACKGROUND_AUTOMATION_ENABLED must be true, false, 1, or 0")
    return value.lower() in {"true", "1"}
