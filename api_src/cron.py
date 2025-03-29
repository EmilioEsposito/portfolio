import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime
import os
from api_src.utils.dependencies import verify_cron_or_admin
from sqlalchemy import text
from api_src.database.database import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from api_src.open_phone import send_message
from fastapi.responses import JSONResponse


router = APIRouter(
    prefix="/cron",  # All endpoints here will be under /cron
    tags=["cron"]    # Optional: groups endpoints in the docs
)

logger = logging.getLogger(__name__)

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


@router.api_route(
    "/check_unreplied_emails",
    methods=["GET", "POST"],
    dependencies=[Depends(verify_cron_or_admin)],
)
async def check_unreplied_emails(
    target_phone_number: str = "+14129101989",
    session: AsyncSession = Depends(get_session),
):
    """
    Cron endpoint to check for unreplied emails and send a summary via OpenPhone.
    This endpoint is scheduled to run at 8am, 12pm, and 5pm ET via vercel.json.

    Args:
        target_phone_number: Optional phone number to send the alert to. Defaults to +14122703505.
    """
    logger.info(
        f"Running check for unreplied emails, target phone: {target_phone_number}"
    )

    try:
        # SQL query to find unreplied emails within past week that were received >4 hours ago
        with open("api_src/unreplied_emails.sql", "r") as f:
            sql_query = f.read()

        # Execute the query
        result = await session.execute(text(sql_query))
        unreplied_emails = result.fetchall()

        # If no unreplied emails, return early
        if not unreplied_emails:
            logger.info("No unreplied emails found")
            return JSONResponse(status_code=204, content=None)

        # Format the results for the message
        formatted_results = []
        for email in unreplied_emails:
            received_date = email[0]
            subject = email[1]
            formatted_results.append(f"• {received_date}: {subject}")

        # Create the message
        message = f"📬 Unreplied Zillow Emails 📬\n\nYou have {len(unreplied_emails)} unreplied Zillow emails:\n\n"
        message += "\n".join(formatted_results)
        message += "\n\nPlease check your email and reply to these messages."

        # Send the message via OpenPhone
        response = send_message(
            message=message,
            to_phone_number=target_phone_number,
            from_phone_number="+14129101500",
        )

        if response.status_code not in [200, 202]:
            logger.error(f"Failed to send OpenPhone message: {response.text}")
            return JSONResponse(
                status_code=500,
                content={
                    "message": f"Failed to send OpenPhone message: {response.text}"
                },
            )

        logger.info(
            f"Successfully sent summary of {len(unreplied_emails)} unreplied emails to {target_phone_number}"
        )
        return {
            "message": f"Successfully sent summary of {len(unreplied_emails)} unreplied emails",
            "unreplied_count": len(unreplied_emails),
            "target_phone_number": target_phone_number,
        }

    except Exception as e:
        logger.error(f"Error checking unreplied emails: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"message": f"Error checking unreplied emails: {str(e)}"},
        )
