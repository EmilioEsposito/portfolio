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
from api_src.database.database import get_session
from api_src.google.db_ops import save_email_message, get_email_by_message_id
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
from email.utils import parsedate_to_datetime
from sqlalchemy import select, func
from api_src.google.models import EmailMessage
from api_src.google.schema import ZillowEmailResponse
from openai import AsyncOpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)
client = AsyncOpenAI()  # Create async client instance

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
        token_audience = claims.get('aud').split('?')[0] # ignore query params
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
    Process a Gmail notification and store messages in the database.
    
    Args:
        notification_data: The decoded notification data from Pub/Sub
            Example: {
                "emailAddress": "user@example.com",
                "historyId": "12345"
            }
    
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
        
        async with get_session() as session:
            # Process each message
            for msg_id in message_ids:
                try:
                    # Check if message already exists in database
                    existing_msg = await get_email_by_message_id(session, msg_id)
                    if existing_msg:
                        logger.info(f"Message {msg_id} already exists in database, skipping")
                        continue
                    
                    # Fetch and process message
                    message = await get_email_content(service, msg_id)
                    processed_msg = await process_single_message(message)
                    
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

async def process_single_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single Gmail message into our standard format.
    
    Args:
        message: Raw Gmail API message
        
    Returns:
        Processed message data ready for database storage
    """
    try:
        # Extract headers for easier access
        headers = {
            h['name'].lower(): h['value'] 
            for h in message.get('payload', {}).get('headers', [])
        }
        
        # Extract body content
        body = extract_email_body(message)
        
        # Parse the date using email.utils since Gmail uses RFC 2822 format
        date_str = headers.get('date')
        if not date_str:
            logger.error("No date header found in message")
            raise ValueError("No date header found in message")
            
        try:
            parsed_date = parsedate_to_datetime(date_str)
        except Exception as e:
            logger.error(f"Failed to parse date '{date_str}': {e}")
            raise ValueError(f"Could not parse date '{date_str}'") from e
        
        # Create a processed message object
        return {
            'message_id': message['id'],
            'thread_id': message.get('threadId'),
            'subject': headers.get('subject', 'No Subject'),
            'from_address': headers.get('from'),  # Aligned with model's from_address field
            'to_address': headers.get('to'),      # Aligned with model's to_address field
            'date': parsed_date.isoformat(),      # Convert to ISO format for consistency
            'body_text': body['text'],
            'body_html': body['html'],
            'raw_payload': message  # Aligned with model's raw_payload field
        }
        
    except Exception as e:
        logger.error(f"Failed to process message: {str(e)}", exc_info=True)
        raise

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
        print(f"Processed email: {msg['subject']} from {msg['from_address']}")
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
                logging.info(f"Processed email: {msg['subject']} from {msg['from_address']}")
            
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

@router.get("/get_zillow_emails")
async def get_zillow_emails():
    """
    Fetch 10 random email messages containing 'zillow' in the body HTML,
    excluding daily listing emails.
    """
    try:
        async with get_session() as db:
            # Construct the query
            query = (
                select(EmailMessage)
                .where(
                    EmailMessage.body_html.ilike('%zillow%'),
                    EmailMessage.subject.like('%is requesting%'),  # Only inquiries
                    ~EmailMessage.subject.like('Re%')  # is NOT a reply
                    # ~EmailMessage.subject.like('%Daily Listing%'),  # ~ is the NOT operator in SQLAlchemy
                    # ~EmailMessage.subject.like('%Zillow Rentals Invoice%'),  # ~ is the NOT operator in SQLAlchemy
                )
                .order_by(func.random())
                .limit(5)
            )
            
            # Execute the query
            result = await db.execute(query)
            emails = result.scalars().all()
            
            # Format the response to match frontend expectations
            return [
                {
                    "id": str(email.id),
                    "subject": email.subject,
                    "sender": email.from_address,
                    "received_at": email.received_date.isoformat(),
                    "body_html": email.body_html
                }
                for email in emails
            ]
        
    except Exception as e:
        logger.error(f"Error fetching Zillow emails: {str(e)}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch Zillow emails: {str(e)}"
        )

class GenerateResponseRequest(BaseModel):
    """Request model for generating AI responses."""
    email_content: str
    system_instruction: str

@router.post("/generate_email_response")
async def generate_email_response(request: GenerateResponseRequest):
    """Generate an AI response to a Zillow email using the provided system instruction."""
    try:
        # Construct the prompt
        prompt = f"""You are an AI assistant helping to respond to a Zillow rental inquiry email.

System Instruction: {request.system_instruction}

Original Email:
{request.email_content}

Please generate a professional and appropriate response:"""

        # Call OpenAI API using async client
        openai_response = await client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": "You are a professional real estate assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        response_text = openai_response.choices[0].message.content
        return {"response": response_text}
        
    except Exception as e:
        logger.error(f"Error generating email response: {str(e)}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate email response: {str(e)}"
        )
