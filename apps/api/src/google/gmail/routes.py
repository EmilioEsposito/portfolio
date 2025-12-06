"""
FastAPI routes for Gmail-specific endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List
import logfire
from openai import AsyncOpenAI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.src.utils.dependencies import verify_cron_or_admin
from apps.api.src.database.database import get_session
from apps.api.src.google.gmail.service import (
    send_email,
    setup_gmail_watch,
    stop_gmail_watch,
    get_gmail_service,
    get_delegated_credentials,
    get_email_changes,
    get_email_content,
    process_single_message
)
from apps.api.src.google.gmail.models import EmailMessage
from apps.api.src.google.gmail.schema import (
    ZillowEmailResponse,
    GenerateResponseRequest,
    OptionalPassword
)
import os

client = AsyncOpenAI()  # Create async client instance


router = APIRouter(prefix="/gmail", tags=["gmail"])

@router.get("/get_zillow_emails")
async def get_zillow_emails(session: AsyncSession = Depends(get_session)) -> List[ZillowEmailResponse]:
    """
    Fetch 10 random email messages containing 'zillow' in the body HTML,
    excluding daily listing emails.
    """
    try:
        # Construct the query
        query = (
            select(EmailMessage)
            .where(
                EmailMessage.body_html.ilike('%zillow%'),
                EmailMessage.subject.like('%is requesting%'),  # Only inquiries
                ~EmailMessage.subject.like('Re%')  # is NOT a reply
            )
            .order_by(func.random())
            .limit(5)
        )
        
        # Execute the query
        result = await session.execute(query)
        emails = result.scalars().all()
        
        # Format the response to match frontend expectations
        return [
            {
                "id": str(email.id),
                "subject": email.subject,
                "sender": email.from_address,
                "received_at": email.received_date.isoformat(),
                "body_html": email.body_html
            }
            for email in emails
        ]
    
    except Exception as e:
        logfire.error(f"Error fetching Zillow emails: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch Zillow emails: {str(e)}"
        )

@router.post("/generate_email_response")
async def generate_email_response(request: GenerateResponseRequest):
    """Generate an AI response to a Zillow email using the provided system instruction."""
    try:
        # Construct the prompt
        prompt = f"""You are an AI assistant helping to respond to a Zillow rental inquiry email.

System Instruction: {request.system_instruction}

Original Email:
{request.email_content}

Please generate a professional and appropriate response:"""

        # Call OpenAI API using async client
        openai_response = await client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": "You are a professional real estate assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        response_text = openai_response.choices[0].message.content
        return {"response": response_text}
        
    except Exception as e:
        logfire.error(f"Error generating email response: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate email response: {str(e)}"
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
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop Gmail watch: {str(e)}"
        )

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
            "expiration": result.get('expiration'),
            "historyId": result.get('historyId')
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Gmail watch: {str(e)}"
        )

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
                "expiration": result.get('expiration'),
                "historyId": result.get('historyId')
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh Gmail watch: {str(e)}"
        ) 