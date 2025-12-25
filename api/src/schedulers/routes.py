import asyncio
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Literal

from api.src.utils.clerk import verify_serniacapital_user

# Thin wrappers around the underlying scheduler endpoints
from api.src.dbos_service.routes import get_jobs as dbos_get_jobs
from api.src.dbos_service.routes import run_job_now as dbos_run_job_now
from api.src.apscheduler_service.routes import get_jobs as apscheduler_get_jobs
from api.src.apscheduler_service.routes import run_job_now as apscheduler_run_job_now
from api.src.apscheduler_service.routes import delete_job as apscheduler_delete_job


SchedulerService = Literal["dbos", "apscheduler"]

router = APIRouter(
    prefix="/schedulers",
    tags=["schedulers"],
    dependencies=[Depends(verify_serniacapital_user)],
)


def _with_service(job: dict, service: SchedulerService) -> dict:
    j = dict(job)
    j["service"] = service
    # Normalize shape for the frontend Scheduler component
    if j.get("name") is None:
        j["name"] = j.get("id")
    return j


@router.get("/get_jobs", response_model=List[dict])
async def get_jobs():
    """
    Retrieve scheduled jobs across DBOS and APScheduler.

    This is a thin wrapper around:
    - /dbos/get_jobs
    - /apscheduler/get_jobs
    """
    dbos_jobs, aps_jobs = await asyncio.gather(
        dbos_get_jobs(),
        apscheduler_get_jobs(),
    )
    combined = [_with_service(j, "dbos") for j in dbos_jobs] + [
        _with_service(j, "apscheduler") for j in aps_jobs
    ]
    combined.sort(key=lambda j: (j.get("service", ""), j.get("id", "")))
    return combined


@router.get("/run_job_now/{service}/{job_id}", response_model=dict)
async def run_job_now(service: SchedulerService, job_id: str):
    """
    Trigger a scheduled job to run immediately.

    This is a thin wrapper around:
    - /dbos/run_job_now/{job_id}
    - /apscheduler/run_job_now/{job_id}
    """
    if service == "dbos":
        result = await dbos_run_job_now(job_id)
        result["service"] = "dbos"
        return result
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

    DBOS "jobs" are declared in code and cannot be deleted via API.
    """
    if service == "apscheduler":
        result = await apscheduler_delete_job(job_id)
        result["service"] = "apscheduler"
        return result
    if service == "dbos":
        raise HTTPException(
            status_code=400,
            detail="DBOS jobs are declared in code and cannot be deleted via API.",
        )

    raise HTTPException(status_code=400, detail=f"Unknown scheduler service: {service}")
