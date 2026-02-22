from dotenv import load_dotenv, find_dotenv
import asyncio
import time
from typing import Annotated, Dict, Any

load_dotenv(find_dotenv(".env"), override=True)
from fastapi import Depends, HTTPException, Header, Request, status
from clerk_backend_api import Clerk, Session, AuthenticateRequestOptions, RequestState, User
import os
from google.oauth2.credentials import Credentials
import logfire
from api.src.database.database import AsyncSessionFactory
from api.src.oauth.service import get_oauth_credentials, save_oauth_credentials

# Initialize Clerk client
clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
if not clerk_secret_key:
    raise ValueError("CLERK_SECRET_KEY is not set")

clerk_client = Clerk(bearer_auth=clerk_secret_key)

# Documentation: https://github.com/clerk/clerk-sdk-python?tab=readme-ov-file#sdk-installation

# TTL cache for Clerk user lookups — avoids hitting Clerk API on every request.
# Key: user_id, Value: {"user": User, "ts": float}
_user_cache: Dict[str, Dict[str, Any]] = {}
_USER_CACHE_TTL = 300  # 5 minutes


def _get_cached_user(user_id: str) -> User | None:
    entry = _user_cache.get(user_id)
    if entry and (time.time() - entry["ts"]) < _USER_CACHE_TTL:
        return entry["user"]
    return None


def _set_cached_user(user_id: str, user: User) -> None:
    _user_cache[user_id] = {"user": user, "ts": time.time()}


async def get_auth_state(request: Request) -> RequestState:
    """
    FastAPI dependency that returns the authenticated state from Clerk.
    authenticate_request has no async variant, so we run it in a thread
    to avoid blocking the event loop.
    """
    auth_state = await asyncio.to_thread(
        clerk_client.authenticate_request,
        request,
        AuthenticateRequestOptions(),
    )
    return auth_state


async def is_signed_in(request: Request) -> bool:
    """
    FastAPI dependency that checks if the user is signed in.
    """
    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )
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

    session_id = auth_state.payload["sid"]
    session = await clerk_client.sessions.get_async(session_id=session_id)
    return session


async def get_auth_user(request: Request) -> User:
    """
    FastAPI dependency that returns the authenticated user from Clerk.
    Uses a TTL cache to avoid hitting Clerk API on every request.
    """
    auth_state = await get_auth_state(request)

    if not auth_state.is_signed_in:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not signed in.",
        )

    user_id = auth_state.payload["sub"]

    cached = _get_cached_user(user_id)
    if cached:
        return cached

    user = await clerk_client.users.get_async(user_id=user_id)
    if user:
        _set_cached_user(user_id, user)
    return user


# Type alias for dependency injection - use this in route handlers:
#   async def my_route(user: AuthUser):
AuthUser = Annotated[User, Depends(get_auth_user)]

async def verify_domain(request: Request, domain: str) -> User:
    """
    FastAPI dependency that verifies if the user has a verified email from a specific domain.

    Args:
        request: FastAPI request object
        domain: Domain to verify (e.g. "@serniacapital.com")

    Returns:
        The authenticated User if authorized. Raises HTTPException with status 401 if not authorized.
    """
    user: User = await get_auth_user(request)
    for email in user.email_addresses:
        if email.email_address.endswith(domain) and email.verification and email.verification.status == "verified":
            logfire.info(f"User {user.id} successfully verified for domain '{domain}' with email: {email.email_address}.") # Log success
            return user  # Return the user instead of True

    # If the loop completes, it means no verified email for the domain was found.
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"User is not authorized to access this resource. Please use a verified {domain} email.",
    )

async def verify_serniacapital_user(request: Request) -> User:
    """
    FastAPI dependency that verifies if the user has a verified email from @serniacapital.com.
    Returns the authenticated User object.
    """
    return await verify_domain(request, "@serniacapital.com")


# Type alias for Sernia Capital authenticated users - combines auth + domain verification in one call
SerniaUser = Annotated[User, Depends(verify_serniacapital_user)]
    
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
            logfire.warn(
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
