"""Inspect the configured DBOS scheduler (starts DBOS).

Run only against an intentionally selected development environment:
python -m scripts.inspect_dbos_scheduler
"""

from api.src.dbos_service.dbos_scheduler import (
    capture_scheduled_workflows,
    get_scheduled_jobs,
)


def main():

    # For testing: import everything needed to populate the registry
    from api.src.dbos_service.dbos_config import launch_dbos
    from api.src.utils.logfire_config import ensure_logfire_configured

    # Import scheduled workflows to register them

    ensure_logfire_configured(mode="test")

    # Capture workflows BEFORE launch
    capture_scheduled_workflows()

    launch_dbos()

    print("Getting scheduled DBOS jobs:")
    jobs = get_scheduled_jobs()
    print(f"Found {len(jobs)} jobs:")
    for job in jobs:
        print(f"  - {job['id']}: {job['trigger']}")
    print("DONE!")


if __name__ == "__main__":
    main()
