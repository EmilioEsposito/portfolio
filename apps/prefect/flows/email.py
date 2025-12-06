"""
Email Notification Flow

Prefect flow for sending email notifications via Google Workspace.
This replicates the APScheduler schedule_email functionality.
"""

from prefect import flow, task
from typing import Literal
import asyncio

# Import shared utilities from the API app
from apps.api.src.google.gmail.service import send_email
from apps.api.src.google.common.service_account_auth import get_delegated_credentials


# Email mapping for team members
TEAM_EMAILS = {
    "EMILIO": "emilio@serniacapital.com",
    "JACKIE": "jackie@serniacapital.com",
    "PEPPINO": "peppino@serniacapital.com",
    "ANNA": "anna@serniacapital.com",
    "SERNIA": "all@serniacapital.com",
}


@task(
    name="get-email-credentials",
    description="Get delegated credentials for sending emails",
    retries=2,
    retry_delay_seconds=5,
    log_prints=True,
)
async def get_email_credentials():
    """Get Google Workspace delegated credentials for sending emails."""
    print("Getting delegated credentials for email...")

    # Run synchronous credential fetch in thread pool
    credentials = await asyncio.to_thread(
        get_delegated_credentials,
        user_email="emilio@serniacapital.com",
        scopes=["https://mail.google.com"],
    )

    print("Successfully obtained email credentials")
    return credentials


@task(
    name="resolve-email-address",
    description="Resolve recipient name to email address",
    log_prints=True,
)
def resolve_email_address(
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"]
) -> str:
    """Resolve a recipient name to an email address."""
    email = TEAM_EMAILS.get(recipient)
    if not email:
        raise ValueError(f"Unknown recipient: {recipient}")

    print(f"Resolved {recipient} to {email}")
    return email


@task(
    name="send-email-task",
    description="Send an email via Google Workspace",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def send_email_task(
    to: str,
    subject: str,
    body: str,
    credentials,
) -> dict:
    """
    Send an email via Google Workspace Gmail API.

    Args:
        to: The recipient's email address
        subject: Email subject
        body: Email body text
        credentials: Google API credentials

    Returns:
        dict with result status
    """
    print(f"Sending email to {to}: {subject}")

    try:
        await send_email(
            to=to,
            subject=subject,
            message_text=body,
            credentials=credentials,
        )

        result = {
            "success": True,
            "to": to,
            "subject": subject,
        }
        print(f"Email sent successfully to {to}")

    except Exception as e:
        result = {
            "success": False,
            "to": to,
            "subject": subject,
            "error": str(e),
        }
        print(f"Email failed: {e}")

    return result


@flow(
    name="email-notification",
    description="Send email notifications to team members",
    log_prints=True,
)
async def email_notification_flow(
    subject: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
) -> dict:
    """
    Send an email notification to a team member.

    This flow:
    1. Gets Google Workspace credentials
    2. Resolves the recipient name to an email
    3. Sends the email

    Args:
        subject: Email subject
        body: Email body text
        recipient: Team member name (EMILIO, JACKIE, PEPPINO, ANNA, SERNIA)

    Returns:
        dict with flow execution results
    """
    print(f"Starting email notification flow for {recipient}")

    # Get credentials
    credentials = await get_email_credentials()

    # Resolve recipient to email
    to_email = resolve_email_address(recipient)

    # Send the email
    result = await send_email_task(
        to=to_email,
        subject=subject,
        body=body,
        credentials=credentials,
    )

    print(f"Email notification flow completed: {result}")
    return result


# Convenience function for testing
async def test_email_flow():
    """Test the email notification flow (dry run)."""
    result = await email_notification_flow(
        subject="Test Email from Prefect",
        body="This is a test email sent via Prefect flow.",
        recipient="EMILIO",
    )
    print(f"Test result: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(test_email_flow())
