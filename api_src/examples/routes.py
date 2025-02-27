from typing import Annotated
from fastapi import APIRouter, Depends, Request
from clerk_backend_api import Session, User, RequestState
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from api_src.utils.clerk import is_signed_in, get_auth_session, get_auth_user, get_google_credentials
from api_src.google.gmail import get_gmail_service
from pprint import pprint
import pytz

router = APIRouter(prefix="/examples", tags=["examples"])

# @router.get("/protected")
# async def protected_route(
#     session: Annotated[Session, Depends(get_auth_session)],
#     user: Annotated[User, Depends(get_auth_user)]
# ):
#     """
#     Example protected endpoint that requires authentication.
#     Returns user information from Clerk.
#     """
#     return {
#         "message": "You are authenticated!",
#         "user_id": user.id,
#         "email": user.email_addresses[0].email_address if user.email_addresses else None,
#         "first_name": user.first_name,
#         "last_name": user.last_name,
#         "session_id": session.id
#     }



@router.get("/protected_simple")
async def protected_route_simple(
    dependencies=[Depends(is_signed_in)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """

    return "You are authenticated!"



@router.get("/protected_get_session")
async def protected_route_get_session(
    session: Annotated[Session, Depends(get_auth_session)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """
    return session

@router.get("/protected_get_user")
async def protected_route_get_user(
    user: Annotated[User, Depends(get_auth_user)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """
    data = {
        "user": user,
        "dummy_data": "dummy"
    }
    return data

@router.get("/protected_google")
async def protected_route_google(
    request: Request,
    credentials: Annotated[Credentials, Depends(get_google_credentials)]
):
    """
    Example protected endpoint that uses Google OAuth credentials from Clerk.
    Returns basic user profile info from Google.
    """
    # Create Google People API client
    gmail_service = get_gmail_service(credentials=credentials)
    
    # Enhanced debug info with time comparison
    from datetime import datetime
    current_time = datetime.now(pytz.UTC).replace(tzinfo=None)
    
    debug_info = {
        'scopes': credentials.scopes,
        'expiry': credentials.expiry,
        'current_time_utc': current_time,
        'is_expired': credentials.expired,
        'time_until_expiry': (credentials.expiry - current_time).total_seconds() if credentials.expiry else None,
        'valid': credentials.valid,
        'has_token': bool(credentials.token),
        'has_refresh_token': bool(getattr(credentials, 'refresh_token', None)),
        'has_client_id': bool(getattr(credentials, 'client_id', None)),
        'has_client_secret': bool(getattr(credentials, 'client_secret', None)),
        'has_token_uri': bool(getattr(credentials, 'token_uri', None))
    }
    pprint(f"Credential debug info: {debug_info}")
    

    # Do search for "Zillow" and return first result
    try:
        first_result = gmail_service.users().messages().list(userId="me", q="Zillow").execute()
        if 'messages' in first_result and first_result['messages']:
            first_result = first_result['messages'][0]
            first_result_content = gmail_service.users().messages().get(userId="me", id=first_result['id']).execute()
        else:
            first_result_content = {"error": "No messages found matching the query"}
    except Exception as e:
        return {
            "error": f"Failed to access Gmail API: {str(e)}",
            "credential_info": debug_info
        }
    
    return {
        "message": "Successfully accessed Google API!",
        "first_result_content": first_result_content
    }
