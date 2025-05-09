from fastapi import APIRouter, Depends, HTTPException
from apscheduler.job import Job
from typing import List
import logging

# Assuming the scheduler instance is globally available or accessible via a dependency
# We'll need to import it from where it's defined, likely service.py
from api.src.scheduler.service import scheduler # This might need adjustment
from api.src.utils.dependencies import verify_serniacapital_user
from datetime import datetime, timedelta
import pytest
from pprint import pprint
import asyncio

router = APIRouter(
    prefix="/scheduler",
    tags=["scheduler"],
    dependencies=[Depends(verify_serniacapital_user)]
)

logger = logging.getLogger(__name__)

# Helper to convert APScheduler Job to a more FastAPI-friendly dict
def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "func_ref": str(job.func_ref),
        "args": job.args,
        "kwargs": job.kwargs,
        "trigger": str(job.trigger),
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        "coalesce": job.coalesce,
        "executor": job.executor,
        "max_instances": job.max_instances,
        "misfire_grace_time": job.misfire_grace_time,
        "pending": job.pending,
    }

@router.get("/get_jobs", response_model=List[dict])
async def get_jobs():
    """
    Retrieve all scheduled jobs.
    """

    jobs = scheduler.get_jobs()
    # sort by job_id
    jobs.sort(key=lambda x: x.id)
    return [job_to_dict(job) for job in jobs]


@pytest.mark.asyncio
async def test_get_jobs():

    if not scheduler.running:
        logger.info("Starting scheduler for test_job...")
        scheduler.start()
    else:
        logger.info("Scheduler already running for test_job.")
    jobs = await get_jobs()
    pprint(jobs)
    scheduler.shutdown()
    assert len(jobs) > 0

@router.get("/run_job_now/{job_id}", response_model=dict)
async def run_job_now(job_id: str):
    """
    Triggers a specific job to run immediately.
    Note: This modifies the job's existing schedule to run once 'now'.
    If the job is a persistent one, its next_run_time will be updated.
    Consider if a one-off execution *without* altering the schedule is needed.
    """
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job with id {job_id} not found")
    
    try:
        job.modify(next_run_time=datetime.now(), misfire_grace_time=300)
        # scheduler.modify_job(job_id, next_run_time=datetime.now()+timedelta(seconds=2))
        updated_job = scheduler.get_job(job_id) # This will be the modified job
        return {"message": f"Job {job_id} has been triggered to run now.", "job_details": job_to_dict(updated_job) if updated_job else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run job {job_id}: {str(e)}") 
    


@pytest.mark.asyncio
async def test_run_job_now():
    if not scheduler.running:
        logger.info("Starting scheduler for test_job...")
        scheduler.start()
    else:
        logger.info("Scheduler already running for test_job.")
    job_id = "zillow_test_job"
    await run_job_now(job_id)
    await asyncio.sleep(10)
    scheduler.shutdown()