"""
FastAPI routes for Google API endpoints.
"""

from fastapi import APIRouter, HTTPException, Request, Response, Depends
from typing import Dict, Any, List
import os
import json
from api_src.utils.dependencies import verify_cron_or_admin
from pydantic import BaseModel
from typing import Union
from api_src.google.gmail import (
    send_email,
    get_oauth_url,
    setup_gmail_watch,
    stop_gmail_watch
)

router = APIRouter(prefix="/google", tags=["google"])

# https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200
@router.post("/gmail/notifications")
async def handle_gmail_notifications(request: Request):
    """
    Receives Gmail push notifications from Google Pub/Sub.
    """
    try:
        # Get the raw request body
        body = await request.body()
        
        # Log the notification for debugging
        print("Received Gmail notification:", body.decode())
        
        # Verify the request is from Google Pub/Sub
        # TODO: Add proper verification using the Authentication header
        
        # Parse the message data
        data = await request.json()
        if 'message' in data:
            # Extract and decode the message data
            message_data = data['message'].get('data', '')
            if message_data:
                # Process the notification
                # TODO: Add your notification processing logic here
                pass
        
        # Return 204 to acknowledge receipt
        return Response(status_code=204)
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process Gmail notification: {str(e)}"
        )


class OptionalPassword(BaseModel):
    password: Union[str, None] = None


# Cron job route
@router.post("/gmail/watch/stop", dependencies=[Depends(verify_cron_or_admin)])
async def stop_watch(payload: OptionalPassword):
    """
    Stops Gmail push notifications.
    """
    try:
        result = stop_gmail_watch()
        return {"success": result}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop Gmail watch: {str(e)}"
        )

# Cron job route
@router.post("/gmail/watch/start", dependencies=[Depends(verify_cron_or_admin)])
async def start_watch(payload: OptionalPassword):
    """
    Starts Gmail push notifications.
    """
    try:
        result = setup_gmail_watch()
        return {
            "success": True,
            "expiration": result.get('expiration'),
            "historyId": result.get('historyId')
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Gmail watch: {str(e)}"
        )


# Cron job route to refresh Gmail watch
@router.post("/gmail/watch/refresh", dependencies=[Depends(verify_cron_or_admin)])
async def refresh_watch(payload: OptionalPassword):
    """
    Refreshes Gmail push notifications idempotently. Stops any existing watch and starts a new one.
    If no watch exists, just starts a new one.
    """
    try:
        # Try to stop any existing watch, but don't fail if there isn't one
        try:
            stop_gmail_watch()
            print("✓ Stopped existing watch")
        except Exception as stop_error:
            print(f"Note: Could not stop existing watch: {stop_error}")
        
        # Start a new watch
        result = setup_gmail_watch()
        print(f"✓ Started new watch (expires: {result.get('expiration')})")
        
        return {
            "success": True,
            "message": "Watch refreshed successfully",
            "expiration": result.get('expiration'),
            "historyId": result.get('historyId')
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh Gmail watch: {str(e)}"
        )

