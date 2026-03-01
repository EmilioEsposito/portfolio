import os
from datetime import datetime

import logfire
from sqlalchemy import String, DateTime, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base

_IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT_NAME", "") == "production"


async def is_sernia_ai_enabled() -> bool:
    """Check if the Sernia AI agent is enabled via the `triggers_enabled` app setting.

    This is a universal kill switch for ALL Sernia AI agent runs — web chat,
    HITL approvals, SMS conversations, and background triggers.

    Default: enabled on production, disabled elsewhere (safety net for dev/PR envs).
    DB setting overrides the environment-based default when present.
    """
    from api.src.database.database import AsyncSessionFactory

    enabled = _IS_PRODUCTION
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


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<AppSetting(key={self.key!r})>"
