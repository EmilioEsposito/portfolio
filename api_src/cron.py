from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
import os

router = APIRouter(
    prefix="/cron",  # All endpoints here will be under /cron
    tags=["cron"]    # Optional: groups endpoints in the docs
)


# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.get("/api/cron_job_example")
async def cron_job_example():

    return {"message": "Cron job executed", "timestamp": datetime.now().isoformat()}


# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.get("/api/cron_job_example_private")
async def cron_job_example_private(request: Request):
    auth_header = request.headers.get("authorization")
    if not auth_header or auth_header != f"Bearer {os.environ.get('CRON_SECRET')}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {"message": "Private Cron job executed", "timestamp": datetime.now().isoformat()}

