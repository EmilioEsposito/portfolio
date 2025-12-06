from fastapi import Request, HTTPException, Depends, status
import os
from typing import Annotated
from apps.api.src.utils.password import verify_admin_auth
from apps.api.src.utils.clerk import get_auth_user
from apps.api.src.utils.clerk import verify_serniacapital_user
import logfire

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


# Combined dependency for OR logic
async def verify_admin_or_serniacapital(
    request: Request
):
    """
    Dependency to verify either admin authentication (password) OR a logged-in user with a verified SerniaCapital email.
    Raises 401/403 if neither auth method succeeds.
    """
    logfire.info("Attempting authorization: admin password (for non-GET) or SerniaCapital user.")

    # 1. Attempt admin authentication if not a GET request
    if request.method == "GET":
        logfire.error(
            "Programming_error: 'verify_admin_or_serniacapital' dependency "
            "was incorrectly used with a GET request for path: %s", 
            request.url.path
        )
        # This indicates a server-side misconfiguration
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error: Endpoint dependency misconfigured for verify_admin_or_serniacapital"
        )
    else:
        try:
            await verify_admin_auth(request)
            logfire.info("Authorization successful via admin password.")
            return True  # Admin auth successful
        except HTTPException as admin_auth_exception:
            # Check for expected admin auth failures (e.g., bad password, no/bad body for POST/PUT)
            # 400 for bad JSON (e.g. from request.json() if body is missing/malformed for password)
            # 401 for invalid password/no password
            if admin_auth_exception.status_code in [400, 401, 403]:
                logfire.info(f"Admin password auth failed or not applicable (status: {admin_auth_exception.status_code}, detail: '{admin_auth_exception.detail}'). Proceeding to SerniaCapital user check.")
                # Fall through to SerniaCapital user check below
            else:
                # Unexpected error during admin auth
                logfire.error(f"Admin auth failed with unexpected status: {admin_auth_exception.status_code}, detail: '{admin_auth_exception.detail}'")
                raise admin_auth_exception # Re-raise unexpected exceptions

    # 2. Attempt SerniaCapital user authentication
    # This part is reached if:
    # - It's a GET request (admin auth was skipped)
    # - Or, it's a non-GET request AND admin auth failed with an expected error (400, 401, 403)
    try:
        # Use the verify_serniacapital_user function from clerk.py
        # This will raise an HTTPException if the user is not a verified SerniaCapital user.
        # Successful verification (including specific email) is now logged within verify_serniacapital_user/verify_domain.
        is_sernia_user = await verify_serniacapital_user(request)

        # if above succeeds, is_sernia_user will always be True.
        logfire.info("Authorization successful via SerniaCapital domain verification.")
        return is_sernia_user # SerniaCapital user is authorized (True)

    except HTTPException as user_auth_exception:
        logfire.warn(f"SerniaCapital auth failed: {user_auth_exception.detail}")
        # Provide a comprehensive error message reflecting the OR logic
        detail_message = "Unauthorized: Requires admin password (for non-GET requests) or a verified @serniacapital.com user login."
        raise HTTPException(status_code=401, detail=detail_message) from user_auth_exception
    except Exception as e:
         logfire.exception(f"Unexpected error during SerniaCapital check: {e}")
         raise HTTPException(status_code=500, detail="Internal server error during authorization check.")
