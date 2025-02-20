from typing import Annotated
from fastapi import APIRouter, Depends, Request
from clerk_backend_api import Session, User, RequestState
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from api_src.utils.clerk import is_signed_in, get_auth_session, get_auth_user, get_google_credentials
from api_src.google.gmail import get_gmail_service

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
    
    # Do search for "Zillow" and return first result
    first_result = gmail_service.users().messages().list(userId="me", q="Zillow").execute()['messages'][0]
    first_result_content = gmail_service.users().messages().get(userId="me", id=first_result['id']).execute()
    
    return {
        "message": "Successfully accessed Google API!",
        "first_result_content": first_result_content
    }
