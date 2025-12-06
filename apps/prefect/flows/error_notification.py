"""
Error Notification Flow

Prefect flow for sending error notifications when flows fail.
This replicates the APScheduler handle_job_error functionality.
"""

import os
from prefect import flow, task
import asyncio

# Import shared utilities from the API app
from apps.api.src.google.gmail.service import send_email
from apps.api.src.google.common.service_account_auth import get_delegated_credentials


@task(
    name="get-error-notification-credentials",
    description="Get credentials for sending error notification emails",
    retries=2,
    retry_delay_seconds=5,
    log_prints=True,
)
async def get_error_credentials():
    """Get Google Workspace delegated credentials for sending error emails."""
    print("Getting delegated credentials for error notification...")

    credentials = await asyncio.to_thread(
        get_delegated_credentials,
        user_email="emilio@serniacapital.com",
        scopes=["https://mail.google.com"],
    )

    print("Successfully obtained credentials")
    return credentials


@task(
    name="send-error-notification-email",
    description="Send an error notification email",
    retries=2,
    retry_delay_seconds=5,
    log_prints=True,
)
async def send_error_email(
    flow_name: str,
    error_message: str,
    traceback: str | None,
    credentials,
) -> dict:
    """
    Send an error notification email.

    Args:
        flow_name: Name of the flow that failed
        error_message: The error message
        traceback: Optional traceback string
        credentials: Google API credentials

    Returns:
        dict with result status
    """
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "unknown environment")
    to_email = os.getenv("ERROR_NOTIFICATION_EMAIL", "espo412@gmail.com")

    subject = f"ALERT: Prefect Flow Error on {environment}"

    message_text = f"""Prefect Flow Error: {flow_name}

Error: {error_message}

Traceback:
{traceback or "No traceback available"}
"""

    print(f"Sending error notification to {to_email}")

    try:
        await send_email(
            to=to_email,
            subject=subject,
            message_text=message_text,
            credentials=credentials,
        )

        result = {
            "success": True,
            "to": to_email,
            "flow_name": flow_name,
        }
        print(f"Error notification sent successfully")

    except Exception as e:
        result = {
            "success": False,
            "to": to_email,
            "flow_name": flow_name,
            "error": str(e),
        }
        print(f"Failed to send error notification: {e}")

    return result


@flow(
    name="error-notification",
    description="Send error notifications when flows fail",
    log_prints=True,
)
async def error_notification_flow(
    flow_name: str,
    error_message: str,
    traceback: str | None = None,
) -> dict:
    """
    Send an error notification for a failed flow.

    This can be called from other flows' error handlers or
    configured as a Prefect automation on flow failure.

    Args:
        flow_name: Name of the flow that failed
        error_message: The error message
        traceback: Optional traceback string

    Returns:
        dict with flow execution results
    """
    print(f"Starting error notification flow for: {flow_name}")

    # Get credentials
    credentials = await get_error_credentials()

    # Send the error email
    result = await send_error_email(
        flow_name=flow_name,
        error_message=error_message,
        traceback=traceback,
        credentials=credentials,
    )

    print(f"Error notification flow completed: {result}")
    return result


# Convenience function for testing
async def test_error_notification():
    """Test the error notification flow."""
    result = await error_notification_flow(
        flow_name="test-flow",
        error_message="This is a test error",
        traceback="Test traceback line 1\nTest traceback line 2",
    )
    print(f"Test result: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(test_error_notification())
