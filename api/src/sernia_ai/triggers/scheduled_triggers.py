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
from api.src.schedulers.job_info import JobInfo
from api.src.sernia_ai.config import (
    DEFAULT_SCHEDULE_DAYS_OF_WEEK,
    DEFAULT_SCHEDULE_HOURS,
)
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger
from api.src.utils.automation import _IS_PRODUCTION

JOB_ID = "sernia_scheduled_checks"
ET = ZoneInfo("America/New_York")

# Shown on the team's Scheduler page (api/src/schedulers/job_info.py). Not part
# of the agent's prompt.
JOB_INFO = JobInfo(
    name="Sernia AI: scheduled inbox check",
    summary="Starts a run of the Sernia AI agent to check the inboxes, at the times set in Settings.",
    description=(
        "At each time set under Scheduled Checks on the Settings page (/team/settings), "
        "it starts one run of the general Sernia AI agent, telling it to follow its "
        "scheduled-checks skill. That skill is kept in the agent's private workspace, not "
        "in this repository, so what a check covers is set there; the Sernia AI triggers "
        "guide describes it as the email and SMS inbox checks."
        "\n\n"
        "The run has the agent's usual tools and approval rules. Its conversation is "
        "saved, and a push notification goes to the team only when something waits for a "
        "person's approval."
        "\n\n"
        "It runs only in production, and only while Triggers is on in Settings (the "
        "switch for all automated agent runs). Saving the schedule in Settings "
        "re-registers this job, and saving it with no days or no hours removes it; with no "
        "schedule saved it runs Monday to Friday at 8 am, 11 am, 2 pm and 5 pm Eastern. "
        "Paused or removed, the agent stops checking the inboxes on its own; texts to its "
        "number still start their own runs."
    ),
)


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
    """Register, re-register, or remove the APScheduler cron job.

    Empty ``days_of_week`` or ``hours`` means "no scheduled checks" — any
    existing job is removed. On non-production environments the job is
    always removed regardless of the config, mirroring the hard-gate in
    ``is_sernia_ai_enabled``.
    """
    scheduler = get_scheduler()

    if not _IS_PRODUCTION or not days_of_week or not hours:
        if scheduler.get_job(JOB_ID):
            scheduler.remove_job(JOB_ID)
            logfire.info(
                "Sernia AI scheduled trigger removed",
                reason="non_production" if not _IS_PRODUCTION else "empty_schedule",
            )
        return

    day_expr = ",".join(str(d) for d in sorted(days_of_week))
    hour_expr = ",".join(str(h) for h in sorted(hours))

    upsert_job(
        scheduler,
        info=JOB_INFO,
        func=run_scheduled_checks,
        trigger="cron",
        day_of_week=day_expr,
        hour=hour_expr,
        minute=0,
        timezone=ET,
        id=JOB_ID,
    )
    logfire.info(
        "Sernia AI scheduled trigger registered",
        days_of_week=day_expr,
        hours=hour_expr,
    )


async def get_schedule_config() -> dict:
    """Read schedule_config from DB, returning defaults if not set."""
    from api.src.database.app_settings import AppSetting
    from api.src.database.database import AsyncSessionFactory

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
