from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api_src.database.database import AsyncSessionFactory
from api_src.oauth.models import OAuthCredential
from fastapi import HTTPException
from typing import Optional, List
from clerk_backend_api import ResponseBody
import pytest
from typing import Union
import pytz
async def save_oauth_credentials(
    session: AsyncSession,
    user_id: str,
    provider: str,
    creds_response: Union[ResponseBody, dict] = None,
    creds_dict: Union[dict, None] = None
) -> OAuthCredential:
    """
    Save or update OAuth credentials in database
    
    Args:
        session: SQLAlchemy async session
        user_id: Clerk user ID
        provider: OAuth provider (e.g. 'oauth_google')
        creds_response: Clerk OAuth response,
        creds_dict: dict
        
    Returns:
        Saved OAuthCredential instance
    """
    assert creds_dict or creds_response, "Either creds_dict or creds_response must be provided, but not both"
    try:
        if creds_response:
            # Convert response to dict for storage
            creds_dict = creds_response.model_dump()
        else:
            creds_dict = creds_dict

        # Check for existing credentials
        stmt = select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.provider == provider
        )
        result = await session.execute(stmt)
        creds = result.scalar_one_or_none()

        # expires_at is in ms. e.g. 1740010044175
        # Convert to UTC datetime and then make it timezone-naive for Google
        expires_at_timestamp = creds_dict["expires_at"] / 1000
        expires_at = datetime.fromtimestamp(expires_at_timestamp, tz=pytz.UTC).replace(tzinfo=None)
        
        # Log the conversion for debugging
        print(f"Original expires_at (ms): {creds_dict['expires_at']}")
        print(f"Converted to UTC datetime: {expires_at}")
        print(f"Current UTC time: {datetime.now(pytz.UTC).replace(tzinfo=None)}")

        if creds:
            # Update existing credentials
            creds.access_token = creds_dict["token"]
            creds.provider_user_id = creds_dict["provider_user_id"]
            creds.expires_at = expires_at
            creds.scopes = creds_dict["scopes"]
            creds.label = creds_dict.get("label")
            creds.raw_response = creds_dict
            # Don't update token_type if not provided
            if "token_type" in creds_dict:
                creds.token_type = creds_dict["token_type"]
        else:
            # Create new credentials
            creds = OAuthCredential(
                user_id=user_id,
                provider=provider,
                provider_user_id=creds_dict["provider_user_id"],
                access_token=creds_dict["token"],
                token_type=creds_dict.get("token_type", "Bearer"),  # Default to Bearer if not specified
                expires_at=expires_at,
                scopes=creds_dict["scopes"],
                label=creds_dict.get("label"),
                raw_response=creds_dict
            )
            session.add(creds)

        await session.commit()
        await session.refresh(creds)
        return creds

    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save OAuth credentials: {str(e)}"
        )


@pytest.mark.asyncio
async def test_save_oauth_credentials():
    import pickle

    with open("api_src/tests/sensitive/creds_response.pkl", "rb") as f:
        creds_response = pickle.load(f)

    user_id = "user_2tBC2KuZVNUxuyjkB4SmwrKzQi7"
    provider = "oauth_google"

    session = AsyncSessionFactory()
    await save_oauth_credentials(
        session, user_id, provider, creds_response=creds_response
    )
    await session.close()


async def get_oauth_credentials(
    session: AsyncSession,
    user_id: str,
    provider: str
) -> Optional[OAuthCredential]:
    """
    Get OAuth credentials from database
    
    Args:
        session: SQLAlchemy async session
        user_id: Clerk user ID
        provider: OAuth provider (e.g. 'oauth_google')
        
    Returns:
        OAuthCredential instance if found, None otherwise
    """
    stmt = select(OAuthCredential).where(
        OAuthCredential.user_id == user_id,
        OAuthCredential.provider == provider
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_all_user_credentials(
    session: AsyncSession,
    user_id: str
) -> List[OAuthCredential]:
    """
    Get all OAuth credentials for a user
    
    Args:
        session: SQLAlchemy async session
        user_id: Clerk user ID
        
    Returns:
        List of OAuthCredential instances
    """
    stmt = select(OAuthCredential).where(OAuthCredential.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()

async def delete_oauth_credentials(
    session: AsyncSession,
    user_id: str,
    provider: str
) -> bool:
    """
    Delete OAuth credentials from database
    
    Args:
        session: SQLAlchemy async session
        user_id: Clerk user ID
        provider: OAuth provider (e.g. 'oauth_google')
        
    Returns:
        True if credentials were deleted, False if not found
    """
    try:
        stmt = select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.provider == provider
        )
        result = await session.execute(stmt)
        creds = result.scalar_one_or_none()
        
        if creds:
            await session.delete(creds)
            await session.commit()
            return True
        return False
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete OAuth credentials: {str(e)}"
        ) 
