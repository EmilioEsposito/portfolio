"""
SMS Notification Flow

Prefect flow for sending SMS messages via OpenPhone.
This replicates the APScheduler schedule_sms functionality.
"""

from prefect import flow, task
from typing import Literal
import asyncio

# Import shared utilities from the API app
from apps.api.src.open_phone.service import send_message
from apps.api.src.contact.service import get_contact_by_slug


# Phone number mapping for team members
PHONE_NUMBERS = {
    "EMILIO": "+14123703550",
    "JACKIE": "+14123703505",
    "PEPPINO": "+14126800593",
    "ANNA": "+14124172322",
}


@task(
    name="get-sernia-phone",
    description="Retrieve the Sernia Capital phone number from the database",
    retries=2,
    retry_delay_seconds=5,
    log_prints=True,
)
async def get_sernia_phone() -> str:
    """Get the Sernia phone number from the contacts database."""
    contact = await get_contact_by_slug("sernia")
    print(f"Retrieved Sernia phone: {contact.phone_number}")
    return contact.phone_number


@task(
    name="resolve-phone-number",
    description="Resolve recipient name to phone number",
    log_prints=True,
)
async def resolve_phone_number(
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"]
) -> str:
    """Resolve a recipient name to a phone number."""
    if recipient == "SERNIA":
        return await get_sernia_phone()

    phone = PHONE_NUMBERS.get(recipient)
    if not phone:
        raise ValueError(f"Unknown recipient: {recipient}")

    print(f"Resolved {recipient} to {phone}")
    return phone


@task(
    name="send-sms",
    description="Send an SMS message via OpenPhone",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def send_sms_task(
    message: str,
    to_phone_number: str,
    from_phone_number: str = "+14129101500",
) -> dict:
    """
    Send an SMS message via OpenPhone API.

    Args:
        message: The message content to send
        to_phone_number: The recipient's phone number
        from_phone_number: The sender's phone number (default: Sernia main line)

    Returns:
        dict with response status and details
    """
    print(f"Sending SMS to {to_phone_number}: {message[:50]}...")

    response = await send_message(
        message=message,
        to_phone_number=to_phone_number,
        from_phone_number=from_phone_number,
    )

    result = {
        "status_code": response.status_code,
        "success": response.status_code in (200, 201, 202),
        "to": to_phone_number,
    }

    if result["success"]:
        print(f"SMS sent successfully to {to_phone_number}")
    else:
        print(f"SMS failed with status {response.status_code}: {response.text}")
        result["error"] = response.text

    return result


@flow(
    name="sms-notification",
    description="Send SMS notifications to team members",
    log_prints=True,
)
async def sms_notification_flow(
    message: str,
    recipient: Literal["EMILIO", "JACKIE", "PEPPINO", "ANNA", "SERNIA"],
    from_phone_number: str = "+14129101500",
) -> dict:
    """
    Send an SMS notification to a team member.

    This flow:
    1. Resolves the recipient name to a phone number
    2. Sends the SMS via OpenPhone
    3. Returns the result

    Args:
        message: The message to send
        recipient: Team member name (EMILIO, JACKIE, PEPPINO, ANNA, SERNIA)
        from_phone_number: The sender phone number (default: Sernia main line)

    Returns:
        dict with flow execution results
    """
    print(f"Starting SMS notification flow for {recipient}")

    # Resolve recipient to phone number
    to_phone_number = await resolve_phone_number(recipient)

    # Send the SMS
    result = await send_sms_task(
        message=message,
        to_phone_number=to_phone_number,
        from_phone_number=from_phone_number,
    )

    print(f"SMS notification flow completed: {result}")
    return result


# Convenience function for testing
async def test_sms_flow():
    """Test the SMS notification flow (dry run - prints only)."""
    result = await sms_notification_flow(
        message="Test message from Prefect",
        recipient="EMILIO",
    )
    print(f"Test result: {result}")
    return result


if __name__ == "__main__":
    # Run a test when executed directly
    asyncio.run(test_sms_flow())
