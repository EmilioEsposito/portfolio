"""
FastAPI routes for Google Pub/Sub webhook endpoints.
"""

from fastapi import APIRouter, HTTPException, Request, Response, Depends
import logging
import json
import traceback
from sqlalchemy.ext.asyncio import AsyncSession

from api_src.google.pubsub.service import verify_pubsub_token, decode_pubsub_message
from api_src.google.gmail.service import process_single_message
from api_src.database.database import get_session
from api_src.google.gmail.db_ops import save_email_message, get_email_by_message_id, get_test_session
from api_src.google.gmail.service import get_gmail_service, get_email_changes, get_email_content
from api_src.google.common.auth import get_delegated_credentials
import pytest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pubsub", tags=["pubsub"])

# https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200
@router.post("/gmail/notifications")
async def handle_gmail_notifications(
    request: Request,
    session: AsyncSession = Depends(get_session)
):
    """
    Receives Gmail push notifications from Google Pub/Sub.
    """
    try:
        # Log request details
        logging.info("=== New Gmail Notification ===")
        logging.info(f"Headers: {dict(request.headers)}")
        
        # Verify the request is from Google Pub/Sub
        logging.info("Verifying Pub/Sub token...")
        expected_audience = f"https://{request.headers.get('host', '')}/api/google/pubsub/gmail/notifications"
        await verify_pubsub_token(request.headers.get("authorization", ""), expected_audience)
        logging.info("✓ Token verified")
        
        # Get the raw request body
        body = await request.body()
        body_str = body.decode()
        logging.info(f"Raw request body: {body_str}")
        
        # Parse the message data
        data = await request.json()
        logging.info(f"Parsed JSON data: {json.dumps(data, indent=2)}")
        
        if 'message' not in data:
            logging.error("No 'message' field in request data")
            raise HTTPException(status_code=400, detail="Missing message field")
            
        # Extract and decode the message data
        message_data = data['message'].get('data', '')
        if not message_data:
            logging.error("No 'data' field in message")
            raise HTTPException(status_code=400, detail="Missing message data")
            
        try:
            # Decode and process the notification
            decoded_json = decode_pubsub_message(message_data)
            
            # Process the notification with the provided session
            processed_messages = await process_gmail_notification(decoded_json, session)
            
            # Log results
            for msg in processed_messages:
                logging.info(f"Processed email: {msg['subject']} from {msg['from_address']}")
            
            logging.info(f"✓ Successfully processed {len(processed_messages)} messages")
            
        except Exception as e:
            logging.error(f"Failed to process notification: {str(e)}")
            logging.error(f"Full traceback:\n{traceback.format_exc()}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process notification: {str(e)}"
            )
        
        # Return 204 to acknowledge receipt
        return Response(status_code=204)
        
    except Exception as e:
        logging.error(f"Unhandled error in Gmail notification handler: {str(e)}")
        logging.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process Gmail notification: {str(e)}"
        )

async def process_gmail_notification(notification_data: dict, session: AsyncSession):
    """
    Process a Gmail notification and store messages in the database.
    
    Args:
        notification_data: The decoded notification data from Pub/Sub
            Example: {
                "emailAddress": "user@example.com",
                "historyId": "12345"
            }
        session: SQLAlchemy async session
    
    Returns:
        List of processed email messages
        
    Raises:
        HTTPException: If processing fails
    """
    try:
        # Extract relevant information
        email_address = notification_data.get('emailAddress')
        history_id = notification_data.get('historyId')
        
        if not email_address or not history_id:
            raise HTTPException(
                status_code=400,
                detail="Missing required fields in notification"
            )
        
        logger.info(f"Processing notification for {email_address} with history ID: {history_id}")
        
        # Get Gmail service with delegated credentials
        credentials = get_delegated_credentials(
            user_email=email_address,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        service = get_gmail_service(credentials)
        
        # Get message IDs from history
        message_ids = await get_email_changes(service, history_id)
        logger.info(f"Found {len(message_ids)} new messages")
        
        processed_messages = []
        
        # Process each message
        for msg_id in message_ids:
            try:
                # Fetch and process message
                message = await get_email_content(service, msg_id)
                processed_msg = await process_single_message(message)

                # Check if message already exists in database
                existing_msg = await get_email_by_message_id(session, msg_id)
                if existing_msg:
                    logger.info(f"Message {msg_id} already exists in database, skipping")
                    # let's count it as processed even though it's already in the database
                    processed_messages.append(processed_msg)
                    continue
                
                # Save to database
                saved_msg = await save_email_message(session, processed_msg)
                if saved_msg:
                    processed_messages.append(processed_msg)
                    logger.info(
                        f"Successfully processed and saved message: "
                        f"{processed_msg['subject']} (ID: {msg_id})"
                    )
                
            except Exception as msg_error:
                logger.error(
                    f"Failed to process message {msg_id}: {str(msg_error)}",
                    exc_info=True
                )
                continue
        
        return processed_messages
        
    except Exception as e:
        logger.error(f"Failed to process Gmail notification: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process Gmail notification: {str(e)}"
        )

@pytest.mark.asyncio
async def test_process_gmail_notification():
    """
    Test function to test the process_gmail_notification function.
    """
    # Create a mock Gmail service
    notification_data = {
        "emailAddress": "emilio@serniacapital.com",
        "historyId": 6531598
    }
    
    # Use the test session for the test
    async with get_test_session() as session:
        # Call the function with the test session
        processed_messages = await process_gmail_notification(notification_data, session)

        assert len(processed_messages) > 0
        
        # Log the results
        for msg in processed_messages:
            print(f"Processed email: {msg['subject']} from {msg['from_address']}")
            assert msg['subject'] is not None