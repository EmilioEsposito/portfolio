"""
FastAPI routes for Gmail-specific endpoints.
"""

import json
import os

import logfire
from fastapi import APIRouter, Depends, HTTPException
from openai import APIStatusError

from api.src.google.gmail.demo import DEFAULT_INSTRUCTIONS, SAFETY_INSTRUCTIONS, SCENARIOS
from api.src.google.gmail.schema import (
    GenerateResponseRequest,
    OptionalPassword,
)
from api.src.google.gmail.service import (
    setup_gmail_watch,
    stop_gmail_watch,
)
from api.src.utils.dependencies import verify_cron_or_admin
from api.src.utils.llm import DEMO_MODEL_ID, async_public_openrouter_client

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.get("/get_zillow_emails")
async def get_zillow_emails() -> dict:
    """Public fictional scenarios; no mailbox or database access."""
    return {"scenarios": SCENARIOS, "default_instructions": DEFAULT_INSTRUCTIONS}


@router.post("/generate_email_response")
async def generate_email_response(request: GenerateResponseRequest) -> dict[str, str]:
    """Draft against a server-owned fictional scenario, never arbitrary email data."""
    scenario = next((item for item in SCENARIOS if item["id"] == request.scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=422, detail="Choose one of the supplied scenarios.")
    try:
        result = await async_public_openrouter_client().chat.completions.create(
            model=DEMO_MODEL_ID,
            messages=[
                {"role": "system", "content": SAFETY_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "writing_preferences": request.system_instruction,
                            "scenario": scenario,
                        }
                    ),
                },
            ],
            max_tokens=800,
            extra_body={"reasoning": {"effort": "low"}},
        )
        reply = result.choices[0].message.content
        if not reply or not reply.strip():
            raise ValueError("Empty draft")
        return {"response": reply, "scenario_id": scenario["id"]}
    except APIStatusError as exc:
        if exc.status_code == 402:
            raise HTTPException(
                status_code=402,
                detail="The demo budget is temporarily unavailable. Your work is preserved; please come back later.",
            ) from None
        if exc.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="The demo is temporarily at capacity. Please wait a minute before trying again.",
                headers={"Retry-After": "60"},
            ) from None
        logfire.exception("Email showcase provider request failed")
        raise HTTPException(
            status_code=503, detail="Drafting is temporarily unavailable. Please try again later."
        ) from None
    except Exception:
        logfire.exception("Email showcase draft failed")
        raise HTTPException(
            status_code=503, detail="Drafting is temporarily unavailable. Please try again shortly."
        )


# Cron job route - supports both GET and POST
@router.post("/watch/stop", dependencies=[Depends(verify_cron_or_admin)])
async def stop_watch(payload: OptionalPassword = None):
    """
    Stops Gmail push notifications.
    Can be called via GET (for cron) or POST (with optional password in body).
    """
    try:
        result = stop_gmail_watch()
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop Gmail watch: {str(e)}")


# Cron job route - supports both GET and POST
@router.post("/watch/start", dependencies=[Depends(verify_cron_or_admin)])
async def start_watch(payload: OptionalPassword = None):
    """
    Starts Gmail push notifications.
    Can be called via GET (for cron) or POST (with optional password in body).
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


# Cron job route to refresh Gmail watch - supports both GET and POST
@router.post("/watch/refresh", dependencies=[Depends(verify_cron_or_admin)])
async def refresh_watch(payload: OptionalPassword = None):
    """
    Refreshes Gmail push notifications idempotently. Stops any existing watch and starts a new one.
    If no watch exists, just starts a new one.
    Can be called via GET (for cron) or POST (with optional password in body).
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
