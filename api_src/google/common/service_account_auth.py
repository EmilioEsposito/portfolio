"""
Shared authentication utilities for Google APIs.
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env.development.local'))
from google.oauth2 import service_account
import os
import json
import base64
from typing import List, Optional
from fastapi import HTTPException


# Update these scopes as you add more Google services
SERVICE_ACCOUNT_DEFAULT_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',  # Full access to Sheets
    'https://www.googleapis.com/auth/drive',         # Full access to Drive
    'https://www.googleapis.com/auth/gmail.send',    # Send-only access to Gmail
    'https://www.googleapis.com/auth/calendar',      # Full access to Calendar
]


def get_service_credentials(scopes: Optional[List[str]] = None) -> service_account.Credentials:
    """
    Get credentials for service account authentication.
    This can be used across multiple Google services.
    
    Args:
        scopes: Optional list of scopes. If None, uses DEFAULT_SCOPES.
    
    Returns:
        Service account credentials that can be used with any Google API.
    """
    try:
        # Get credentials from environment variable
        credentials_b64 = os.getenv('GOOGLE_SERVICE_ACCOUNT_CREDENTIALS')
        if not credentials_b64:
            raise HTTPException(
                status_code=500,
                detail="Missing GOOGLE_SERVICE_ACCOUNT_CREDENTIALS environment variable"
            )
        
        # Parse the JSON string safely
        try:
            # Clean and decode base64
            b64_clean = credentials_b64.strip().strip('"').strip("'")
            # Add padding if needed
            padding = 4 - (len(b64_clean) % 4)
            if padding != 4:
                b64_clean += '=' * padding
            
            # Decode base64 and parse JSON
            json_str = base64.b64decode(b64_clean).decode('utf-8')
            creds_dict = json.loads(json_str)
            
            # Validate required fields
            required_fields = ['type', 'project_id', 'private_key', 'client_email']
            missing_fields = [f for f in required_fields if f not in creds_dict]
            if missing_fields:
                raise HTTPException(
                    status_code=500,
                    detail=f"Missing required fields in credentials: {', '.join(missing_fields)}"
                )
            
            if creds_dict['type'] != 'service_account':
                raise HTTPException(
                    status_code=500,
                    detail="Invalid credentials type. Must be 'service_account'"
                )
            
        except (json.JSONDecodeError, base64.binascii.Error) as e:
            # Add helpful debug info
            raise HTTPException(
                status_code=500,
                detail=f"Invalid credentials format. Error: {str(e)}\nTip: Run 'python api/google/scripts/prepare_credentials.py' to properly format your credentials"
            )
        
        # Create credentials object
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=scopes or SERVICE_ACCOUNT_DEFAULT_SCOPES
        )
        
        return credentials
    
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Google service account credentials: {str(e)}"
        )
    
def test_get_service_credentials():
    creds = get_service_credentials()
    print(creds)

def get_delegated_credentials(
    user_email: str,
    scopes: Optional[List[str]] = None
) -> service_account.Credentials:
    """
    Get service account credentials delegated to act as a specific user.
    Requires domain-wide delegation to be set up in Google Workspace admin.
    
    Args:
        user_email: The email of the user to impersonate
        scopes: Optional list of scopes. If None, uses DEFAULT_SCOPES.
    
    Returns:
        Delegated service account credentials that can be used with any Google API.
    """
    try:
        credentials = get_service_credentials(scopes)
        delegated_credentials = credentials.with_subject(user_email)
        return delegated_credentials
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get delegated credentials for {user_email}: {str(e)}"
        )

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    try:
        # Test service account credentials
        print("Testing service account credentials...")
        credentials = get_service_credentials()
        print("✓ Successfully loaded service account credentials")
        print(f"✓ Service account email: {credentials.service_account_email}")
        print(f"✓ Scopes: {credentials.scopes}")
        
        # Test delegation if email provided
        test_email = os.getenv('TEST_DELEGATION_EMAIL')
        if test_email:
            print(f"\nTesting delegation to {test_email}...")
            delegated = get_delegated_credentials(test_email)
            print(f"✓ Successfully created delegated credentials for {test_email}")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        if isinstance(e, HTTPException):
            print(f"HTTP Status: {e.status_code}")
            print(f"Detail: {e.detail}")
        raise
