import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime
import os
from api.src.utils.dependencies import verify_cron_or_admin
from sqlalchemy import text
from api.src.database.database import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from api.src.open_phone.client import send_message
from fastapi.responses import JSONResponse


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
    logging.info(
        f"Running check for unreplied emails, target phone: {target_phone_number}"
    )

    try:
        # Initialize default response values
        response_message = "No unreplied emails found"
        sent_message = False
        unreplied_count = 0

        # SQL query to find unreplied emails within past week that were received >4 hours ago
        # Use the calculated absolute path to the SQL file
        with open("api/src/unreplied_emails.sql", "r") as f:
            sql_query = f.read()

        # Execute the query
        result = await session.execute(text(sql_query))
        unreplied_emails = result.fetchall()
        unreplied_count = len(unreplied_emails) # Get count immediately

        # Only proceed with formatting and sending if there are emails
        if unreplied_emails:
            logging.info(f"Found {unreplied_count} unreplied emails.") # Log count here
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

            # Skip OpenPhone message in hosted development environment
            if os.getenv("RAILWAY_ENVIRONMENT_NAME", "local") not in ["production", "local"]:
                response_message = "Skipping OpenPhone message in hosted development environment"
                logging.info(response_message)
            else:
                # Send the message via OpenPhone
                response = await send_message(
                    message=message,
                    to_phone_number=target_phone_number,
                    from_phone_number="+14129101500",
                )

                if response.status_code not in [200, 202]:
                    # Keep early return for critical send failure
                    logging.error(f"Failed to send OpenPhone message: {response.text}")
                    return JSONResponse(
                        status_code=500,
                        content={
                            "message": f"Failed to send OpenPhone message: {response.text}"
                        },
                    )

                # Message sent successfully
                sent_message = True
                response_message = f"Successfully sent summary of {unreplied_count} unreplied emails"
                logging.info(f"{response_message} to {target_phone_number}")
        else:
            # Log if no emails were found (uses default response_message)
            logging.info(response_message)

        # Construct and return the final response (handles both cases)
        return {
            "message": response_message,
            "unreplied_count": unreplied_count,
            "target_phone_number": target_phone_number,
            "sent_message": sent_message,
        }

    except Exception as e:
        logging.error(f"Error checking unreplied emails: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"message": f"Error checking unreplied emails: {str(e)}"},
        )
