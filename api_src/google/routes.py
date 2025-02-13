"""
FastAPI routes for Google API endpoints.
"""

from fastapi import APIRouter, HTTPException, Request, Response, Depends
from typing import Dict, Any, List
import os
import json
import base64
from google.auth import jwt, crypt
# from google.oauth2 import id_token
from google.auth.transport import requests
from api_src.utils.dependencies import verify_cron_or_admin
from pydantic import BaseModel
from typing import Union
import logging
import traceback
import time
import pytest
import asyncio
import requests as http_requests
from api_src.google.gmail import (
    send_email,
    get_oauth_url,
    setup_gmail_watch,
    stop_gmail_watch,
    get_gmail_service,
    get_delegated_credentials
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

router = APIRouter(prefix="/google", tags=["google"])

# Cache for Google's public keys
_GOOGLE_PUBLIC_KEYS = None
_GOOGLE_PUBLIC_KEYS_EXPIRY = 0

def get_google_public_keys():
    """
    Fetches and caches Google's public keys used for JWT verification.
    Keys are cached until their expiry time.
    """
    global _GOOGLE_PUBLIC_KEYS, _GOOGLE_PUBLIC_KEYS_EXPIRY
    
    # Return cached keys if they're still valid
    if _GOOGLE_PUBLIC_KEYS and time.time() < _GOOGLE_PUBLIC_KEYS_EXPIRY:
        return _GOOGLE_PUBLIC_KEYS
    
    # Fetch new keys
    resp = http_requests.get('https://www.googleapis.com/oauth2/v1/certs')
    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch Google public keys: {resp.status_code}")
    
    # Cache the keys and their expiry time
    _GOOGLE_PUBLIC_KEYS = resp.json()
    
    # Get cache expiry from headers (with some buffer time)
    cache_control = resp.headers.get('Cache-Control', '')
    if 'max-age=' in cache_control:
        max_age = int(cache_control.split('max-age=')[1].split(',')[0])
        _GOOGLE_PUBLIC_KEYS_EXPIRY = time.time() + max_age - 60  # 1 minute buffer
    else:
        _GOOGLE_PUBLIC_KEYS_EXPIRY = time.time() + 3600  # 1 hour default
    
    return _GOOGLE_PUBLIC_KEYS


async def verify_pubsub_token(request: Request) -> bool:
    """
    Verifies the Google Pub/Sub authentication token.
    Raises HTTPException if verification fails.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logging.error(f"Invalid auth header: {auth_header}")
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")
    
    try:
        # Log the token for debugging (careful with sensitive data in prod)
        token = auth_header.split("Bearer ")[1]
        logging.info(f"Verifying token: {token[:20]}...")
        
        # The audience in the token is the full URL of our endpoint
        expected_audience = f"https://{request.headers.get('host', '')}/api/google/gmail/notifications"
        logging.info(f"Expected audience: {expected_audience}")
        
        # Get Google's public keys
        certs = get_google_public_keys()
        
        # Verify token signature and claims using jwt.decode
        claims = jwt.decode(token, certs=certs)
        logging.info(f"Token claims: {json.dumps(claims, indent=2)}")
        
        # Verify audience
        token_audience = claims.get('aud')
        if token_audience != expected_audience:
            logging.error(f"Invalid audience. Expected {expected_audience}, got {token_audience}")
            raise HTTPException(status_code=401, detail="Invalid token audience")
        
        # Verify issuer
        if claims.get('iss') != 'https://accounts.google.com':
            logging.error(f"Invalid issuer: {claims.get('iss')}")
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        
        # Verify service account email
        email = claims.get('email', '')
        if not email.endswith('gserviceaccount.com'):
            logging.error(f"Invalid service account email: {email}")
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        
        return True
        
    except ValueError as e:
        logging.error(f"Token validation error: {str(e)}")
        logging.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(status_code=401, detail=f"Invalid token format: {str(e)}")
    except Exception as e:
        logging.error(f"Pub/Sub token verification failed: {str(e)}")
        logging.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")

async def get_email_changes(service, history_id: str, user_id: str = "me"):
    """
    Fetches email changes using the history ID.
    Uses exponential backoff to handle cases where history ID isn't available yet (race condition)
    Returns a list of message IDs that were added.
    """
    max_retries = 5
    wait_time = 2  # Start with 2 seconds

    for attempt in range(max_retries):
        try:
            # List all changes since the last history ID
            results = service.users().history().list(
                userId=user_id,
                startHistoryId=history_id
            ).execute()

            # results = {
            #     "history": [
            #         {
            #             "id": "6521444",
            #             "messages": [
            #                 {"id": "194fd747652006ca", "threadId": "194fd747652006ca"}
            #             ],
            #         },
            #         {
            #             "id": "6521445",
            #             "messages": [
            #                 {"id": "194fd747652006ca", "threadId": "194fd747652006ca"}
            #             ],
            #             "labelsRemoved": [
            #                 {
            #                     "message": {
            #                         "id": "194fd747652006ca",
            #                         "threadId": "194fd747652006ca",
            #                         "labelIds": [
            #                             "IMPORTANT",
            #                             "CATEGORY_PERSONAL",
            #                             "INBOX",
            #                         ],
            #                     },
            #                     "labelIds": ["UNREAD"],
            #                 }
            #             ],
            #         },
            #         {
            #             "id": "6521484",
            #             "messages": [
            #                 {"id": "194fd86cf22d77f5", "threadId": "194fd747652006ca"}
            #             ],
            #         },
            #         {
            #             "id": "6521485",
            #             "messages": [
            #                 {"id": "194fd86cf22d77f5", "threadId": "194fd747652006ca"}
            #             ],
            #             "labelsRemoved": [
            #                 {
            #                     "message": {
            #                         "id": "194fd86cf22d77f5",
            #                         "threadId": "194fd747652006ca",
            #                         "labelIds": [
            #                             "IMPORTANT",
            #                             "CATEGORY_PERSONAL",
            #                             "INBOX",
            #                         ],
            #                     },
            #                     "labelIds": ["UNREAD"],
            #                 }
            #             ],
            #         },
            #         {
            #             "id": "6521486",
            #             "messages": [
            #                 {"id": "194fd86cf22d77f5", "threadId": "194fd747652006ca"}
            #             ],
            #         },
            #         {
            #             "id": "6521520",
            #             "messages": [
            #                 {"id": "194fd86cf22d77f5", "threadId": "194fd747652006ca"}
            #             ],
            #         },
            #     ],
            #     "historyId": "6521544",
            # }

            message_ids = set()

            if 'history' in results:
                for history in results['history']:
                    if 'messages' in history:
                        for msg in history['messages']:
                            message_ids.add(msg['id'])
                    # Look for added messages
                    if 'messagesAdded' in history:
                        for msg in history['messagesAdded']:
                            msg_id = msg['message']['id']
                            message_ids.add(msg_id)

                return list(message_ids)
            else:
                # If no history found, wait and retry
                logging.info(f"No history found for ID {history_id}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(wait_time)
                    wait_time *= 2  # Exponential backoff
                continue

        except Exception as e:
            if "Invalid history ID" in str(e):
                logging.info(f"History ID {history_id} not yet available, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:  # Don't sleep on last attempt
                    await asyncio.sleep(wait_time)
                    wait_time *= 2  # Exponential backoff
                continue
            else:
                logging.error(f"Failed to fetch history: {str(e)}")
                raise

    logging.warning(f"Failed to retrieve history after {max_retries} retries")
    return []  # Return empty list if we couldn't get the history after all retries

async def get_email_content(service, message_id: str, user_id: str = "me"):
    """
    Fetches the content of a specific email message.
    """
    try:
        # Get the email message
        message = service.users().messages().get(
            userId=user_id,
            id=message_id,
            format='full'
        ).execute()
        
        return message
        
    except Exception as e:
        logging.error(f"Failed to fetch message {message_id}: {str(e)}")
        raise

def extract_email_body(message: Dict[str, Any]) -> Dict[str, str]:
    """
    Extracts both plain text and HTML body from a Gmail message.
    
    The body can be in the payload directly or nested in parts (multipart emails).
    We need to recursively check parts to find all content types.
    
    Args:
        message: The full message from Gmail API
        
    Returns:
        Dict with 'text' and 'html' keys containing the respective body content
    """
    def decode_body(data: str) -> str:
        """Helper to decode base64url encoded body"""
        try:
            # Add padding if needed
            padded = data + '=' * (4 - len(data) % 4)
            return base64.urlsafe_b64decode(padded).decode('utf-8')
        except Exception as e:
            logging.error(f"Failed to decode body: {str(e)}")
            return ""
    
    def extract_parts(payload: Dict[str, Any]) -> Dict[str, str]:
        """Recursively extract body parts"""
        body = {'text': '', 'html': ''}
        
        # Check for body in the current payload
        if 'body' in payload and 'data' in payload['body']:
            mime_type = payload.get('mimeType', '')
            if mime_type == 'text/plain':
                body['text'] = decode_body(payload['body']['data'])
            elif mime_type == 'text/html':
                body['html'] = decode_body(payload['body']['data'])
        
        # Check for nested parts
        if 'parts' in payload:
            for part in payload['parts']:
                part_body = extract_parts(part)
                # Combine with any existing content
                if part_body['text']: body['text'] += part_body['text']
                if part_body['html']: body['html'] += part_body['html']
        
        return body
    
    payload = message.get('payload', {})
    return extract_parts(payload)

async def process_gmail_notification(notification_data: dict) -> List[Dict[str, Any]]:
    """
    Process a Gmail notification and fetch the actual email contents.
    
    Args:
        notification_data: The decoded notification data from Pub/Sub
            Example: {
                "emailAddress": "user@example.com",
                "historyId": "12345"
            }
    
    Returns:
        List of processed email messages with their contents
        
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
        
        logging.info(f"Processing notification for {email_address} with history ID: {history_id}")
        
        # Get Gmail service with delegated credentials
        credentials = get_delegated_credentials(
            user_email=email_address,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        service = get_gmail_service(credentials)
        
        # Get message IDs from history
        message_ids = await get_email_changes(service, history_id)
        logging.info(f"Found {len(message_ids)} new messages")
        
        processed_messages = []
        
        # Fetch each message
        for msg_id in message_ids:
            try:
                message = await get_email_content(service, msg_id)
                
                # Extract headers for easier access
                headers = {
                    h['name'].lower(): h['value'] 
                    for h in message.get('payload', {}).get('headers', [])
                }
                
                # Extract body content
                body = extract_email_body(message)
                
                # Create a processed message object
                processed_message = {
                    'id': msg_id,
                    'threadId': message.get('threadId'),
                    'subject': headers.get('subject', 'No Subject'),
                    'from': headers.get('from'),
                    'to': headers.get('to'),
                    'date': headers.get('date'),
                    'body_text': body['text'],
                    'body_html': body['html'],
                    'raw_message': message  # Include full message for custom processing
                }
                
                processed_messages.append(processed_message)
                logging.info(
                    f"Processed email: {processed_message['subject']} (ID: {msg_id})\n"
                    f"Text body preview: {processed_message['body_text'][:100]}..."
                )
                
            except Exception as msg_error:
                logging.error(f"Failed to process message {msg_id}: {str(msg_error)}")
                # Continue processing other messages even if one fails
                continue
        
        return processed_messages
        
    except Exception as e:
        logging.error(f"Failed to process Gmail notification: {str(e)}")
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
        "historyId": 6521441
    }
    
    # Call the function
    processed_messages = await process_gmail_notification(notification_data)


    assert len(processed_messages) > 0
    
    # Log the results
    for msg in processed_messages:
        print(f"Processed email: {msg['subject']} from {msg['from']}")
        assert msg['subject'] is not None

# https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200
@router.post("/gmail/notifications")
async def handle_gmail_notifications(request: Request):
    """
    Receives Gmail push notifications from Google Pub/Sub.
    """
    try:
        # Log request details
        logging.info("=== New Gmail Notification ===")
        logging.info(f"Headers: {dict(request.headers)}")
        
        # Verify the request is from Google Pub/Sub
        logging.info("Verifying Pub/Sub token...")
        await verify_pubsub_token(request)
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
            # Decode base64 message data
            decoded_bytes = base64.b64decode(message_data)
            decoded_json = json.loads(decoded_bytes.decode('utf-8'))
            logging.info(f"Decoded Pub/Sub message: {json.dumps(decoded_json, indent=2)}")
            
            # Process the notification
            processed_messages = await process_gmail_notification(decoded_json)
            
            # Log results
            for msg in processed_messages:
                logging.info(f"Processed email: {msg['subject']} from {msg['from']}")
            
            logging.info(f"✓ Successfully processed {len(processed_messages)} messages")
            
        except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as e:
            logging.error(f"Failed to decode message data: {str(e)}")
            logging.error(f"Full traceback:\n{traceback.format_exc()}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to decode message data: {str(e)}"
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


class OptionalPassword(BaseModel):
    password: Union[str, None] = None


# Cron job route
@router.post("/gmail/watch/stop", dependencies=[Depends(verify_cron_or_admin)])
async def stop_watch(payload: OptionalPassword):
    """
    Stops Gmail push notifications.
    """
    try:
        result = stop_gmail_watch()
        return {"success": result}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop Gmail watch: {str(e)}"
        )

# Cron job route
@router.post("/gmail/watch/start", dependencies=[Depends(verify_cron_or_admin)])
async def start_watch(payload: OptionalPassword):
    """
    Starts Gmail push notifications.
    """
    try:
        result = setup_gmail_watch()
        return {
            "success": True,
            "expiration": result.get('expiration'),
            "historyId": result.get('historyId')
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Gmail watch: {str(e)}"
        )


# Cron job route to refresh Gmail watch
@router.post("/gmail/watch/refresh", dependencies=[Depends(verify_cron_or_admin)])
async def refresh_watch(payload: OptionalPassword):
    """
    Refreshes Gmail push notifications idempotently. Stops any existing watch and starts a new one.
    If no watch exists, just starts a new one.
    """
    try:
        # Try to stop any existing watch, but don't fail if there isn't one
        try:
            stop_gmail_watch()
            print("✓ Stopped existing watch")
        except Exception as stop_error:
            print(f"Note: Could not stop existing watch: {stop_error}")
        
        # Start a new watch
        result = setup_gmail_watch()
        print(f"✓ Started new watch (expires: {result.get('expiration')})")
        
        return {
            "success": True,
            "message": "Watch refreshed successfully",
            "expiration": result.get('expiration'),
            "historyId": result.get('historyId')
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh Gmail watch: {str(e)}"
        )
