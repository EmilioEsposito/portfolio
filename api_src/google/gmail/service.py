"""
Gmail service functionality for interacting with Gmail API.
"""

from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
import os
from typing import Optional, Union, Dict, Any, List
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import Flow
import logging
import asyncio
from email.utils import parsedate_to_datetime
from googleapiclient.discovery_cache.base import Cache


from api_src.google.common.service_account_auth import get_service_credentials, get_delegated_credentials
from api_src.oauth.service import get_oauth_credentials


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/pubsub'
]

def get_gmail_service(credentials: Union[Credentials, service_account.Credentials]):
    """
    Creates and returns an authorized Gmail API service instance.
    
    Args:
        credentials: Either service account or OAuth user credentials
    """
    try:
        service = build('gmail', 'v1', credentials=credentials)
        return service
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Gmail service: {str(e)}"
        )

def create_message(sender: str, to: str, subject: str, message_text: str):
    """
    Creates a message for an email.

    Args:
        sender: Email address of the sender.
        to: Email address of the receiver.
        subject: The subject of the email message.
        message_text: The text of the email message.

    Returns:
        An object containing a base64url encoded email object.
    """
    message = MIMEText(message_text)
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    
    # Encode the message
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    return {'raw': encoded_message}

def send_email(
    to: str,
    subject: str,
    message_text: str,
    sender: Optional[str] = None,
    credentials: Optional[Union[Credentials, service_account.Credentials]] = None,
    credentials_json: Optional[dict] = None,
):
    """
    Sends an email using the Gmail API.
    
    Args:
        to: Email address of the receiver
        subject: The subject of the email message
        message_text: The text of the email message
        sender: Optional email address of the sender. If None, uses the authenticated user's email
        credentials: Optional credentials object (service account or OAuth)
        credentials_json: Optional OAuth credentials as dictionary (for backward compatibility)
    """
    try:
        # Determine which credentials to use
        if credentials is None:
            if credentials_json:
                # Legacy support for OAuth credentials
                credentials = get_oauth_credentials(credentials_json)
            else:
                # Default to service account
                credentials = get_service_credentials()
        
        service = get_gmail_service(credentials)
        
        if not sender:
            # Get the authenticated user's email address
            profile = service.users().getProfile(userId='me').execute()
            sender = profile['emailAddress']
        
        # Create the email message
        message = create_message(sender, to, subject, message_text)
        
        # Send the email
        sent_message = service.users().messages().send(
            userId='me',
            body=message
        ).execute()
        
        return sent_message
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {str(e)}"
        )

async def get_email_changes(gmail_service, history_id: str, user_id: str = "me"):
    """
    Fetches email changes using the history ID.
    Uses exponential backoff to handle cases where history ID isn't available yet (race condition)
    
    Returns:
        Dictionary with:
        - status: "success", "no_messages", or "retry_needed"
        - email_message_ids: List of message IDs that were added
        - reason: Explanation string for what happened
    """
    max_retries = 4
    wait_time = 20  # Start with 10 seconds

    logging.info(f"Fetching email changes for history ID: {history_id}")

    for attempt in range(max_retries):
        try:
            # List all changes since the last history ID
            results = gmail_service.users().history().list(
                userId=user_id,
                startHistoryId=history_id,
                labelId="INBOX"  # Only check INBOX history (this is all we watch anyway)
            ).execute()


            email_message_ids = set()

            if 'history' in results:
                for history in results['history']:
                    if 'messages' in history:
                        for msg in history['messages']:
                            email_message_ids.add(msg['id'])
                    # Look for added messages
                    if 'messagesAdded' in history:
                        for msg in history['messagesAdded']:
                            msg_id = msg['message']['id']
                            email_message_ids.add(msg_id)

                if email_message_ids:
                    return {
                        "status": "success",
                        "email_message_ids": list(email_message_ids),
                        "reason": f"Found {len(email_message_ids)} new messages"
                    }
                else:
                    return {
                        "status": "no_messages",
                        "email_message_ids": [],
                        "reason": f"History found for ID {history_id}, but no new messages"
                    }
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
                return {
                    "status": "retry_needed",
                    "email_message_ids": [],
                    "reason": f"Exception fetching history: {str(e)}"
                }

    logging.warning(f"Failed to retrieve history after {max_retries} retries")
    return {
        "status": "retry_needed",
        "email_message_ids": [],
        "reason": f"Failed to retrieve history after {max_retries} retries"
    }

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
            'label_ids': headers.get('labelIds', []),
            'subject': headers.get('subject', 'No Subject'),
            'from_address': headers.get('from'),  # Aligned with model's from_address field
            'to_address': headers.get('to'),      # Aligned with model's to_address field
            'date': parsed_date.isoformat(),      # Convert to ISO format for consistency
            'body_text': body['text'],
            'body_html': body['html'],
            'raw_payload': message  # Aligned with model's raw_payload field
        }
        
    except Exception as e:
        logger.error(f"Failed to process message: {str(e)}")
        raise

def stop_gmail_watch(user_id: str = 'me'):
    """
    Stops Gmail API push notifications.
    """
    try:
        credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        service = get_gmail_service(credentials)
        service.users().stop(userId=user_id).execute()
        logger.info("✓ Stopped existing Gmail watch")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to stop Gmail watch: {str(e)}")
        return False

def setup_gmail_watch(user_id: str = 'me', topic_name: str = 'projects/portfolio-450200/topics/gmail-notifications'):
    """
    Sets up Gmail API push notifications to a Pub/Sub topic.
    
    Args:
        user_id: The user's email address or 'me' for authenticated user
        topic_name: The full resource name of the Pub/Sub topic
        
    Returns:
        The watch response from Gmail API
    """
    try:
        # Stop any existing watch first
        stop_gmail_watch(user_id)
        
        # Get delegated credentials with necessary scopes
        credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",
            scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/pubsub"
            ]
        )
        logger.info(f"✓ Using service account: portfolio-app-service-account@portfolio-450200.iam.gserviceaccount.com")
        
        # Create Gmail service
        service = get_gmail_service(credentials)
        logger.info("✓ Created Gmail service")
        
        # Set up the watch request
        request = {
            'labelIds': ['INBOX'],
            'topicName': topic_name,
            'labelFilterAction': 'include'
        }
        
        # Start watching the mailbox
        response = service.users().watch(userId=user_id, body=request).execute()
        return response
        
    except Exception as e:
        logger.error(f"\n❌ Watch setup failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set up Gmail watch: {str(e)}"
        ) 