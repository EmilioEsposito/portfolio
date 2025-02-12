from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime
import os
from api_src.utils.dependencies import verify_cron_or_admin

router = APIRouter(
    prefix="/cron",  # All endpoints here will be under /cron
    tags=["cron"]    # Optional: groups endpoints in the docs
)


# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.get("/api/cron_job_example")
async def cron_job_example():
    return {"message": "Cron job executed", "timestamp": datetime.now().isoformat()}


# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.post(
    "/api/cron_job_example_private", dependencies=[Depends(verify_cron_or_admin)]
)
async def cron_job_example_private(payload: dict):
    print(f"Cron job executed with payload: {payload}")
    if "password" in payload:
        payload["password"] = "REDACTED"
    return {
        "message": "Private Cron job executed",
        "timestamp": datetime.now().isoformat(),
        "payload": payload,
    }
