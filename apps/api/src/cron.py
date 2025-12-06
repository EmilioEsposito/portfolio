import logfire
from aiohttp_retry import Union
from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime
import os
from apps.api.src.utils.dependencies import verify_cron_or_admin
from sqlalchemy import text
from apps.api.src.database.database import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.src.open_phone.service import send_message
from fastapi.responses import JSONResponse
from apps.api.src.contact.service import get_contact_by_slug


router = APIRouter(
    prefix="/cron",  # All endpoints here will be under /cron
    tags=["cron"]    # Optional: groups endpoints in the docs
)



# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.get("/cron_job_example")
async def cron_job_example():
    return {"message": "Cron job executed", "timestamp": datetime.now().isoformat()}


# Note hobby plan only allows for cron job once per day. Deployment will fail without error message otherwise.
@router.get(
    "/cron_job_example_private", dependencies=[Depends(verify_cron_or_admin)]
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
