"""
FastAPI routes for Google Pub/Sub webhook endpoints.
"""

from fastapi import APIRouter, HTTPException, Request, Response
import logfire
import json
import traceback
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.google.pubsub.service import verify_pubsub_token, decode_pubsub_message
from api.src.google.gmail.service import process_single_message
from api.src.database.database import DBSession
from api.src.google.gmail.db_ops import save_email_message, get_email_by_message_id, get_test_session
from api.src.google.gmail.service import get_gmail_service, get_email_changes, get_email_content
from api.src.google.common.service_account_auth import get_delegated_credentials
import pytest


router = APIRouter(prefix="/pubsub", tags=["pubsub"])


# https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200
@router.post("/gmail/notifications")
async def handle_gmail_notifications(
    request: Request,
    session: DBSession
):
    """
    Receives Gmail push notifications from Google Pub/Sub.
    """
    try:
        # Log request details
        logfire.info("=== New Gmail Notification ===")
        logfire.info(f"Headers: {dict(request.headers)}")
        
        # Verify the request is from Google Pub/Sub
        logfire.info("Verifying Pub/Sub token...")
        expected_audience = f"https://{request.headers.get('host', '')}/api/google/pubsub/gmail/notifications"
        await verify_pubsub_token(request.headers.get("authorization", ""), expected_audience)
        logfire.info("✓ Token verified")
        
        # Get the raw request body
        pubsub_body = await request.body()
        pubsub_body_str = pubsub_body.decode()
        logfire.info(f"Raw request body: {pubsub_body_str}")
        
        # Parse the message data
        pubsub_data = await request.json()
        logfire.info(f"Parsed JSON data: {json.dumps(pubsub_data, indent=2)}")
        
        if 'message' not in pubsub_data:
            logfire.error("No 'message' field in request data")
            return Response(status_code=503, content="Missing message field")
            
        # Extract and decode the message data
        pubsub_message_data = pubsub_data['message'].get('data', '')
        if not pubsub_message_data:
            logfire.error("No 'data' field in message")
            return Response(status_code=503, content="Missing message data")
            
        try:
            # Decode and process the notification
            pubsub_decoded_json = decode_pubsub_message(pubsub_message_data)
            
            # Process the notification with the provided session
            processing_result = await process_gmail_notification(pubsub_decoded_json, session)
            
            # Handle response based on processing result
            if processing_result["status"] == "success":
                for email_msg in processing_result["messages"]:
                    logfire.info(f"Processed email: {email_msg['subject']} from {email_msg['from_address']}")
                logfire.info(f"✓ Successfully processed {len(processing_result['messages'])} messages")
                return Response(status_code=204)
            elif processing_result["status"] == "no_messages":
                logfire.info(f"No messages to process: {processing_result['reason']}")
                return Response(status_code=204)
            elif processing_result["status"] == "partial_success_failure":
                logfire.info(f"Partial success: {processing_result['reason']}")
                return Response(status_code=500, content=processing_result["reason"])
            else:  # "retry_needed"
                logfire.warn(f"Retry needed: {processing_result['reason']}")
                return Response(
                    status_code=429,
                    content=processing_result["reason"],
                    headers={
                        "Retry-After": "5",
                        "X-Retry-Reason": "waiting_for_gmail_history"
                    }
                )
            
        except Exception as e:
            logfire.error(f"Failed to process notification: {str(e)}")
            logfire.error(f"Full traceback:\n{traceback.format_exc()}")
            return Response(
                status_code=500,
                content=f"Failed to process notification (unhandled error1): {str(e)}"
            )
        
    except Exception as e:
        logfire.error(f"Unhandled error in Gmail notification handler: {str(e)}")
        logfire.error(f"Full traceback:\n{traceback.format_exc()}")
        return Response(
            status_code=500,
            content=f"Failed to process Gmail notification (unhandled error2): {str(e)}"
        )

async def process_gmail_notification(pubsub_notification_data: dict, session: AsyncSession):
    """
    Process a Gmail notification and store messages in the database.
    
    Args:
        pubsub_notification_data: The decoded notification data from Pub/Sub
            Example: {
                "emailAddress": "user@example.com",
                "historyId": "12345"
            }
        session: SQLAlchemy async session
    
    Returns:
        Dictionary with:
        - status: "success", "no_messages", or "retry_needed"
        - messages: List of processed messages (if any)
        - reason: Explanation string for logger/debugging
    """
    try:
        # Extract relevant information
        email_address = pubsub_notification_data.get('emailAddress')
        history_id = pubsub_notification_data.get('historyId')
        
        if not email_address or not history_id:
            return {
                "status": "retry_needed",
                "messages": [],
                "reason": "Missing required fields in pubsub notification"
            }
        
        logfire.info(f"Processing notification for {email_address} with history ID: {history_id}")
        
        # Get Gmail service with delegated credentials
        credentials = get_delegated_credentials(
            user_email=email_address,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        gmail_service = get_gmail_service(credentials)
        
        # Get message IDs from history with status
        email_changes_result = await get_email_changes(gmail_service, history_id)
        
        # If we got a non-success status, pass it through directly
        if email_changes_result["status"] != "success":
            logfire.info(f"Email changes status: {email_changes_result['status']} - {email_changes_result['reason']}")
            return {
                "status": email_changes_result["status"],
                "messages": [],
                "reason": email_changes_result["reason"]
            }
            
        # We have email messages to process
        email_message_ids = email_changes_result["email_message_ids"]
        logfire.info(f"Found {len(email_message_ids)} new messages to process")
        
        processed_email_messages = []
        failed_email_ids = []
        legitimately_skipped_message_ids = []  # Track messages that were not found (404)
        
        # Process each message
        for email_message_id in email_message_ids:
            try:
                # Fetch and process message
                email_message = await get_email_content(gmail_service, email_message_id)
                
                # Skip if message not found (404) - this is expected in some cases
                if email_message is None:
                    logfire.info(f"Skipping message {email_message_id} as it was not found (may have been deleted)")
                    legitimately_skipped_message_ids.append(email_message_id)
                    continue
                
                processed_email_message = await process_single_message(email_message)
                 
                # Save to database (will update if message exists)
                saved_msg = await save_email_message(session, processed_email_message, history_id)
                if saved_msg:
                    processed_email_messages.append(processed_email_message)
                    logfire.info(
                        f"Successfully processed and saved message: "
                        f"{processed_email_message['subject']} (ID: {email_message_id})"
                    )
                else:
                    failed_email_ids.append(email_message_id)
                    logfire.error(f"Failed to save message {email_message_id}")
                
            except Exception as msg_error:
                failed_email_ids.append(email_message_id)
                logfire.exception(
                    f"Failed to process message {email_message_id}: {str(msg_error)}"
                )
                continue
        
        # Calculate the number of messages we actually attempted to process
        # (excluding skipped messages that were not found)
        attempted_message_count = len(email_message_ids) - len(legitimately_skipped_message_ids)
        
        # Only override the status if we had issues processing messages
        if attempted_message_count == 0:
            # All messages were skipped due to 404s
            return {
                "status": "success", 
                "messages": [],
                "reason": f"All {len(legitimately_skipped_message_ids)} messages were not found (likely deleted)"
            }
        elif len(processed_email_messages) == attempted_message_count:
            # All attempted messages processed successfully
            return {
                "status": "success", 
                "messages": processed_email_messages,
                "reason": f"All {attempted_message_count} messages processed successfully. {len(legitimately_skipped_message_ids)} messages were skipped (not found)."
            }
        elif not processed_email_messages:
            # We found message IDs but couldn't process any - need retry
            return {
                "status": "retry_needed",
                "messages": [],
                "reason": f"Found {attempted_message_count} email messages but processed none. Failed IDs: {failed_email_ids}"
            }
        else:
            # Partial success - some messages processed, some failed
            return {
                "status": "partial_success_failure",
                "messages": processed_email_messages,
                "reason": f"Action required. Check logs for details. Processed {len(processed_email_messages)}/{attempted_message_count} email messages. Failed IDs: {failed_email_ids}. {len(legitimately_skipped_message_ids)} messages were skipped (not found)."
            }
        
    except Exception as e:
        logfire.exception(f"Failed to process Gmail notification: {str(e)}")
        return {
            "status": "retry_needed",
            "messages": [],
            "reason": f"Exception during processing: {str(e)}"
        }

@pytest.mark.asyncio
async def test_process_gmail_notification():
    """
    Test function to test the process_gmail_notification function.
    """
    # Create test notification data
    pubsub_notification_data = {
        "emailAddress": "emilio@serniacapital.com",
        "historyId": 6531598
    }
    
    # Use the test session for the test
    async with get_test_session() as session:
        # Call the function with the test session
        processing_result = await process_gmail_notification(pubsub_notification_data, session)

        assert processing_result['status'] in ["success", "no_messages", "retry_needed"]
        
        # Log the results
        for email_msg in processing_result['messages']:
            print(f"Processed email: {email_msg['subject']} from {email_msg['from_address']}")
            assert email_msg['subject'] is not None