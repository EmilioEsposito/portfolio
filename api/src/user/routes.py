import os

import logfire
from dotenv import load_dotenv
from fastapi import APIRouter, Header, HTTPException, Request, Response
from svix.webhooks import Webhook, WebhookVerificationError

from api.src.database.database import DBSession
from api.src.user.service import delete_user, upsert_user

# Load environment variables (especially CLERK_WEBHOOK_SECRET)
load_dotenv()

router = APIRouter()

# Each deployment accepts webhooks from its configured Clerk instance only.
CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET")
webhook = Webhook(CLERK_WEBHOOK_SECRET) if CLERK_WEBHOOK_SECRET else None


@router.post("/user/webhook/clerk", status_code=200)
async def handle_clerk_webhook(
    request: Request,
    db: DBSession,
    svix_id: str | None = Header(None),
    svix_timestamp: str | None = Header(None),
    svix_signature: str | None = Header(None),
):
    """Handles user events signed by the configured Clerk webhook secret."""
    logfire.info("Received Clerk webhook request")

    if webhook is None:
        logfire.error("CLERK_WEBHOOK_SECRET is not configured.")
        # Raise 500 because this is a server configuration issue
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

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

    try:
        event = webhook.verify(payload_str, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Webhook verification failed")
    except Exception:
        logfire.error("Unexpected error during Clerk webhook verification")
        raise HTTPException(status_code=500, detail="Internal server error during verification")

    # The signing secret must belong to the same instance as CLERK_SECRET_KEY.
    # Use the instance type, not the hosting environment (previews can use dev Clerk).
    clerk_key = os.getenv("CLERK_SECRET_KEY", "")
    if clerk_key.startswith("sk_live_"):
        environment = "production"
    elif clerk_key.startswith("sk_test_"):
        environment = "development"
    else:
        logfire.error("CLERK_SECRET_KEY has no recognized instance prefix")
        raise HTTPException(status_code=500, detail="Clerk instance environment not configured")

    # Process the event
    event_type = event.get("type")
    event_data = event.get("data")

    if not event_data:
        logfire.error("Webhook payload missing 'data'")
        raise HTTPException(status_code=400, detail="Invalid webhook payload structure")

    message = "Event received but not processed."  # Default message

    try:
        if event_type == "user.created" or event_type == "user.updated":
            logfire.info(
                f"Processing {event_type} for user: {event_data.get('id')} from env: {environment}"
            )
            # Pass the determined environment to the service function
            message = await upsert_user(db, event_data, environment)
        elif event_type == "user.deleted":
            # Pass the determined environment to the service function
            logfire.info(
                f"Processing {event_type} for user: {event_data.get('id')} from env: {environment}"
            )
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
