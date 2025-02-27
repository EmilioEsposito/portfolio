from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(".env.development.local"), override=True)
from fastapi import Depends, HTTPException, Header, Request, status
from clerk_backend_api import Clerk, Session, AuthenticateRequestOptions, RequestState
import os
from google.oauth2.credentials import Credentials
import logging
from api_src.database.database import AsyncSessionFactory
from api_src.oauth.service import get_oauth_credentials, save_oauth_credentials

logger = logging.getLogger(__name__)

# Initialize Clerk client - in production, this would use os.environ

clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
if not clerk_secret_key:
    raise ValueError("CLERK_SECRET_KEY is not set")

clerk_client = Clerk(bearer_auth=clerk_secret_key)

# Documentation: https://github.com/clerk/clerk-sdk-python?tab=readme-ov-file#sdk-installation


async def get_auth_state(request: Request) -> RequestState:
    """
    FastAPI dependency that returns the authenticated state from Clerk.
    """
    auth_state = clerk_client.authenticate_request(
        request, AuthenticateRequestOptions()
    )

    return auth_state


async def is_signed_in(request: Request) -> bool:
    """
    FastAPI dependency that checks if the user is signed in.
    """
    auth_state = get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )
    else:
        return auth_state.is_signed_in


async def get_auth_session(request: Request) -> Session:
    """
    FastAPI dependency that validates the JWT token and returns the Clerk session.
    Raises 401 if token is invalid or missing.
    """

    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )
    else:
        session_id = auth_state.payload["sid"]
        session = clerk_client.sessions.get(session_id=session_id)

    return session


async def get_auth_user(request: Request):
    """
    FastAPI dependency that returns the authenticated user from Clerk.
    Must be used with get_auth_session.
    """
    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )
    else:
        user_id = auth_state.payload["sub"]
        user = clerk_client.users.get(user_id=user_id)

    return user


async def get_google_credentials(request: Request) -> Credentials:
    """
    FastAPI dependency that returns the Google credentials for the authenticated user.
    Checks database for existing credentials, retrieves from Clerk if not found or expired.
    """
    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )

    user_id = auth_state.payload["sub"]
    provider = "oauth_google"

    # Check database for existing credentials
    async with AsyncSessionFactory() as session:
        db_creds = await get_oauth_credentials(session, user_id, provider)
        
        # If we have valid credentials in the database, use them
        if db_creds and not db_creds.is_expired():
            print(f"Using existing credentials from database, expires at: {db_creds.expires_at}")
            return Credentials(
                token=db_creds.access_token,
                scopes=db_creds.scopes,
                expiry=db_creds.expires_at,
                # Add these fields to enable token refresh
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                refresh_token=db_creds.refresh_token
            )
        
        # Get new credentials from Clerk
        print("Getting fresh credentials from Clerk")
        list_creds_responses = await clerk_client.users.get_o_auth_access_token_async(
            user_id=user_id, provider=provider
        )

        if len(list_creds_responses) == 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User has no Google credentials.",
            )
        elif len(list_creds_responses) > 1:
            logger.warning(
                f"User {user_id} has multiple Google credentials, using the first one."
            )

        for creds_response in list_creds_responses:
            db_creds = await save_oauth_credentials(
                session=session,
                user_id=user_id,
                provider=provider,
                creds_response=creds_response
            )

        # Return last saved Google credentials object # TODO: Return all credentials?
        print(f"Returning fresh credentials from Clerk, expires at: {db_creds.expires_at}")
        return Credentials(
            token=db_creds.access_token,
            scopes=db_creds.scopes,
            expiry=db_creds.expires_at,
            # Add these fields to enable token refresh
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=db_creds.refresh_token
        )
