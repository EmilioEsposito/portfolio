"""
Main router for Google API endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from google_auth_oauthlib.flow import Flow
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

# Create main router
router = APIRouter(prefix="/google", tags=["google"])

# Include subrouters
router.include_router(gmail_router)
router.include_router(pubsub_router)
# router.include_router(sheets_router)

@router.get("/auth/url")
async def get_auth_url(
    request: Request,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Get Google OAuth URL"""
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    
    # Store state in session
    request.session["oauth_state"] = state
    
    # Get auth URL with state
    auth_url = get_oauth_url(scopes=OAUTH_DEFAULT_SCOPES, state=state)
    
    return {"url": auth_url}

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
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    
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
        
        # Exchange code for credentials
        flow.fetch_token(code=code)
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
        userinfo = authed_session.get("https://www.googleapis.com/oauth2/v2/userinfo").json()
        user_id = userinfo["email"]
        
        # Save token to database
        await save_oauth_token(
            session,
            user_id,
            {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_type": credentials.token_type,
                "expiry": credentials.expiry.isoformat()
            },
            credentials.scopes
        )
        
        # Store user_id in session
        request.session["user_id"] = user_id
        
        # Redirect to frontend success page
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        return RedirectResponse(f"{frontend_url}/auth/success")
        
    except Exception as e:
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
            credentials.refresh(Request())
            
            # Update token in database
            await save_oauth_token(
                session,
                user_id,
                {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token or token.refresh_token,
                    "token_type": credentials.token_type,
                    "expiry": credentials.expiry.isoformat()
                },
                credentials.scopes
            )
            
            return {"access_token": credentials.token}
            
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Failed to refresh token: {str(e)}"
            )
    
    return {"access_token": token.access_token}
