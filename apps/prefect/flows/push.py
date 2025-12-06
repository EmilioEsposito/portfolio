"""
Push Notification Flow

Prefect flow for sending push notifications via Expo.
This replicates the APScheduler schedule_push functionality.
"""

from prefect import flow, task
from typing import Literal, Any
import asyncio

# Import shared utilities from the API app
from apps.api.src.push.service import send_push_to_user


# Email mapping for team members (push tokens are looked up by email)
TEAM_EMAILS = {
    "EMILIO": "emilio@serniacapital.com",
    "JACKIE": "jackie@serniacapital.com",
    "PEPPINO": "peppino@serniacapital.com",
    "ANNA": "anna@serniacapital.com",
    "SERNIA": "all@serniacapital.com",
}


@task(
    name="resolve-user-email",
    description="Resolve recipient name to email for push lookup",
    log_prints=True,
)
def resolve_user_email(
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"]
) -> str:
    """Resolve a recipient name to an email address for push token lookup."""
    email = TEAM_EMAILS.get(recipient)
    if not email:
        raise ValueError(f"Unknown recipient: {recipient}")

    print(f"Resolved {recipient} to {email}")
    return email


@task(
    name="send-push-notification",
    description="Send a push notification via Expo",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def send_push_task(
    email: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict:
    """
    Send a push notification via Expo Push API.

    Args:
        email: The user's email (to look up push token)
        title: Notification title
        body: Notification body text
        data: Optional data payload

    Returns:
        dict with result status
    """
    print(f"Sending push notification to {email}: {title}")

    if data is None:
        data = {}

    try:
        await send_push_to_user(
            email=email,
            title=title,
            body=body,
            data=data,
        )

        result = {
            "success": True,
            "email": email,
            "title": title,
        }
        print(f"Push notification sent successfully to {email}")

    except Exception as e:
        result = {
            "success": False,
            "email": email,
            "title": title,
            "error": str(e),
        }
        print(f"Push notification failed: {e}")

    return result


@flow(
    name="push-notification",
    description="Send push notifications to team members",
    log_prints=True,
)
async def push_notification_flow(
    title: str,
    body: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    data: dict[str, Any] | None = None,
) -> dict:
    """
    Send a push notification to a team member.

    This flow:
    1. Resolves the recipient name to an email
    2. Sends the push notification via Expo

    Args:
        title: Notification title
        body: Notification body text
        recipient: Team member name (EMILIO, JACKIE, PEPPINO, ANNA, SERNIA)
        data: Optional data payload

    Returns:
        dict with flow execution results
    """
    print(f"Starting push notification flow for {recipient}")

    # Resolve recipient to email
    email = resolve_user_email(recipient)

    # Send the push notification
    result = await send_push_task(
        email=email,
        title=title,
        body=body,
        data=data or {},
    )

    print(f"Push notification flow completed: {result}")
    return result


# Convenience function for testing
async def test_push_flow():
    """Test the push notification flow (dry run)."""
    result = await push_notification_flow(
        title="Test Push from Prefect",
        body="This is a test push notification sent via Prefect flow.",
        recipient="EMILIO",
        data={"test": True},
    )
    print(f"Test result: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(test_push_flow())
