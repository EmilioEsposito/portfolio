"""
FastAPI routes for Gmail-specific endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List
import logging
from openai import AsyncOpenAI
from sqlalchemy import select, func

from api_src.utils.dependencies import verify_cron_or_admin
from api_src.database.database import get_session
from api_src.google.gmail.db_ops import save_email_message, get_email_by_message_id
from api_src.google.gmail.service import (
    send_email,
    setup_gmail_watch,
    stop_gmail_watch,
    get_gmail_service,
    get_delegated_credentials,
    get_email_changes,
    get_email_content,
    process_single_message
)
from api_src.google.gmail.models import EmailMessage
from api_src.google.gmail.schema import (
    ZillowEmailResponse,
    GenerateResponseRequest,
    OptionalPassword
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
client = AsyncOpenAI()  # Create async client instance

router = APIRouter(prefix="/gmail", tags=["gmail"])

@router.get("/get_zillow_emails")
async def get_zillow_emails():
    """
    Fetch 10 random email messages containing 'zillow' in the body HTML,
    excluding daily listing emails.
    """
    try:
        async with get_session() as db:
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
            result = await db.execute(query)
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
        logger.error(f"Error fetching Zillow emails: {str(e)}")
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
        logger.error(f"Error generating email response: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate email response: {str(e)}"
        )

# Cron job route
@router.post("/watch/stop", dependencies=[Depends(verify_cron_or_admin)])
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
@router.post("/watch/start", dependencies=[Depends(verify_cron_or_admin)])
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
@router.post("/watch/refresh", dependencies=[Depends(verify_cron_or_admin)])
async def refresh_watch(payload: OptionalPassword):
    """
    Refreshes Gmail push notifications idempotently. Stops any existing watch and starts a new one.
    If no watch exists, just starts a new one.
    """
    try:
        # Try to stop any existing watch, but don't fail if there isn't one
        try:
            stop_gmail_watch()
            logger.info("✓ Stopped existing watch")
        except Exception as stop_error:
            logger.info(f"Note: Could not stop existing watch: {stop_error}")
        
        # Start a new watch
        result = setup_gmail_watch()
        logger.info(f"✓ Started new watch (expires: {result.get('expiration')})")
        
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