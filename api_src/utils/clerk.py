from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(".env.development.local"), override=True)
from typing import Annotated, Optional
from fastapi import Depends, HTTPException, Header, Request, status
from clerk_backend_api import Clerk, Session, AuthenticateRequestOptions, RequestState
import os
from google.oauth2 import id_token
from google.auth.transport import requests
from google.oauth2.credentials import Credentials
from typing import Union
import logging

logger = logging.getLogger(__name__)

# Initialize Clerk client - in production, this would use os.environ

clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
if not clerk_secret_key:
    raise ValueError("CLERK_SECRET_KEY is not set")

clerk_client = Clerk(bearer_auth=clerk_secret_key)

# Documentation: https://github.com/clerk/clerk-sdk-python?tab=readme-ov-file#sdk-installation

async def get_auth_state(
    request: Request
) -> RequestState:
    """
    FastAPI dependency that returns the authenticated state from Clerk.
    """
    auth_state = clerk_client.authenticate_request(
        request,
        AuthenticateRequestOptions()
    )

    return auth_state


async def is_signed_in(
    request: Request
) -> bool:
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


async def get_auth_session(
    request: Request
) -> Session:
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
        session_id = auth_state.payload['sid']
        session = clerk_client.sessions.get(session_id=session_id)
    

    return session

async def get_auth_user(
    request: Request
):
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
        user_id = auth_state.payload['sub']
        user = clerk_client.users.get(user_id=user_id)
    
    return user



async def get_google_credentials(
    request: Request
):
    """
    FastAPI dependency that returns the Google credentials for the authenticated user.
    """
    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )
    else:
        user_id = auth_state.payload['sub']
        list_creds = clerk_client.users.get_o_auth_access_token(user_id=user_id, provider="oauth_google")

        if len(list_creds) == 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User has no Google credentials.",
            )
        elif len(list_creds) > 1:
            logger.warning(f"User {user_id} has multiple Google credentials, using the first one.")
        
        creds_resp = list_creds[0]
        creds_dict = creds_resp.model_dump()
        # creds_dict.keys()
        # dict_keys(['object', 'external_account_id', 'provider_user_id', 'token', 'provider', 'public_metadata', 'label', 'scopes', 'expires_at'])
        
        credentials = Credentials(creds_dict['token'])
        return credentials
    