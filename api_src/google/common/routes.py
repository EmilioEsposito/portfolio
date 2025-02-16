"""
Main router for Google API endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from api_src.database.database import get_session
from api_src.google.common.auth import (
    get_oauth_url,
    save_oauth_token,
    get_oauth_token,
    get_oauth_credentials_from_token,
    OAUTH_DEFAULT_SCOPES
)
import os
import json
import secrets
from typing import Optional
from api_src.google.gmail.routes import router as gmail_router
from api_src.google.pubsub.routes import router as pubsub_router
# from api_src.google.sheets.routes import router as sheets_router
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Create main router
router = APIRouter(prefix="/google", tags=["google"])

# Include subrouters
router.include_router(gmail_router)
router.include_router(pubsub_router)
# router.include_router(sheets_router)

class AuthUrlRequest(BaseModel):
    state: str

@router.post("/auth/url")
async def get_auth_url(
    request: Request,
    body: AuthUrlRequest,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Get Google OAuth URL with state parameter"""
    try:
        # Store state in session
        request.session["oauth_state"] = body.state
        
        # Get auth URL with state
        auth_url = get_oauth_url(scopes=OAUTH_DEFAULT_SCOPES, state=body.state)
        
        return {"url": auth_url}
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate auth URL: {str(e)}"
        )

@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Handle OAuth callback"""
    # Verify state
    stored_state = request.session.get("oauth_state")
    if not stored_state:
        logger.error("No stored state found in session")
        raise HTTPException(status_code=400, detail="No stored state found")
        
    if stored_state != state:
        logger.error(f"State mismatch. Expected: {stored_state}, Got: {state}")
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
    # Clear the state from session after verification
    request.session.pop("oauth_state", None)
    
    try:
        # Create flow instance
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
            scopes=OAUTH_DEFAULT_SCOPES,
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        )
        
        # Exchange code for credentials with scope validation disabled
        flow.fetch_token(
            code=code,
            # Don't validate scopes - allow Google to add additional scopes like 'openid'
            include_granted_scopes=True
        )
        credentials = flow.credentials
        
        # Get user info
        import google.oauth2.credentials
        import google.auth.transport.requests
        import requests
        
        # Create authorized session
        authed_session = requests.Session()
        authed_session.headers.update({
            "Authorization": f"Bearer {credentials.token}"
        })
        
        # Get user info from userinfo endpoint
        userinfo_response = authed_session.get("https://www.googleapis.com/oauth2/v2/userinfo")
        if not userinfo_response.ok:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get user info: {userinfo_response.status_code} - {userinfo_response.text}"
            )

        userinfo = userinfo_response.json()
        logger.info(f"Userinfo response: {userinfo}")  # Log the response for debugging

        if "email" not in userinfo:
            raise HTTPException(
                status_code=500,
                detail=f"Email not found in user info. Available fields: {list(userinfo.keys())}"
            )

        user_id = userinfo["email"]
        
        # Save token to database with the actual granted scopes
        await save_oauth_token(
            session,
            user_id,
            {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_type": "Bearer",  # Google OAuth2 always uses Bearer tokens
                "expiry": credentials.expiry.isoformat()
            },
            credentials.scopes  # Use the actual scopes granted by Google
        )
        
        # Store user_id in session
        request.session["user_id"] = user_id
        
        # Redirect to frontend success page
        frontend_url = request.base_url.scheme + "://" + request.base_url.netloc
        return RedirectResponse(f"{frontend_url}/auth/success")
        
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete OAuth flow: {str(e)}"
        )

@router.get("/auth/check")
async def check_auth(
    request: Request,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Check if user is authenticated"""
    user_id = request.session.get("user_id")
    if not user_id:
        return {"authenticated": False}
    
    token = await get_oauth_token(session, user_id)
    if not token:
        return {"authenticated": False}
    
    return {
        "authenticated": True,
        "user_id": user_id,
        "scopes": token.scopes
    }

@router.post("/auth/logout")
async def logout(request: Request) -> dict:
    """Log out user"""
    request.session.pop("user_id", None)
    request.session.pop("oauth_state", None)
    return {"message": "Logged out successfully"}

@router.get("/auth/token")
async def get_token(
    request: Request,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Get access token for authenticated user"""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = await get_oauth_token(session, user_id)
    if not token:
        raise HTTPException(status_code=401, detail="No token found")
    
    if token.is_expired():
        # Get fresh credentials
        credentials = get_oauth_credentials_from_token(token)
        
        # Refresh token
        try:
            credentials.refresh(GoogleRequest())
            
            # Update token in database
            await save_oauth_token(
                session,
                user_id,
                {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token or token.refresh_token,
                    "token_type": "Bearer",  # Google OAuth2 always uses Bearer tokens
                    "expiry": credentials.expiry.isoformat()
                },
                credentials.scopes
            )
            
            return {"access_token": credentials.token}
            
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}", exc_info=True)  # Add logging
            raise HTTPException(
                status_code=401,
                detail=f"Failed to refresh token: {str(e)}"
            )
    
    return {"access_token": token.access_token}
