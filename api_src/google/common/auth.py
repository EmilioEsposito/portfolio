"""
Shared authentication utilities for Google APIs.
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env.development.local'))
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import os
import json
import base64
from typing import List, Optional, Union
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api_src.google.common.models import GoogleOAuthToken
from datetime import datetime, timedelta
import asyncio

# Update these scopes as you add more Google services
SERVICE_ACCOUNT_DEFAULT_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',  # Full access to Sheets
    'https://www.googleapis.com/auth/drive',         # Full access to Drive
    'https://www.googleapis.com/auth/gmail.send',    # Send-only access to Gmail
    'https://www.googleapis.com/auth/calendar',      # Full access to Calendar
]

OAUTH_DEFAULT_SCOPES = [
    "openid",                                        # OpenID Connect
    "https://www.googleapis.com/auth/userinfo.email",# Get user's email address
    "https://www.googleapis.com/auth/userinfo.profile", # Get user's basic profile info
    "https://www.googleapis.com/auth/drive.file",    # Access to user-selected Drive files
    "https://www.googleapis.com/auth/drive.readonly", # Read-only access to Drive files
    "https://www.googleapis.com/auth/drive.metadata.readonly", # Read metadata for Drive files
    "https://www.googleapis.com/auth/gmail.readonly",# Read emails
    "https://www.googleapis.com/auth/gmail.labels",  # Read Gmail labels
    "https://www.googleapis.com/auth/gmail.metadata",# Read metadata
    "https://www.googleapis.com/auth/gmail.send"     # Send emails
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

def get_oauth_credentials(credentials_json: dict, scopes: Optional[List[str]] = None) -> Credentials:
    """
    Get credentials from OAuth 2.0 user authentication.
    Use this when you need to act on behalf of a user.
    
    Args:
        credentials_json: The OAuth 2.0 credentials as a dictionary
        scopes: Optional list of scopes. If None, uses scopes from credentials
    
    Returns:
        OAuth user credentials that can be used with any Google API.
    """
    try:
        credentials = Credentials.from_authorized_user_info(
            credentials_json,
            scopes=scopes
        )
        return credentials
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Google OAuth credentials: {str(e)}"
        )

async def save_oauth_token(
    session: AsyncSession,
    user_id: str,
    credentials_json: dict,
    scopes: List[str]
) -> GoogleOAuthToken:
    """
    Save or update OAuth token in database
    
    Args:
        session: SQLAlchemy async session
        user_id: User's email or unique identifier
        credentials_json: OAuth credentials as dictionary
        scopes: List of granted scopes
        
    Returns:
        Saved GoogleOAuthToken instance
    """
    try:
        # Check for existing token
        stmt = select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user_id)
        result = await session.execute(stmt)
        token = result.scalar_one_or_none()
        
        if token:
            # Update existing token, preserving refresh_token if not provided
            token.access_token = credentials_json['token']
            if credentials_json.get('refresh_token'):
                token.refresh_token = credentials_json['refresh_token']
            # Otherwise keep the existing refresh_token
            token.token_type = credentials_json['token_type']
            token.expiry = datetime.fromisoformat(credentials_json['expiry'])
            token.scopes = scopes
        else:
            # Create new token - refresh_token is required for new tokens
            if not credentials_json.get('refresh_token'):
                raise HTTPException(
                    status_code=400,
                    detail="Refresh token is required for new OAuth tokens"
                )
            token = GoogleOAuthToken(
                user_id=user_id,
                access_token=credentials_json['token'],
                refresh_token=credentials_json['refresh_token'],
                token_type=credentials_json['token_type'],
                expiry=datetime.fromisoformat(credentials_json['expiry']),
                scopes=scopes
            )
            session.add(token)
        
        await session.commit()
        await session.refresh(token)
        return token
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save OAuth token: {str(e)}"
        )

async def get_oauth_token(
    session: AsyncSession,
    user_id: str
) -> Optional[GoogleOAuthToken]:
    """
    Get OAuth token from database
    
    Args:
        session: SQLAlchemy async session
        user_id: User's email or unique identifier
        
    Returns:
        GoogleOAuthToken instance if found, None otherwise
    """
    stmt = select(GoogleOAuthToken).where(GoogleOAuthToken.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

def get_oauth_url(scopes: Optional[List[str]] = None, state: Optional[str] = None) -> str:
    """
    Generate OAuth URL with proper scopes and state
    
    Args:
        scopes: Optional list of scopes. If None, uses DEFAULT_SCOPES
        state: Optional state parameter for CSRF protection
        
    Returns:
        OAuth authorization URL
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
            scopes=scopes or OAUTH_DEFAULT_SCOPES,
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        )
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',  # Request a refresh token
            include_granted_scopes='true',
            state=state,
            prompt='consent'  # Force consent screen to get refresh token
        )
        
        return auth_url
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate OAuth URL: {str(e)}"
        )

def get_oauth_credentials_from_token(token: GoogleOAuthToken) -> Credentials:
    """
    Create OAuth credentials from database token
    
    Args:
        token: GoogleOAuthToken instance from database
        
    Returns:
        Google OAuth credentials
    """
    try:
        credentials = Credentials(
            token=token.access_token,
            refresh_token=token.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            scopes=token.scopes
        )
        
        # Set expiry
        credentials.expiry = token.expiry
        
        return credentials
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create credentials from token: {str(e)}"
        )

# Test functions
async def test_oauth_token_crud():
    """Test OAuth token CRUD operations"""
    from api_src.database.database import AsyncSessionFactory
    
    async with AsyncSessionFactory() as session:
        # Test data
        user_id = "test@example.com"
        credentials_json = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "token_type": "Bearer",
            "expiry": (datetime.now() + timedelta(hours=1)).isoformat()
        }
        scopes = OAUTH_DEFAULT_SCOPES
        
        # Test save
        token = await save_oauth_token(session, user_id, credentials_json, scopes)
        assert token.user_id == user_id
        assert token.access_token == credentials_json["token"]
        
        # Test get
        retrieved = await get_oauth_token(session, user_id)
        assert retrieved is not None
        assert retrieved.user_id == user_id
        
        # Test update
        new_credentials = {
            "token": "new_token",
            "token_type": "Bearer",
            "expiry": (datetime.now() + timedelta(hours=1)).isoformat()
        }
        updated = await save_oauth_token(session, user_id, new_credentials, scopes)
        assert updated.access_token == "new_token"
        assert updated.refresh_token == "test_refresh"  # Should keep old refresh token
        
        # Clean up
        await session.delete(token)
        await session.commit()

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

    # Run new OAuth tests
    asyncio.run(test_oauth_token_crud())