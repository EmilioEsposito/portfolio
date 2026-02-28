"""
Register Sernia AI trigger jobs with APScheduler.

Called from api/index.py lifespan alongside other scheduler registrations.
"""
import logfire

from api.src.apscheduler_service.service import get_scheduler


def register_sernia_trigger_jobs() -> None:
    """Register all Sernia AI scheduled trigger jobs with APScheduler."""
    from api.src.sernia_ai.triggers.email_trigger import (
        check_general_emails,
        check_zillow_emails,
    )

    scheduler = get_scheduler()

    # General email check — every 3 hours
    scheduler.add_job(
        func=check_general_emails,
        trigger="interval",
        hours=3,
        id="sernia_general_email_check",
        replace_existing=True,
        name="Sernia AI: General Email Check",
    )

    # Zillow email check — every 30 minutes during business hours (8am-8pm ET)
    # 8am ET = 13:00 UTC, 8pm ET = 01:00 UTC (next day)
    scheduler.add_job(
        func=check_zillow_emails,
        trigger="cron",
        hour="13-23,0",
        minute="*/30",
        id="sernia_zillow_email_check",
        replace_existing=True,
        name="Sernia AI: Zillow Email Check",
    )

    logfire.info("Sernia AI trigger jobs registered")
