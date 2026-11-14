"""
FastAPI routes for Gmail-specific endpoints.
"""

import os

import logfire
from fastapi import APIRouter, Depends, HTTPException

from api.src.google.gmail.service import (
    setup_gmail_watch,
    stop_gmail_watch,
)
from api.src.utils.dependencies import verify_cron_secret

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.post("/watch/stop", dependencies=[Depends(verify_cron_secret)])
async def stop_watch():
    """
    Stops Gmail push notifications.
    Requires Authorization: Bearer <CRON_SECRET>.
    """
    try:
        result = stop_gmail_watch()
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop Gmail watch: {str(e)}")


@router.post("/watch/start", dependencies=[Depends(verify_cron_secret)])
async def start_watch():
    """
    Starts Gmail push notifications.
    Requires Authorization: Bearer <CRON_SECRET>.
    """
    try:
        result = setup_gmail_watch()
        return {
            "success": True,
            "expiration": result.get("expiration"),
            "historyId": result.get("historyId"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start Gmail watch: {str(e)}")


@router.post("/watch/refresh", dependencies=[Depends(verify_cron_secret)])
async def refresh_watch():
    """
    Refreshes Gmail push notifications idempotently. Stops any existing watch and starts a new one.
    If no watch exists, just starts a new one.
    Requires Authorization: Bearer <CRON_SECRET>.
    """
    try:
        if os.getenv("RAILWAY_ENVIRONMENT_NAME") == "development":
            logfire.info("Skipping Gmail watch refresh in hosted development environment")
            return {
                "success": True,
                "message": "Skipping Gmail watch refresh in hosted development environment",
            }
        else:
            # Try to stop any existing watch, but don't fail if there isn't one
            try:
                stop_gmail_watch()
                logfire.info("✓ Stopped existing watch")
            except Exception as stop_error:
                logfire.info(f"Note: Could not stop existing watch: {stop_error}")

            # Start a new watch
            result = setup_gmail_watch()
            logfire.info(f"✓ Started new watch (expires: {result.get('expiration')})")

            return {
                "success": True,
                "message": "Watch refreshed successfully",
                "expiration": result.get("expiration"),
                "historyId": result.get("historyId"),
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh Gmail watch: {str(e)}")
