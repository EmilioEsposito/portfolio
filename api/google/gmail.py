"""
Utilities for interacting with Gmail API.
"""

from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
import os
from typing import Optional, Union
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import Flow

from api.google.auth import get_service_credentials, get_oauth_credentials, get_oauth_url, get_delegated_credentials

SCOPES = ['https://www.googleapis.com/auth/gmail.send']

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

def get_oauth_url() -> str:
    """
    Generates a URL for OAuth 2.0 authorization.
    """
    try:
        client_config = {
            "web": {
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.getenv("GOOGLE_OAUTH_REDIRECT_URI")]
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        )
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        return auth_url
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate OAuth URL: {str(e)}"
        ) 

def test_send_email():
    """
    Test sending an email using delegated credentials.
    Requires:
    1. Service account with domain-wide delegation enabled
    2. In Google Workspace Admin Console:
       - Go to Security > API Controls > Domain-wide Delegation
       - Add client_id from service account
       - Add scope: https://www.googleapis.com/auth/gmail.send
    """
    try:
        # Get service account credentials
        # service_creds = get_service_credentials()
        # print(f"✓ Got service account: {service_creds.service_account_email}")
        
        # Get delegated credentials
        delegated_credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",
            scopes=["https://www.googleapis.com/auth/gmail.send"]
        )
        print("✓ Created delegated credentials")
        
        # Try to send email
        result = send_email(
            to="espo412@gmail.com",
            subject="Test email",
            message_text="This is a test email",
            credentials=delegated_credentials,
            sender="emilio@serniacapital.com"  # Explicitly set sender
        )
        
        assert result.get('id'), "Email was not sent successfully"
        print(f"✓ Email sent successfully with ID: {result['id']}")
        
    except Exception as e:
        print("\n❌ Test failed")
        if isinstance(e, HTTPException):
            print(f"Error: {e.detail}")
        else:
            print(f"Error: {str(e)}")
        raise