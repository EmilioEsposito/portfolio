"""
Register Sernia AI trigger jobs with APScheduler.

Called from api/index.py lifespan alongside other scheduler registrations.
"""
import logfire

from api.src.apscheduler_service.service import get_scheduler, upsert_job
from api.src.sernia_ai.config import (
    GENERAL_EMAIL_CHECK_INTERVAL_MINUTES,
    ZILLOW_EMAIL_CHECK_INTERVAL_HOURS,
)


def register_scheduled_triggers() -> None:
    """Register all Sernia AI scheduled trigger jobs with APScheduler."""
    from api.src.sernia_ai.triggers.email_scheduled_trigger import (
        check_general_emails,
        check_zillow_emails,
    )

    scheduler = get_scheduler()

    upsert_job(
        scheduler,
        func=check_general_emails,
        trigger="interval",
        minutes=GENERAL_EMAIL_CHECK_INTERVAL_MINUTES,
        id="sernia_general_email_check",
        name="Sernia AI: General Email Check",
    )

    # Zillow email check — during business hours (8am-5pm ET)
    # 8am ET = 13:00 UTC, 5pm ET = 22:00 UTC
    upsert_job(
        scheduler,
        func=check_zillow_emails,
        trigger="cron",
        hour=f"13-22/{ZILLOW_EMAIL_CHECK_INTERVAL_HOURS}",
        minute=0,
        id="sernia_zillow_email_check",
        name="Sernia AI: Zillow Email Check",
    )

    logfire.info("Sernia AI trigger jobs registered")
