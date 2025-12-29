from fastapi import APIRouter, Depends, HTTPException
from typing import List
import logfire
from datetime import datetime

from api.src.dbos_service.dbos_scheduler import get_scheduled_jobs, get_scheduled_job, get_workflow_func
from api.src.utils.clerk import verify_serniacapital_user
import pytest
from pprint import pprint

router = APIRouter(
    prefix="/dbos",
    tags=["dbos"],
    dependencies=[Depends(verify_serniacapital_user)]
)


@router.get("/get_jobs", response_model=List[dict])
async def get_jobs():
    """
    Retrieve all DBOS scheduled workflows.
    """
    return get_scheduled_jobs()


@pytest.mark.asyncio
async def test_get_jobs():
    jobs = await get_jobs()
    pprint(jobs)
    assert len(jobs) > 0


@router.get("/run_job_now/{job_id}", response_model=dict)
async def run_job_now(job_id: str):
    """
    Triggers a specific DBOS scheduled workflow to run immediately.
    This starts a new workflow execution with the current time as both scheduled and actual time.
    """
    job = get_scheduled_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job with id {job_id} not found")

    workflow_func = get_workflow_func(job_id)
    if not workflow_func:
        raise HTTPException(status_code=404, detail=f"Workflow function for job {job_id} not found")

    try:
        # Execute the workflow immediately
        now = datetime.now()
        logfire.info(f"Manually triggering DBOS workflow: {job_id}")
        await workflow_func(now, now)
        return {
            "message": f"Job {job_id} has been triggered to run now.",
            "job_details": job
        }
    except Exception as e:
        logfire.exception(f"Failed to run job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run job {job_id}: {str(e)}")

