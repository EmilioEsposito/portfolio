from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from api.src.apscheduler_service.routes import delete_job as apscheduler_delete_job

# DBOS DISABLED: $75/month DB keep-alive costs too high for hobby project.
# See api/src/schedulers/README.md for re-enabling instructions.
# from api.src.dbos_service.routes import get_jobs as dbos_get_jobs
# from api.src.dbos_service.routes import run_job_now as dbos_run_job_now
from api.src.apscheduler_service.routes import get_jobs as apscheduler_get_jobs
from api.src.apscheduler_service.routes import run_job_now as apscheduler_run_job_now
from api.src.schedulers.job_info import info_for
from api.src.utils.clerk import verify_serniacapital_user

SchedulerService = Literal["dbos", "apscheduler"]

router = APIRouter(
    prefix="/schedulers",
    tags=["schedulers"],
    dependencies=[Depends(verify_serniacapital_user)],
)


def _with_service(job: dict, service: SchedulerService) -> dict:
    """Normalize a job for the Scheduler page, with its name, summary and description.

    The description registered with the job (``job_info.py``) wins. A job added
    without one keeps its scheduler name (or its id) and has no summary or
    description.
    """
    j = dict(job)
    j["service"] = service
    info = info_for(j.get("id", ""))
    j["name"] = info.name if info else (j.get("name") or j.get("id"))
    j["summary"] = info.summary if info else None
    j["description"] = info.description if info else None
    return j


@router.get("/get_jobs", response_model=list[dict])
async def get_jobs():
    """
    Retrieve scheduled jobs from APScheduler.

    DBOS is currently disabled. See api/src/schedulers/README.md for re-enabling.
    """
    # DBOS DISABLED: Only return APScheduler jobs
    aps_jobs = await apscheduler_get_jobs()
    combined = [_with_service(j, "apscheduler") for j in aps_jobs]
    combined.sort(key=lambda j: (j.get("service", ""), j.get("id", "")))
    return combined


@router.get("/run_job_now/{service}/{job_id}", response_model=dict)
async def run_job_now(service: SchedulerService, job_id: str):
    """
    Trigger a scheduled job to run immediately.

    DBOS is currently disabled. See api/src/schedulers/README.md for re-enabling.
    """
    if service == "dbos":
        # DBOS DISABLED
        raise HTTPException(
            status_code=400,
            detail="DBOS is currently disabled. See api/src/schedulers/README.md for re-enabling.",
        )
    if service == "apscheduler":
        result = await apscheduler_run_job_now(job_id)
        result["service"] = "apscheduler"
        return result

    # Should be unreachable due to Literal typing, but keep defensive.
    raise HTTPException(status_code=400, detail=f"Unknown scheduler service: {service}")


@router.delete("/delete_job/{service}/{job_id}", response_model=dict)
async def delete_job(service: SchedulerService, job_id: str):
    """
    Delete a scheduled job (only supported for APScheduler).
    """
    if service == "apscheduler":
        result = await apscheduler_delete_job(job_id)
        result["service"] = "apscheduler"
        return result
    if service == "dbos":
        raise HTTPException(
            status_code=400,
            detail="DBOS is currently disabled. See api/src/schedulers/README.md for re-enabling.",
        )

    raise HTTPException(status_code=400, detail=f"Unknown scheduler service: {service}")
