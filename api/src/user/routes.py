import os
import json
import logfire
from fastapi import APIRouter, Request, Depends, HTTPException, Header, Response
from svix.webhooks import Webhook, WebhookVerificationError
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from api.src.database.database import get_session
from api.src.user.service import upsert_user, delete_user

# Load environment variables (especially CLERK_WEBHOOK_SIGNING_SECRET)
load_dotenv()

router = APIRouter()

# Retrieve the secret from environment variables
# Make sure this is set in your .env.development.local or server environment
DEV_CLERK_WEBHOOK_SECRET = os.getenv("DEV_CLERK_WEBHOOK_SECRET")
PROD_CLERK_WEBHOOK_SECRET = os.getenv("PROD_CLERK_WEBHOOK_SECRET")

if not DEV_CLERK_WEBHOOK_SECRET:
    logfire.warn("DEV_CLERK_WEBHOOK_SECRET environment variable not set. Webhook verification will fail.")
    # Depending on your policy, you might want to raise an error here
    raise ValueError("DEV_CLERK_WEBHOOK_SECRET is not set.")

if not PROD_CLERK_WEBHOOK_SECRET:
    logfire.warn("PROD_CLERK_WEBHOOK_SECRET environment variable not set. Webhook verification will fail.")
    # Depending on your policy, you might want to raise an error here
    raise ValueError("PROD_CLERK_WEBHOOK_SECRET is not set.")

# Create Webhook instances for each secret
webhook_dev = Webhook(DEV_CLERK_WEBHOOK_SECRET) if DEV_CLERK_WEBHOOK_SECRET else None
webhook_prod = Webhook(PROD_CLERK_WEBHOOK_SECRET) if PROD_CLERK_WEBHOOK_SECRET else None

@router.post("/user/webhook/clerk", status_code=200)
async def handle_clerk_webhook(
    request: Request,
    svix_id: str | None = Header(None),
    svix_timestamp: str | None = Header(None),
    svix_signature: str | None = Header(None),
    db: AsyncSession = Depends(get_session)
):
    """Handles incoming webhooks from Clerk for user events, trying multiple secrets."""
    logfire.info("Received Clerk webhook request")

    if not webhook_dev or not webhook_prod:
        logfire.error("One or more webhook secrets are not configured.")
        # Raise 500 because this is a server configuration issue
        raise HTTPException(status_code=500, detail="Webhook secret(s) not configured")

    if not svix_id or not svix_timestamp or not svix_signature:
        logfire.error("Missing Svix headers")
        raise HTTPException(status_code=400, detail="Missing Svix headers")

    headers = {
        "svix-id": svix_id,
        "svix-timestamp": svix_timestamp,
        "svix-signature": svix_signature,
    }

    # Get the raw request body
    payload_bytes = await request.body()
    payload_str = payload_bytes.decode("utf-8")

    # Verify the webhook signature - Try dev secret first, then prod
    event = None
    environment = None # Store the environment name ("development" or "production")
    try:
        logfire.debug("Attempting verification with DEV secret.")
        event = webhook_dev.verify(payload_str, headers)
        environment = "development"
        logfire.info(f"Webhook verified successfully with DEV secret. Event type: {event.get('type')}")
    except WebhookVerificationError as e_dev:
        logfire.warn(f"Webhook verification failed with DEV secret: {e_dev}. Trying PROD secret.")
        try:
            event = webhook_prod.verify(payload_str, headers)
            environment = "production"
            logfire.info(f"Webhook verified successfully with PROD secret. Event type: {event.get('type')}")
        except WebhookVerificationError as e_prod:
            logfire.error(f"Webhook verification failed with both DEV and PROD secrets: DEV_Error='{e_dev}', PROD_Error='{e_prod}'")
            raise HTTPException(status_code=400, detail="Webhook verification failed")
        except Exception as e:
            # Catch other errors during PROD verification attempt
            logfire.exception(f"Unexpected error during PROD webhook verification: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during verification")
    except Exception as e:
        # Catch other errors during DEV verification attempt
        logfire.exception(f"Unexpected error during DEV webhook verification: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during verification")

    if event is None or environment is None:
        # This case should technically not be reachable if exceptions are handled correctly, but added for safety.
        logfire.error("Webhook event object or environment is None after verification attempts.")
        raise HTTPException(status_code=500, detail="Internal server error after verification")

    # Process the event
    event_type = event.get("type")
    event_data = event.get("data")

    if not event_data:
        logfire.error("Webhook payload missing 'data'")
        raise HTTPException(status_code=400, detail="Invalid webhook payload structure")

    message = "Event received but not processed." # Default message

    try:
        if event_type == "user.created" or event_type == "user.updated":
            logfire.info(f"Processing {event_type} for user: {event_data.get('id')} from env: {environment}")
            # Pass the determined environment to the service function
            message = await upsert_user(db, event_data, environment)
        elif event_type == "user.deleted":
            # Pass the determined environment to the service function
            logfire.info(f"Processing {event_type} for user: {event_data.get('id')} from env: {environment}")
            message = await delete_user(db, event_data, environment)
        else:
            logfire.info(f"Received unhandled event type: {event_type} from env: {environment}")
            # Return 200 OK even for unhandled events as per webhook best practices
            # to prevent unnecessary retries from Clerk.
            message = f"Unhandled event type '{event_type}' received."
            return Response(status_code=200, content={"message": message})

    except ValueError as ve:
        logfire.error(f"Value error processing webhook: {ve}")
        # Use 400 for bad data from the webhook payload itself
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        # Catch other potential errors during DB operations
        logfire.exception(f"Error processing webhook event {event_type}: {e}")
        # Use 500 for internal server errors (like DB issues)
        raise HTTPException(status_code=500, detail="Internal server error processing event")

    # If processing is successful (or event type ignored), return 200
    logfire.info(f"Webhook processing completed. Result: {message}")
    return Response(status_code=200, content=message)
