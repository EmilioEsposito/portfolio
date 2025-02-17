from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(".env.development.local"), override=True)
from typing import Annotated, Optional
from fastapi import Depends, HTTPException, Header, Request, status
from clerk_backend_api import Clerk, Session, AuthenticateRequestOptions, RequestState
import os
# Initialize Clerk client - in production, this would use os.environ

clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
if not clerk_secret_key:
    raise ValueError("CLERK_SECRET_KEY is not set")

clerk_client = Clerk(bearer_auth=clerk_secret_key)


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