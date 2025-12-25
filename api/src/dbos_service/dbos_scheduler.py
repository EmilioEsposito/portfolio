"""
DBOS Scheduled Workflows Helper

This module provides utilities to list and manage DBOS scheduled workflows,
enabling the frontend to display and trigger scheduled jobs.

IMPORTANT: DBOS clears the registry.pollers list when launch() is called,
so we need to capture the scheduled workflow info BEFORE launch.
Call `capture_scheduled_workflows()` before `DBOS.launch()`.
"""

from datetime import datetime
from typing import Optional
import logfire
from croniter import croniter
import dbos._dbos as _dbos_module

# Storage for captured scheduled workflow info (populated before DBOS.launch())
_captured_workflows: list[dict] = []
_workflow_funcs: dict[str, callable] = {}


def _get_registry():
    """Get the DBOS global registry, accessing it dynamically."""
    return _dbos_module._dbos_global_registry


def capture_scheduled_workflows() -> None:
    """
    Capture scheduled workflow info from the DBOS registry BEFORE launch().

    This must be called after all @DBOS.scheduled decorated functions are imported
    but BEFORE DBOS.launch() is called, because launch() clears the pollers list.
    """
    global _captured_workflows, _workflow_funcs
    _captured_workflows = []
    _workflow_funcs = {}

    registry = _get_registry()
    if registry is None:
        logfire.warning("DBOS registry not initialized - cannot capture workflows")
        return

    for poller in registry.pollers:
        # Poller structure: (stop_event, scheduler_loop_func, (workflow_func, cron, stop_event), {})
        if len(poller) >= 3 and len(poller[2]) >= 2:
            args = poller[2]
            workflow_func = args[0]
            cron = args[1]

            func_name = getattr(workflow_func, "__name__", str(workflow_func))
            func_module = getattr(workflow_func, "__module__", "")
            func_ref = f"{func_module}:{func_name}" if func_module else func_name

            _workflow_funcs[func_name] = workflow_func
            _captured_workflows.append({
                "func_name": func_name,
                "func_ref": func_ref,
                "cron": cron,
            })

    logfire.info(f"Captured {len(_captured_workflows)} DBOS scheduled workflows")


def get_scheduled_jobs() -> list[dict]:
    """
    Get all DBOS scheduled workflows.

    Returns a list of job dictionaries compatible with the frontend Scheduler component.
    Uses captured workflow info (must call capture_scheduled_workflows() first).
    """
    jobs = []

    if not _captured_workflows:
        # Try to capture from registry if not yet captured (before launch)
        registry = _get_registry()
        if registry and registry.pollers:
            capture_scheduled_workflows()

    for wf in _captured_workflows:
        # Calculate next run time
        next_run = None
        try:
            cron_iter = croniter(wf["cron"], datetime.now())
            next_run = cron_iter.get_next(datetime)
        except Exception as e:
            logfire.error(f"Error calculating next run time for {wf['func_name']}: {e}")

        jobs.append({
            "id": wf["func_name"],
            "name": wf["func_name"],
            "func_ref": wf["func_ref"],
            "args": [],
            "kwargs": {},
            "trigger": f"cron[{wf['cron']}]",
            "next_run_time": next_run.isoformat() if next_run else None,
            "coalesce": True,
            "executor": "dbos",
            "max_instances": 1,
            "misfire_grace_time": 300,
            "pending": False,
        })

    # Sort by job id
    jobs.sort(key=lambda x: x["id"])
    return jobs


def get_scheduled_job(job_id: str) -> Optional[dict]:
    """Get a specific scheduled job by ID."""
    jobs = get_scheduled_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return job
    return None


def get_workflow_func(job_id: str):
    """Get the workflow function for a scheduled job by ID."""
    # First check captured workflows
    if job_id in _workflow_funcs:
        return _workflow_funcs[job_id]

    # Fallback to registry (works before launch)
    registry = _get_registry()
    if registry is None:
        return None

    for poller in registry.pollers:
        if len(poller) >= 3 and len(poller[2]) >= 2:
            workflow_func = poller[2][0]
            func_name = getattr(workflow_func, "__name__", str(workflow_func))
            if func_name == job_id:
                return workflow_func

    return None


if __name__ == '__main__':
    # For testing: import everything needed to populate the registry
    from api.src.utils.logfire_config import ensure_logfire_configured
    from api.src.dbos_service.dbos_config import launch_dbos
    # Import scheduled workflows to register them
    from api.src.zillow_email.service import (
        zillow_test_job_scheduled,
        zillow_email_new_unreplied_scheduled,
        zillow_email_threads_ai_scheduled,
    )
    from api.src.clickup.service import clickup_peppino_tasks_scheduled
    from api.src.dbos_service.examples.hello_dbos import scheduled_workflow_example

    ensure_logfire_configured(mode='test')

    # Capture workflows BEFORE launch
    capture_scheduled_workflows()

    launch_dbos()

    print("Getting scheduled DBOS jobs:")
    jobs = get_scheduled_jobs()
    print(f"Found {len(jobs)} jobs:")
    for job in jobs:
        print(f"  - {job['id']}: {job['trigger']}")
    print("DONE!")
