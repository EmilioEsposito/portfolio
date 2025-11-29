import logfire
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from clerk_backend_api import User # Import Clerk User model

from api.src.database.database import get_session # Assuming get_session provides DB session
from api.src.utils.clerk import get_auth_user # Import your actual user dependency
from . import service
from .models import PushToken # Import model for potential future use

# Define router with prefix and tags
router = APIRouter(prefix="/push", tags=["push"])

@router.post("/register", status_code=201)
async def register_push_token(
    token_body: Annotated[dict, Body(embed=True, example={"token": "ExponentPushToken[...token..."})],
    # Use the get_auth_user dependency to get Clerk user object
    user: Annotated[User, Depends(get_auth_user)], 
    db: Annotated[AsyncSession, Depends(get_session)]
):
    """Registers an Expo push token for the currently authenticated user."""
    token = token_body.get("token")
    if not token or not isinstance(token, str) or not token.startswith("ExponentPushToken"):
        raise HTTPException(status_code=400, detail="Invalid push token provided.")
    
    # Extract primary email from Clerk user object
    primary_email = None
    if user.email_addresses:
        for email_obj in user.email_addresses:
            # Assuming the first verified email is the one to use
            # You might have different logic based on primary email ID if available
            if email_obj.verification and email_obj.verification.status == 'verified':
                 primary_email = email_obj.email_address
                 break 
        # Fallback to the first email if none are verified (adjust as needed)
        if not primary_email:
             primary_email = user.email_addresses[0].email_address
             logfire.warn(f"No verified email found for user {user.id}, using first email: {primary_email}")

    if not primary_email:
         raise HTTPException(status_code=400, detail="Could not determine user email.")

    try:
        # Register using the determined email
        await service.register_token(email=primary_email, token=token, db=db)
        return {"message": "Token registered successfully"}
    except Exception as e:
        # Catch potential errors during registration (e.g., database issues)
        # logger happens in the service layer
        raise HTTPException(status_code=500, detail=f"Failed to register token: {str(e)}")

