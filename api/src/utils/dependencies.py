from fastapi import Request, HTTPException, Depends
import os
from typing import Annotated
from api.src.utils.password import verify_admin_auth
from api.src.utils.clerk import get_auth_user
import logging

logger = logging.getLogger(__name__)

async def verify_cron_secret(request: Request):
    """
    Dependency to verify the cron job secret token.
    Raises 401 if unauthorized.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or auth_header != f"Bearer {os.environ.get('CRON_SECRET')}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True 

async def verify_cron_or_admin(request: Request):
    """
    Dependency to verify either cron secret or admin authentication.
    Raises 401 if neither auth method succeeds.
    """
    try:
        # Try cron auth first
        await verify_cron_secret(request)
        return True
    except HTTPException:
        try:
            # If cron auth fails, try admin auth
            await verify_admin_auth(request)
            return True
        except HTTPException:
            # If both fail, raise unauthorized
            raise HTTPException(status_code=401, detail="Unauthorized: Requires either cron secret or admin authentication") 

async def verify_serniacapital_user(
    user: Annotated[any, Depends(get_auth_user)]
):
    """
    Dependency to verify if the authenticated user has a VERIFIED @serniacapital.com email.
    Raises 401 if unauthorized.
    """
    is_authorized = False
    verified_sernia_email = None
    for email in user.email_addresses:
        if email.email_address.endswith("@serniacapital.com") and email.verification and email.verification.status == "verified":
            is_authorized = True
            verified_sernia_email = email.email_address
            break # Found a valid email, no need to check further
    
    if not is_authorized:
        logger.warning(f"SerniaCapital check failed for user {user.id}. No verified @serniacapital.com email found.")
        raise HTTPException(status_code=401, detail="Unauthorized: User requires a verified @serniacapital.com email.")
    else:
        logger.info(f"SerniaCapital user check successful for user {user.id} via email {verified_sernia_email}")
        return True

# Combined dependency for OR logic
async def verify_admin_or_serniacapital(
    request: Request,
    user: Annotated[any, Depends(get_auth_user)]
):
    """
    Dependency to verify either admin authentication (password) OR a logged-in user with a verified SerniaCapital email.
    Raises 401/403 if neither auth method succeeds.
    """
    try:
        # Try admin password auth first
        await verify_admin_auth(request)
        logger.info("Authorization successful via admin password.")
        return True # Admin auth successful
    except HTTPException as admin_auth_exception:
        if admin_auth_exception.status_code not in [401, 403]:
             logger.error(f"Admin auth failed with unexpected status: {admin_auth_exception.status_code}")
             raise admin_auth_exception

        logger.info("Admin password auth failed. Trying SerniaCapital user check.")
        try:
            # Check for any verified @serniacapital.com email
            is_sernia_user = False
            verified_email_for_log = "<none found>"
            for email in user.email_addresses:
                if email.email_address.endswith("@serniacapital.com") and email.verification and email.verification.status == "verified":
                    is_sernia_user = True
                    verified_email_for_log = email.email_address
                    break

            if is_sernia_user:
                logger.info(f"Authorization successful via SerniaCapital user {user.id} ({verified_email_for_log})")
                return True # SerniaCapital user is authorized
            else:
                logger.warning(f"SerniaCapital check failed for user {user.id}. No verified @serniacapital.com email found.")
                raise HTTPException(status_code=401, detail="Unauthorized: Requires admin password or a verified @serniacapital.com user login.")

        except HTTPException as user_auth_exception:
            logger.warning(f"SerniaCapital auth failed for user {user.id}: {user_auth_exception.detail}")
            raise HTTPException(status_code=401, detail="Unauthorized: Requires admin password or a verified @serniacapital.com user login.") from user_auth_exception
        except Exception as e:
             logger.error(f"Unexpected error during SerniaCapital check for user {user.id}: {e}", exc_info=True)
             raise HTTPException(status_code=500, detail="Internal server error during authorization check.")

# Removed the old placeholder verify_admin_or_serniacapital_user 