"""
Scheduled triggers for the Sernia AI agent.

One job wakes the agent up periodically. All instructions live in the
scheduled-checks workspace skill.

Schedule is configurable via the ``schedule_config`` AppSetting (JSONB):
    {
        "days_of_week": [0, 1, 2, 3, 4],   # 0=Mon … 6=Sun
        "hours": [8, 11, 14, 17]             # ET hours (24-h)
    }

Falls back to DEFAULT_SCHEDULE_* constants in config.py when no DB row exists.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import logfire
from sqlalchemy import select

from api.src.apscheduler_service.service import get_scheduler, upsert_job
from api.src.sernia_ai.config import (
    DEFAULT_SCHEDULE_DAYS_OF_WEEK,
    DEFAULT_SCHEDULE_HOURS,
)
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger

JOB_ID = "sernia_scheduled_checks"
ET = ZoneInfo("America/New_York")


async def run_scheduled_checks() -> None:
    logfire.info("scheduled_trigger: running scheduled checks")
    await run_agent_for_trigger(
        trigger_source="scheduled_check",
        trigger_prompt="Scheduled inbox check. Follow the scheduled-checks skill.",
        trigger_metadata={"trigger_source": "scheduled_check", "trigger_type": "scheduled_check"},
        rate_limit_key="scheduled_check",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_schedule(days_of_week: list[int], hours: list[int]) -> None:
    """Register (or re-register) the APScheduler cron job with the given config."""
    scheduler = get_scheduler()
    day_expr = ",".join(str(d) for d in sorted(days_of_week)) if days_of_week else "0-6"
    hour_expr = ",".join(str(h) for h in sorted(hours)) if hours else "8"

    upsert_job(
        scheduler,
        func=run_scheduled_checks,
        trigger="cron",
        day_of_week=day_expr,
        hour=hour_expr,
        minute=0,
        timezone=ET,
        id=JOB_ID,
        name="Sernia AI: Scheduled Checks",
    )
    logfire.info(
        "Sernia AI scheduled trigger registered",
        days_of_week=day_expr,
        hours=hour_expr,
    )


async def get_schedule_config() -> dict:
    """Read schedule_config from DB, returning defaults if not set."""
    from api.src.database.database import AsyncSessionFactory
    from api.src.sernia_ai.models import AppSetting

    defaults = {
        "days_of_week": DEFAULT_SCHEDULE_DAYS_OF_WEEK,
        "hours": DEFAULT_SCHEDULE_HOURS,
    }
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(AppSetting.value).where(AppSetting.key == "schedule_config")
            )
            row = result.scalar_one_or_none()
            if row is not None and isinstance(row, dict):
                return {
                    "days_of_week": row.get("days_of_week", defaults["days_of_week"]),
                    "hours": row.get("hours", defaults["hours"]),
                }
    except Exception:
        logfire.warn("Failed to read schedule_config from DB, using defaults")
    return defaults


async def apply_schedule_from_db() -> None:
    """Read config from DB and (re-)register the scheduled job."""
    config = await get_schedule_config()
    _apply_schedule(config["days_of_week"], config["hours"])


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def register_scheduled_triggers() -> None:
    """Register Sernia AI scheduled trigger with APScheduler (reads config from DB)."""
    await apply_schedule_from_db()
