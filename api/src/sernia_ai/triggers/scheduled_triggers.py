"""
Scheduled triggers for the Sernia AI agent.

One job wakes the agent up periodically. All instructions live in the
scheduled-checks workspace skill.
"""
import logfire

from api.src.apscheduler_service.service import get_scheduler, upsert_job
from api.src.sernia_ai.config import SCHEDULED_CHECK_INTERVAL_HOURS
from api.src.sernia_ai.triggers.background_agent_runner import run_agent_for_trigger


async def run_scheduled_checks() -> None:
    logfire.info("scheduled_trigger: running scheduled checks")
    await run_agent_for_trigger(
        trigger_source="scheduled_check",
        trigger_prompt="Scheduled inbox check. Follow the scheduled-checks skill.",
        trigger_metadata={"trigger_source": "scheduled_check", "trigger_type": "scheduled_check"},
        rate_limit_key="scheduled_check",
    )


def register_scheduled_triggers() -> None:
    """Register Sernia AI scheduled trigger with APScheduler."""
    scheduler = get_scheduler()

    from zoneinfo import ZoneInfo

    # Business hours: 8am-5pm ET
    upsert_job(
        scheduler,
        func=run_scheduled_checks,
        trigger="cron",
        hour=f"8-17/{SCHEDULED_CHECK_INTERVAL_HOURS}",
        minute=0,
        timezone=ZoneInfo("America/New_York"),
        id="sernia_scheduled_checks",
        name="Sernia AI: Scheduled Checks",
    )

    logfire.info("Sernia AI scheduled triggers registered")
