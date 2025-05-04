import requests
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import PushToken

# The endpoint for Expo's Push API
EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"

async def register_token(email: str, token: str, db: AsyncSession):
    """Registers or updates an Expo push token for a given email using SELECT-then-UPDATE/INSERT pattern."""
    logging.info(f"Registering token for email {email}: {token[:15]}...")

    try:
        # Check if token already exists
        stmt_select = select(PushToken).where(PushToken.token == token)
        result = await db.execute(stmt_select)
        existing_token = result.scalar_one_or_none()

        if existing_token:
            # Update existing token's email and timestamp
            if existing_token.email != email:
                logging.info(f"Updating email for existing token {token[:15]}... from {existing_token.email} to {email}")
                existing_token.email = email
            existing_token.updated_at = func.now() # Use func.now() for update
            logging.info(f"Updated existing token entry for {email}")
        else:
            # Create new token entry
            logging.info(f"Creating new token entry for {email}")
            new_token = PushToken(
                email=email,
                token=token
                # created_at and updated_at will use server defaults
            )
            db.add(new_token)

        await db.commit()

    except Exception as e:
        await db.rollback()
        logging.error(f"Error registering push token for {email}: {e}", exc_info=True)
        raise


def send_push_message(token: str, title: str, body: str, data: dict = None):
    """Sends a single push notification using Expo's push service."""
    # (This is the function moved from the example script, with added logging)
    if not token or not token.startswith("ExponentPushToken"):
        logging.warning(f"Attempted to send to invalid token: {token}")
        return False # Indicate failure

    logging.info(f"Sending push to {token[:15]}... Title: {title}")
    message = {
        "to": token,
        "sound": "default",
        "title": title,
        "body": body,
    }
    if data:
        message["data"] = data

    headers = {
        "Accept": "application/json",
        "Accept-encoding": "gzip, deflate",
        "Content-Type": "application/json",
    }

    success = False
    try:
        response = requests.post(EXPO_PUSH_ENDPOINT, headers=headers, data=json.dumps(message), timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        try:
            response_data = response.json()
            logging.debug(f"Expo Push API Response: {response_data}")
            push_response_data = response_data.get("data")

            if isinstance(push_response_data, dict) and push_response_data.get("status") == "ok":
                logging.info(f"Push notification ticket generated successfully for {token[:15]}... ID: {push_response_data.get('id')}")
                success = True
            elif isinstance(push_response_data, list) and len(push_response_data) > 0:
                if push_response_data[0].get("status") == "error":
                    error_details = push_response_data[0].get("details", {})
                    error_code = error_details.get("error")
                    logging.error(f"Error sending push notification to {token[:15]}...: {error_code}")
                    if error_code == "DeviceNotRegistered":
                        logging.warning(f"Token {token[:15]}... reported as unregistered.")
                        # TODO: Optionally remove this token from DB here
                elif push_response_data[0].get("status") == "ok":
                     logging.info(f"Push notification ticket generated successfully for {token[:15]}... (from list)")
                     success = True
                else:
                     logging.warning(f"Could not determine push status for {token[:15]}... from list response.")
            else:
                 logging.warning(f"Could not determine push status for {token[:15]}... from response format: {push_response_data}")
        except json.JSONDecodeError:
            logging.error(f"Error decoding Expo Push API response for {token[:15]}... Status: {response.status_code}, Text: {response.text}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending request to Expo Push API for {token[:15]}...: {e}")
        
    return success # Return True if sending was likely successful

async def send_notification_to_user(email: str, title: str, body: str, data: dict | None, db: AsyncSession):
    """Sends a push notification to all tokens associated with an email."""
    logging.info(f"Attempting to send notification to email: {email}")
    stmt = select(PushToken.token).where(PushToken.email == email)
    result = await db.execute(stmt)
    tokens = result.scalars().all()
    
    if not tokens:
        logging.warning(f"No push tokens found for email: {email}")
        return

    logging.info(f"Found {len(tokens)} token(s) for {email}. Sending notifications...")
    sent_count = 0
    for token in tokens:
        if send_push_message(token=token, title=title, body=body, data=data):
            sent_count += 1
            
    logging.info(f"Finished sending notifications to {email}. Successfully sent: {sent_count}/{len(tokens)}")

# --- Pytest Example --- 
# Note: This requires a live database and a valid token for the specified user.
# You might want to mock the database session and send_push_message in more complex tests.
import pytest
from api.src.database.database import AsyncSessionFactory # Assuming this provides session

@pytest.mark.asyncio
async def test_send_notification_to_user():
    """Basic integration test for sending a notification to an email."""
    test_email = "espo412@gmail.com"
    # Optionally register a known token first for reliability
    # test_token = "ExponentPushToken[...your_token...]";
    # async with AsyncSessionFactory() as session:
    #     await register_token(test_email, test_token, session)
        
    async with AsyncSessionFactory() as session:
        logging.info(f"[TEST] Attempting to send notification to email {test_email}")
        try:
            await send_notification_to_user(
                email=test_email,
                title="Pytest Hello World!",
                body="This is a test notification from pytest.",
                data={"test": True},
                db=session
            )
            # Basic assertion: Check if the function ran without throwing exceptions
            assert True 
        except Exception as e:
            pytest.fail(f"send_notification_to_user failed: {e}")
